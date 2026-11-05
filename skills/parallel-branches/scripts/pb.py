#!/usr/bin/env python3
"""parallel-branches: run several git branches side by side.

Each branch gets its own git worktree, its own port and its own detached dev
server. State lives under the repository's git common dir, so every worktree
of the repo sees the same registry.

Subcommands: up, status, down, clean, detect, browser.
Python 3.9+, standard library only.
"""

import argparse
import contextlib
import hashlib
import http.client
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import devbrowser  # noqa: E402
import stacks  # noqa: E402

try:
    import fcntl
except ImportError:  # Windows: run without locking
    fcntl = None

PORT_BASE = 4000
PORT_SPAN = 1000
# Ports other software commonly owns (macOS AirPlay uses 5000 and 7000).
RESERVED_PORTS = {5000, 7000}
STATE_DIRNAME = "parallel-branches"


class PbError(Exception):
    """A problem the user can act on. Printed without a traceback."""


# ---------------------------------------------------------------- git helpers

def git(repo, *args, check=True):
    proc = subprocess.run(
        ["git", "-C", repo] + list(args),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
    )
    if check and proc.returncode != 0:
        raise PbError("git %s failed: %s" % (" ".join(args), proc.stderr.strip()))
    return proc


def repo_info(start):
    """Return (main_worktree_path, state_dir) for the repo containing `start`."""
    top = git(start, "rev-parse", "--show-toplevel", check=False)
    if top.returncode != 0:
        raise PbError("%s is not inside a git repository" % start)
    common = git(start, "rev-parse", "--git-common-dir").stdout.strip()
    common = os.path.abspath(os.path.join(top.stdout.strip(), common))
    worktrees = list_worktrees(start)
    main = worktrees[0]["path"] if worktrees else top.stdout.strip()
    return os.path.realpath(main), os.path.join(common, STATE_DIRNAME)


def list_worktrees(repo):
    out = git(repo, "worktree", "list", "--porcelain").stdout
    items, cur = [], {}
    for line in out.splitlines() + [""]:
        if not line:
            if cur:
                items.append(cur)
            cur = {}
        elif line.startswith("worktree "):
            cur["path"] = os.path.realpath(line[len("worktree "):])
        elif line.startswith("branch refs/heads/"):
            cur["branch"] = line[len("branch refs/heads/"):]
    return items


def ref_exists(repo, ref):
    return git(repo, "show-ref", "--verify", "--quiet", ref, check=False).returncode == 0


def slugify(branch):
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", branch).strip("-.")
    return slug or "branch"


# ------------------------------------------------------------------- registry

@contextlib.contextmanager
def locked(state_dir):
    os.makedirs(state_dir, exist_ok=True)
    handle = open(os.path.join(state_dir, "state.lock"), "w")
    try:
        if fcntl:
            fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        handle.close()


def load_state(state_dir):
    path = os.path.join(state_dir, "state.json")
    try:
        with open(path) as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("branches"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"version": 1, "branches": {}}


def save_state(state_dir, state):
    os.makedirs(state_dir, exist_ok=True)
    path = os.path.join(state_dir, "state.json")
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


# ---------------------------------------------------------------------- ports

def preferred_port(branch):
    digest = hashlib.sha1(branch.encode("utf-8")).hexdigest()
    return PORT_BASE + int(digest[:8], 16) % PORT_SPAN


def port_is_free(port):
    """True if nothing is listening on `port` on loopback or all interfaces."""
    for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            probe = socket.socket(family, socket.SOCK_STREAM)
        except OSError:
            continue  # family unavailable
        probe.settimeout(0.3)
        try:
            if probe.connect_ex((host, port)) == 0:
                return False
        except OSError:
            pass
        finally:
            probe.close()
    for family, host in ((socket.AF_INET, "0.0.0.0"), (socket.AF_INET6, "::")):
        try:
            sock = socket.socket(family, socket.SOCK_STREAM)
        except OSError:
            continue
        if os.name != "nt":
            # Ignore TIME_WAIT leftovers so a restarted branch keeps its port.
            # (On Windows this option lets a second socket steal a live port.)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
        finally:
            sock.close()
    return True


def claimed_ports(state, skip_branch=None):
    """Ports held by other registry entries (running or not), so URLs stay stable."""
    claimed = set()
    for name, entry in state["branches"].items():
        if name == skip_branch:
            continue
        for rec in all_records(entry):
            for key in ("port", "hmr_port"):
                if rec.get(key):
                    claimed.add(rec[key])
    return claimed


def allocate_ports(branch, state, extra_taken=(), key=None):
    """Pick (port, hmr_port): a hashed preferred port, probing upward when taken."""
    taken = claimed_ports(state, skip_branch=branch) | set(extra_taken)
    start = preferred_port(key or branch)
    for offset in range(PORT_SPAN):
        port = PORT_BASE + (start - PORT_BASE + offset) % PORT_SPAN
        hmr = port + 1
        if port in RESERVED_PORTS or hmr in RESERVED_PORTS:
            continue
        if port in taken or hmr in taken:
            continue
        if port_is_free(port) and port_is_free(hmr):
            return port, hmr
    raise PbError("no free port pair found in %d-%d" % (PORT_BASE, PORT_BASE + PORT_SPAN))


# ------------------------------------------------------------------ processes

def process_token(pid):
    """A start-time string that changes if the PID is reused by another process."""
    proc = subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(pid)],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def group_alive(pgid):
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def entry_owns_process(entry):
    """True only if the recorded PID is still the process we started."""
    pid = entry.get("pid")
    token = entry.get("token")
    if not pid or not token or not group_alive(entry.get("pgid", pid)):
        return False
    return process_token(pid) == token


def listener_pgids(port):
    """Process-group ids listening on `port`, or None if lsof is unavailable."""
    if not shutil.which("lsof"):
        return None
    proc = subprocess.run(
        ["lsof", "-nP", "-t", "-iTCP:%d" % port, "-sTCP:LISTEN"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True,
    )
    pgids = set()
    for pid in proc.stdout.split():
        ps = subprocess.run(
            ["ps", "-o", "pgid=", "-p", pid],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True,
        )
        if ps.stdout.strip().isdigit():
            pgids.add(int(ps.stdout.strip()))
    return pgids


def http_probe(port, path="/", timeout=1.0):
    """Return the HTTP status from localhost:`port`, or None if nothing answers."""
    for host in ("127.0.0.1", "::1"):
        try:
            conn = http.client.HTTPConnection(host, port, timeout=timeout)
            conn.request("GET", path)
            status = conn.getresponse().status
            conn.close()
            return status
        except (OSError, http.client.HTTPException):
            continue
    return None


def tcp_probe(port, timeout=1.0):
    """True if something accepts a TCP connection on localhost:`port`."""
    for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            probe = socket.socket(family, socket.SOCK_STREAM)
        except OSError:
            continue
        probe.settimeout(timeout)
        try:
            if probe.connect_ex((host, port)) == 0:
                return True
        except OSError:
            pass
        finally:
            probe.close()
    return False


def all_records(entry):
    """The primary service (the entry itself) followed by the extra services, in start order."""
    return [entry] + list(entry.get("extras") or [])


def branch_running(entry):
    return all(entry_owns_process(rec) for rec in all_records(entry))


def any_running(entry):
    return any(entry_owns_process(rec) for rec in all_records(entry))


def stop_records(entry):
    """Stop every service we started for this branch: primary first, then the others in reverse order."""
    stopped = True
    for rec in [entry] + list(reversed(entry.get("extras") or [])):
        if entry_owns_process(rec):
            stopped = stop_group(rec["pgid"]) and stopped
        rec.update(pid=None, pgid=None, token=None)
    return stopped


def check_health(entry, path="/", verify_owner=True, mode="http"):
    """Return (healthy, detail) for one service record. `mode` is "http" or "tcp"."""
    if not entry_owns_process(entry):
        return False, "process not running"
    if mode == "tcp":
        status = "port open" if tcp_probe(entry["port"]) else None
    else:
        status = http_probe(entry["port"], path)
    if status is None:
        return False, "nothing answering on port %d" % entry["port"]
    if verify_owner:
        owners = listener_pgids(entry["port"])
        if owners is not None and entry["pgid"] not in owners:
            return False, "port %d is answered by another process" % entry["port"]
    return True, ("HTTP %d" % status) if isinstance(status, int) else status


def reap(pid):
    """Collect a finished child process. A zombie leader makes its group look alive, and on macOS
    a group of only zombies refuses signals with EPERM."""
    try:
        os.waitpid(pid, os.WNOHANG)
    except OSError:  # not our child (ChildProcessError is an OSError)
        pass


def stop_group(pgid, grace=8.0):
    """SIGTERM the process group, then SIGKILL whatever is left. True once the group is gone."""
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 3.0)):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return True
        except PermissionError:
            reap(pgid)
            return not group_alive(pgid)
        deadline = time.time() + wait
        while time.time() < deadline:
            reap(pgid)
            if not group_alive(pgid):
                return True
            time.sleep(0.1)
    return not group_alive(pgid)


def tail(path, lines=15):
    try:
        with open(path, errors="replace") as fh:
            return "".join(fh.readlines()[-lines:]).rstrip()
    except OSError:
        return ""


# ------------------------------------------------------------------- worktree

def ensure_worktree(repo, main, branch, base_dir, create):
    """Return (path, created_by_us) for the branch's worktree."""
    for item in list_worktrees(repo):
        if item.get("branch") == branch:
            return item["path"], False
    path = os.path.join(base_dir, slugify(branch))
    if os.path.exists(path) and os.listdir(path):
        raise PbError("%s already exists and is not an empty worktree for %s" % (path, branch))
    os.makedirs(base_dir, exist_ok=True)
    if ref_exists(repo, "refs/heads/" + branch):
        git(repo, "worktree", "add", path, branch)
    elif ref_exists(repo, "refs/remotes/origin/" + branch):
        git(repo, "worktree", "add", "--track", "-b", branch, path, "origin/" + branch)
    elif create:
        git(repo, "worktree", "add", "-b", branch, path)
    else:
        raise PbError("branch '%s' not found locally or on origin (use --create to make it)" % branch)
    return os.path.realpath(path), True


def render_command(template, port, hmr_port):
    return template.replace("{PORT}", str(port)).replace("{HMR_PORT}", str(hmr_port))


def check_branch_name(branch):
    if not branch.strip() or branch.startswith("-"):
        raise PbError("invalid branch name: %r" % branch)


def worktree_path_for(repo, branch):
    for item in list_worktrees(repo):
        if item.get("branch") == branch:
            return item["path"]
    return None


# ------------------------------------------------------------------- commands

def default_worktree_dir(main):
    return main + ".worktrees"


def resolve_settings(args, path, main, fresh=False, cfg=None, from_cli=True):
    """Merge CLI flags, config and stack detection for one service.

    `cfg` is the repository config, or one entry of `services`. `from_cli` is False for services,
    because --cmd and --health-path describe a single-service run.
    """
    if cfg is None:
        cfg = stacks.load_config(path, main)
    stack = stacks.detect_stack(path)
    template = (args.cmd if from_cli else None) or cfg.get("cmd") or (stack or {}).get("cmd")
    if not template:
        raise PbError("could not work out how to start the project in %s; pass --cmd "
                      "(or run: pb.py detect)" % path)
    env = dict((stack or {}).get("env") or {})
    env.update(cfg.get("env") or {})
    in_main = os.path.realpath(path) == os.path.realpath(main) and not fresh
    if args.no_install or cfg.get("install") is False or in_main:
        install = None
    elif isinstance(cfg.get("install"), str):
        install = {"cmd": cfg["install"], "clone_from": None, "note": "install: " + cfg["install"]}
    else:
        install = stacks.plan_install(path, main, stack, fresh=fresh)
    return {"template": template, "env": env, "install": install, "stack": stack,
            "health_path": (args.health_path if from_cli else None) or cfg.get("health_path") or "/"}


def plan_services(args, cfg, branch, state):
    """Return (services, primary_name, ports). A repo without `services` has one unnamed service."""
    try:
        parsed = None if args.cmd else stacks.parse_services(cfg)
    except ValueError as exc:
        raise PbError("bad services in %s: %s" % (stacks.CONFIG_NAME, exc))
    if not parsed:
        return [{"name": None, "cwd": "."}], None, {None: allocate_ports(branch, state)}
    services, primary = parsed
    ports, taken = {}, set()
    for svc in services:
        ports[svc["name"]] = allocate_ports(branch, state, taken, key="%s/%s" % (branch, svc["name"]))
        taken.update(ports[svc["name"]])
    return services, primary, ports


def launch(rec, cmd, cwd, env, log_path, timeout, health_path, mode, verify_owner, save):
    """Start one service detached and wait until it answers. Returns (healthy, detail)."""
    rec["cmd"] = cmd
    with open(log_path, "w") as log:
        proc = subprocess.Popen(
            cmd, shell=True, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
    rec.update(pid=proc.pid, pgid=proc.pid, token=process_token(proc.pid), status="running")
    save()
    deadline = time.time() + timeout
    detail = "timed out after %ds" % timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            detail = "process exited with code %s" % proc.returncode
            break
        healthy, last = check_health(rec, health_path, verify_owner, mode)
        if healthy:
            return True, last
        detail = "timed out after %ds (%s)" % (timeout, last)
        time.sleep(0.4)
    stop_group(proc.pid)
    rec.update(status="failed", pid=None, pgid=None, token=None)
    return False, detail


def up_one(args, state, state_dir, main, base_dir, branch):
    """Start one branch: its worktree and every service. Returns (result_row, failed)."""
    existing = state["branches"].get(branch)
    if existing and branch_running(existing):
        return dict(existing, note="already running"), False
    if existing and any_running(existing):
        stop_records(existing)  # only some services were alive: start over cleanly
    known = worktree_path_for(args.repo, branch)
    created = False
    if args.dry_run:
        path = known or main
    else:
        path, created = ensure_worktree(args.repo, main, branch, base_dir, args.create)
        created = created or bool(existing and existing.get("created_worktree"))
    repo_cfg = stacks.load_config(path, main)
    services, primary, ports = plan_services(args, repo_cfg, branch, state)
    multi = services[0]["name"] is not None
    port_map = ports if multi else None
    prepared = []
    for svc in services:
        svc_dir = os.path.normpath(os.path.join(path, svc.get("cwd", ".")))
        svc_main = os.path.normpath(os.path.join(main, svc.get("cwd", ".")))
        if not os.path.isdir(svc_dir):
            raise PbError("service %r: directory %s does not exist on this branch" % (svc["name"], svc_dir))
        settings = resolve_settings(args, svc_dir, svc_main, fresh=args.dry_run and known is None,
                                    cfg=svc if multi else repo_cfg, from_cli=not multi)
        prepared.append((svc, svc_dir, settings))
    primary_name = primary if multi else None

    if args.dry_run:
        rows = []
        for svc, svc_dir, st in prepared:
            port, hmr = ports[svc["name"]]
            rows.append({"service": svc["name"], "port": port, "hmr_port": hmr,
                         "cmd": stacks.render(st["template"], port, hmr, svc_dir, port_map),
                         "stack": (st["stack"] or {}).get("name"),
                         "confidence": (st["stack"] or {}).get("confidence"),
                         "install": (st["install"] or {}).get("note")})
        main_row = next(r for r in rows if r["service"] == primary_name)
        row = {"branch": branch, "port": main_row["port"], "hmr_port": main_row["hmr_port"],
               "cmd": main_row["cmd"], "stack": main_row["stack"], "confidence": main_row["confidence"],
               "install": main_row["install"],
               "note": "dry run, nothing started" + ("" if known else " (stack detected from the main checkout)")}
        if multi:
            row["services"] = rows
        return row, False

    log_dir = os.path.join(state_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    slug = slugify(branch)
    entry = {"branch": branch, "path": path, "created_worktree": created, "started_at": int(time.time()),
             "status": "failed", "extras": []}
    if multi:
        entry["service_order"] = [svc["name"] for svc, _, _ in prepared]
    recs = {}
    for svc, svc_dir, st in prepared:
        name = svc["name"]
        port, hmr = ports[name]
        suffix = "." + name if name else ""
        base = {"port": port, "hmr_port": hmr, "url": "http://localhost:%d" % port,
                "pid": None, "pgid": None, "token": None, "log": os.path.join(log_dir, slug + suffix + ".log"),
                "health_path": st["health_path"], "health": svc.get("health") or "http"}
        if name == primary_name:
            entry.update(base, service=name, stack=(st["stack"] or {}).get("name"),
                         confidence=(st["stack"] or {}).get("confidence"))
            recs[name] = entry
        else:
            base.update(service=name, path=svc_dir, status="failed")
            entry["extras"].append(base)
            recs[name] = base
    state["branches"][branch] = entry
    save = lambda: save_state(state_dir, state)  # noqa: E731

    if created and path != main:
        entry["copied_env"] = stacks.copy_env_files(main, path, repo_cfg.get("copy") or [])
    own_ports = {p for pair in ports.values() for p in pair}
    risks = stacks.scan_risks(path, own_ports)
    entry["cors_risk"], entry["warnings"] = risks["cors_risk"], list(risks["findings"])
    if multi:
        for caller, callee in stacks.cross_service_refs([svc for svc, _, _ in prepared]):
            entry["warnings"].append({"file": stacks.CONFIG_NAME, "line": 0, "kind": "cross-service",
                                      "url": "%s calls %s at http://localhost:%d" % (caller, callee, ports[callee][0]),
                                      "auth": False})
            entry["cors_risk"] = True

    primary_detail = ""
    for svc, svc_dir, st in prepared:
        name = svc["name"]
        rec = recs[name]
        port, hmr = ports[name]
        where = " (service %s)" % name if multi else ""
        if st["install"]:
            install_log = os.path.join(log_dir, slug + ("." + name if name else "") + ".install.log")
            ok, note = stacks.run_install(st["install"], svc_dir, install_log, args.install_timeout)
            rec["install"] = note
            if not ok:
                stop_records(entry)
                entry["status"] = "failed"
                save()
                return dict(entry, note="FAILED%s: %s" % (where, note)), True
        cmd = stacks.render(st["template"], port, hmr, svc_dir, port_map)
        env = dict(os.environ, PORT=str(port), HMR_PORT=str(hmr), BROWSER="none")
        for key, value in st["env"].items():
            env[key] = stacks.render(str(value), port, hmr, svc_dir, port_map)
        ok, detail = launch(rec, cmd, svc_dir, env, rec["log"], args.timeout, rec["health_path"],
                            rec["health"], not args.no_verify_owner, save)
        if not ok:
            hint = ""
            if (st["stack"] or {}).get("confidence") == "low":
                hint = "\nThe stack was detected with low confidence; the server may ignore PORT. " \
                       "Pass --cmd with the right port flag."
            stop_records(entry)  # also stops the services that already started
            entry["status"] = "failed"
            for extra in entry["extras"]:
                extra["status"] = "failed"
            save()
            return dict(entry, note="FAILED%s: %s\n%s%s" % (where, detail, tail(rec["log"]), hint)), True
        if name == primary_name:
            primary_detail = detail
    entry["status"] = "running"
    for rec in all_records(entry):
        rec["healthy"] = True
    save()
    return dict(entry, note=primary_detail), False


def cmd_up(args):
    main, state_dir = repo_info(args.repo)
    base_dir = args.worktree_dir or default_worktree_dir(main)
    for branch in args.branches:
        check_branch_name(branch)
    results, failed = [], False
    with locked(state_dir):
        state = load_state(state_dir)
        for branch in args.branches:
            row, bad = up_one(args, state, state_dir, main, base_dir, branch)
            failed = failed or bad
            entry = state["branches"].get(branch)
            if args.browser and not bad and not args.dry_run and entry:
                try:
                    row["browser"] = ensure_browser(state_dir, state, entry, args.chrome, "", args.headless_browser)
                except PbError as exc:
                    row["note"] = "%s (browser: %s)" % (row.get("note", ""), exc)
            results.append(row)
            save_state(state_dir, state)
    print_results(results, args.json)
    return 1 if failed else 0


# -------------------------------------------------------------------- browser

def browser_alive(entry):
    info = entry.get("browser")
    return bool(info) and group_alive(info["pgid"]) and process_token(info["pid"]) == info["token"]


def free_cdp_port(state):
    used = {e["browser"]["cdp_port"] for e in state["branches"].values() if e.get("browser")}
    for port in range(devbrowser.CDP_BASE, devbrowser.CDP_BASE + 200):
        if port not in used and port_is_free(port):
            return port
    raise PbError("no free port for the browser debugging endpoint")


def ensure_browser(state_dir, state, entry, chrome_arg, path, headless):
    """Open (or reuse) the dev-mode browser for a running branch. Returns its info dict."""
    if browser_alive(entry):
        return entry["browser"]
    if path and not path.startswith("/"):
        raise PbError("--path must start with /")
    chrome = devbrowser.find_chrome(chrome_arg)
    if not chrome:
        raise PbError("no Chrome or Chromium found; pass --chrome PATH or set PB_CHROME")
    profile = os.path.join(state_dir, "profiles", slugify(entry["branch"]))
    shutil.rmtree(profile, ignore_errors=True)  # always start from an empty profile
    cdp_port = free_cdp_port(state)
    url = entry["url"] + path  # always a localhost URL
    proc = devbrowser.launch(chrome, profile, cdp_port, url, headless)
    if not devbrowser.wait_for_cdp(cdp_port):
        stop_group(proc.pid)
        shutil.rmtree(profile, ignore_errors=True)
        raise PbError("the browser did not start (no DevTools endpoint on port %d)" % cdp_port)
    entry["browser"] = {"pid": proc.pid, "pgid": proc.pid, "token": process_token(proc.pid),
                        "cdp_port": cdp_port, "profile": profile, "url": url}
    return entry["browser"]


def stop_browser(entry):
    info = entry.get("browser")
    if not info:
        return False
    if browser_alive(entry):
        stop_group(info["pgid"])
    profile = info.get("profile", "")
    if os.path.basename(os.path.dirname(profile)) == "profiles":  # only delete our own profile dirs
        shutil.rmtree(profile, ignore_errors=True)
    entry.pop("browser", None)
    return True


def cmd_browser(args):
    _, state_dir = repo_info(args.repo)
    with locked(state_dir):
        state = load_state(state_dir)
        entry = state["branches"].get(args.branch)
        if not entry or not entry_owns_process(entry):
            raise PbError("'%s' is not running; start it first with: pb.py up %s" % (args.branch, args.branch))
        info = ensure_browser(state_dir, state, entry, args.chrome, args.path or "", args.headless)
        save_state(state_dir, state)
    print_browser(args.branch, info, args.json)
    return 0


def print_browser(branch, info, as_json):
    if as_json:
        print(json.dumps(dict(info, branch=branch), indent=2))
        return
    print("%s: dev-mode browser open on %s" % (branch, info["url"]))
    print("  WARNING: web security is OFF in this browser (CORS bypass). Use it only for this local app.")
    print("  DevTools endpoint: http://127.0.0.1:%d  (agents: attach with Playwright --cdp-endpoint)"
          % info["cdp_port"])


def cmd_detect(args):
    main, _ = repo_info(args.repo)
    path = os.path.abspath(args.path) if args.path else main
    stack = stacks.detect_stack(path)
    config = stacks.load_config(path)
    services = None
    try:
        parsed = stacks.parse_services(config)
        if parsed:
            services = {"start_order": [x["name"] for x in parsed[0]], "primary": parsed[1]}
    except ValueError as exc:
        services = {"error": str(exc)}
    print(json.dumps({"path": path, "stack": stack, "config": config, "services": services,
                      "install": stacks.plan_install(path, main, stack, fresh=True)}, indent=2))
    return 0 if stack else 1


def live_status(entry, path="/", mode="http"):
    if entry.get("status") == "failed":
        return "failed", "start failed earlier"
    if not entry_owns_process(entry):
        return "stopped", ""
    healthy, detail = check_health(entry, path, True, mode)
    return ("healthy" if healthy else "unhealthy"), detail


def cmd_status(args):
    _, state_dir = repo_info(args.repo)
    state = load_state(state_dir)
    rows = []
    for branch in sorted(state["branches"]):
        entry = state["branches"][branch]
        status, detail = live_status(entry, entry.get("health_path", "/"), entry.get("health", "http"))
        extras = []
        for rec in entry.get("extras") or []:
            live, why = live_status(rec, rec.get("health_path", "/"), rec.get("health", "http"))
            extras.append(dict(rec, live=live, detail=why))
        rows.append(dict(entry, live=status, detail=detail, browser_alive=browser_alive(entry), extras=extras))
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("No branches registered.")
        return 0
    lines = [("BRANCH", "URL", "STATUS", "PID", "DEV-BROWSER", "PATH")]
    for r in rows:
        label = r["branch"] + (":" + r["service"] if r.get("service") else "")
        lines.append((label, r["url"], r["live"], str(r.get("pid") or "-"),
                      ("cdp:%d" % r["browser"]["cdp_port"]) if r["browser_alive"] else "-", r["path"]))
        for x in r["extras"]:
            lines.append(("  %s:%s" % (r["branch"], x["service"]), x["url"], x["live"], str(x.get("pid") or "-"),
                          "-", x.get("path", r["path"])))
    table(lines)
    return 0


def cmd_down(args):
    _, state_dir = repo_info(args.repo)
    with locked(state_dir):
        state = load_state(state_dir)
        names = sorted(state["branches"]) if args.all else args.branches
        if not names:
            raise PbError("give a branch name or --all")
        for branch in names:
            entry = state["branches"].get(branch)
            if not entry:
                print("%s: not registered" % branch)
                continue
            records = [(branch + (":" + entry["service"] if entry.get("service") else ""), entry)]
            records += [("%s:%s" % (branch, rec["service"]), rec) for rec in reversed(entry.get("extras") or [])]
            stuck = False
            for label, rec in records:
                if not entry_owns_process(rec):
                    if rec.get("pid") and group_alive(rec.get("pgid", rec["pid"])):
                        print("%s: PID %s no longer matches the process we started; not killing it"
                              % (label, rec["pid"]))
                    else:
                        print("%s: already stopped" % label)
                elif stop_group(rec["pgid"]):
                    print("%s: stopped (port %d free)" % (label, rec["port"]))
                else:
                    print("%s: could not stop process group %d" % (label, rec["pgid"]))
                    stuck = True
                    continue
                rec.update(status="stopped", pid=None, pgid=None, token=None)
            if stuck:
                continue
            if stop_browser(entry):
                print("%s: dev-mode browser closed" % branch)
        save_state(state_dir, state)
    return 0


def cmd_clean(args):
    main, state_dir = repo_info(args.repo)
    with locked(state_dir):
        state = load_state(state_dir)
        for branch in sorted(state["branches"]):
            entry = state["branches"][branch]
            if any_running(entry):
                print("%s: still running, skipped" % branch)
                continue
            if not entry.get("created_worktree"):
                print("%s: worktree was not created by parallel-branches, left in place" % branch)
                state["branches"].pop(branch)
                continue
            path = entry["path"]
            if not os.path.isdir(path):
                print("%s: worktree already gone, dropping entry" % branch)
                state["branches"].pop(branch)
                continue
            dirty = git(path, "status", "--porcelain").stdout.strip()
            if dirty and not args.force:
                print("%s: has uncommitted changes, skipped (use --force to remove)\n%s" % (branch, dirty))
                continue
            if not args.yes:
                print("%s: would remove worktree %s (rerun with --yes)" % (branch, path))
                continue
            if args.force:
                git(main, "worktree", "remove", "--force", path)
            else:
                git(main, "worktree", "remove", path)
            print("%s: removed worktree %s" % (branch, path))
            state["branches"].pop(branch)
        save_state(state_dir, state)
    return 0


# --------------------------------------------------------------------- output

def table(rows):
    widths = [max(len(str(r[i])) for r in rows) for i in range(len(rows[0]))]
    for row in rows:
        print("  ".join(str(c).ljust(widths[i]) for i, c in enumerate(row)).rstrip())


def print_results(results, as_json):
    if as_json:
        print(json.dumps(results, indent=2))
        return
    rows = [("BRANCH", "URL", "PORT", "PID", "PATH")]
    for r in results:
        rows.append((r["branch"], r.get("url", "-"), str(r["port"]), str(r.get("pid") or "-"),
                     r.get("path", "-")))
    table(rows)
    for r in results:
        name = r["branch"]
        if r.get("stack"):
            print("%s: stack %s (%s confidence)" % (name, r["stack"], r.get("confidence", "?")))
        if r.get("cmd"):
            print("%s: command %s" % (name, r["cmd"]))
        for x in r.get("services") or []:  # dry run of a multi-service branch
            print("%s: service %s on port %d: %s" % (name, x["service"], x["port"], x["cmd"]))
        for x in r.get("extras") or []:
            print("%s: service %s %s (pid %s)" % (name, x["service"], x["url"], x.get("pid") or "-"))
        if r.get("install"):
            print("%s: %s" % (name, r["install"]))
        if r.get("copied_env"):
            print("%s: copied env files %s" % (name, ", ".join(r["copied_env"])))
        if r.get("note"):
            print("%s: %s" % (name, r["note"]))
        if r.get("log") and r.get("healthy"):
            print("%s: log %s" % (name, r["log"]))
        for w in r.get("warnings") or []:
            suffix = " (auth/redirect: a dev-mode browser will not fix this)" if w.get("auth") else ""
            where = w["file"] + (":%d" % w["line"] if w["line"] else "")
            print("%s: WARNING %s %s [%s]%s" % (name, where, w["url"], w["kind"], suffix))
        if r.get("cors_risk"):
            print("%s: CORS risk found. Open the dev-mode browser: pb.py browser %s" % (name, name))
        if r.get("browser"):
            print("%s: dev-mode browser open, DevTools endpoint http://127.0.0.1:%d (web security OFF)"
                  % (name, r["browser"]["cdp_port"]))


# ------------------------------------------------------------------------ CLI

def build_parser():
    parser = argparse.ArgumentParser(prog="pb.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", default=os.getcwd(), help="path inside the git repo (default: cwd)")
    sub = parser.add_subparsers(dest="command", required=True)

    up = sub.add_parser("up", help="create worktrees and start one dev server per branch")
    up.add_argument("branches", nargs="+")
    up.add_argument("--cmd",
                    help="dev command; {PORT} and {HMR_PORT} are replaced and also exported. "
                         "Default: .parallel-branches.json, then stack detection")
    up.add_argument("--create", action="store_true", help="create the branch from HEAD if it does not exist")
    up.add_argument("--worktree-dir", help="where to put new worktrees (default: <repo>.worktrees)")
    up.add_argument("--timeout", type=int, default=60, help="seconds to wait for the server (default 60)")
    up.add_argument("--health-path", help="URL path used for the health check (default /)")
    up.add_argument("--no-install", action="store_true", help="do not install or copy dependencies")
    up.add_argument("--install-timeout", type=int, default=900, help="seconds allowed for installing (default 900)")
    up.add_argument("--browser", action="store_true",
                    help="also open a dev-mode browser (web security off) on each started branch")
    up.add_argument("--chrome", help="path to Chrome/Chromium for --browser (or set PB_CHROME)")
    up.add_argument("--headless-browser", dest="headless_browser", action="store_true",
                    help="run the --browser instance headless")
    up.add_argument("--no-verify-owner", action="store_true",
                    help="skip the check that our process owns the port (for servers that daemonize)")
    up.add_argument("--dry-run", action="store_true", help="show ports and commands, start nothing")
    up.add_argument("--json", action="store_true")
    up.set_defaults(func=cmd_up)

    det = sub.add_parser("detect", help="show how the project would be started and installed")
    det.add_argument("path", nargs="?", help="directory to inspect (default: main checkout)")
    det.set_defaults(func=cmd_detect)

    br = sub.add_parser("browser", help="open a dev-mode browser (web security OFF, throwaway profile)")
    br.add_argument("branch")
    br.add_argument("--path", help="URL path to open, must start with / (default: /)")
    br.add_argument("--chrome", help="path to Chrome/Chromium (or set PB_CHROME)")
    br.add_argument("--headless", action="store_true")
    br.add_argument("--json", action="store_true")
    br.set_defaults(func=cmd_browser)

    st = sub.add_parser("status", help="show registered branches and their health")
    st.add_argument("--json", action="store_true")
    st.set_defaults(func=cmd_status)

    down = sub.add_parser("down", help="stop the servers this tool started")
    down.add_argument("branches", nargs="*")
    down.add_argument("--all", action="store_true")
    down.set_defaults(func=cmd_down)

    clean = sub.add_parser("clean", help="remove worktrees this tool created (dry run unless --yes)")
    clean.add_argument("--yes", action="store_true", help="actually remove")
    clean.add_argument("--force", action="store_true", help="also remove worktrees with uncommitted changes")
    clean.set_defaults(func=cmd_clean)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.repo = os.path.abspath(args.repo)
    try:
        return args.func(args)
    except PbError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
