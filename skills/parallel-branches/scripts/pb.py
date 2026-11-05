#!/usr/bin/env python3
"""parallel-branches: run several git branches side by side.

Each branch gets its own git worktree, its own port and its own detached dev
server. State lives under the repository's git common dir, so every worktree
of the repo sees the same registry.

Subcommands: up, status, down, clean.
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
        for key in ("port", "hmr_port"):
            if entry.get(key):
                claimed.add(entry[key])
    return claimed


def allocate_ports(branch, state):
    """Pick (port, hmr_port): a hashed preferred port, probing upward when taken."""
    taken = claimed_ports(state, skip_branch=branch)
    start = preferred_port(branch)
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


def check_health(entry, path="/", verify_owner=True):
    """Return (healthy, detail) for one registry entry."""
    if not entry_owns_process(entry):
        return False, "process not running"
    status = http_probe(entry["port"], path)
    if status is None:
        return False, "nothing answering on port %d" % entry["port"]
    if verify_owner:
        owners = listener_pgids(entry["port"])
        if owners is not None and entry["pgid"] not in owners:
            return False, "port %d is answered by another process" % entry["port"]
    return True, "HTTP %d" % status


def stop_group(pgid, grace=8.0):
    """SIGTERM the process group, then SIGKILL whatever is left."""
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 3.0)):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return True
        deadline = time.time() + wait
        while time.time() < deadline:
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


# ------------------------------------------------------------------- commands

def default_worktree_dir(main):
    return main + ".worktrees"


def cmd_up(args):
    main, state_dir = repo_info(args.repo)
    base_dir = args.worktree_dir or default_worktree_dir(main)
    results, failed = [], False
    with locked(state_dir):
        state = load_state(state_dir)
        for branch in args.branches:
            existing = state["branches"].get(branch)
            if existing and entry_owns_process(existing):
                results.append(dict(existing, note="already running"))
                continue
            port, hmr = allocate_ports(branch, state)
            command = render_command(args.cmd, port, hmr)
            if args.dry_run:
                results.append({"branch": branch, "port": port, "hmr_port": hmr,
                                "cmd": command, "note": "dry run, nothing started"})
                continue
            path, created = ensure_worktree(args.repo, main, branch, base_dir, args.create)
            created = created or bool(existing and existing.get("created_worktree"))
            log_dir = os.path.join(state_dir, "logs")
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, slugify(branch) + ".log")
            env = dict(os.environ, PORT=str(port), HMR_PORT=str(hmr))
            with open(log_path, "w") as log:
                proc = subprocess.Popen(
                    command, shell=True, cwd=path, env=env, stdin=subprocess.DEVNULL,
                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                )
            entry = {
                "branch": branch, "path": path, "port": port, "hmr_port": hmr,
                "url": "http://localhost:%d" % port, "cmd": command,
                "pid": proc.pid, "pgid": proc.pid, "token": process_token(proc.pid),
                "log": log_path, "created_worktree": created,
                "started_at": int(time.time()), "status": "running",
            }
            state["branches"][branch] = entry
            save_state(state_dir, state)
            deadline = time.time() + args.timeout
            healthy, detail = False, "timed out after %ds" % args.timeout
            while time.time() < deadline:
                if proc.poll() is not None:
                    healthy, detail = False, "process exited with code %s" % proc.returncode
                    break
                healthy, detail = check_health(entry, args.health_path, not args.no_verify_owner)
                if healthy:
                    break
                time.sleep(0.4)
            if healthy:
                entry["healthy"] = True
                results.append(dict(entry, note=detail))
            else:
                failed = True
                stop_group(proc.pid)
                entry.update(status="failed", pid=None, pgid=None, token=None)
                results.append(dict(entry, note="FAILED: %s\n%s" % (detail, tail(log_path))))
            save_state(state_dir, state)
    print_results(results, args.json)
    return 1 if failed else 0


def live_status(entry, path="/"):
    if entry.get("status") == "failed":
        return "failed", "start failed earlier"
    if not entry_owns_process(entry):
        return "stopped", ""
    healthy, detail = check_health(entry, path)
    return ("healthy" if healthy else "unhealthy"), detail


def cmd_status(args):
    _, state_dir = repo_info(args.repo)
    state = load_state(state_dir)
    rows = []
    for branch in sorted(state["branches"]):
        entry = state["branches"][branch]
        status, detail = live_status(entry)
        rows.append(dict(entry, live=status, detail=detail))
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("No branches registered.")
        return 0
    table([("BRANCH", "URL", "STATUS", "PID", "PATH")] +
          [(r["branch"], r["url"], r["live"], str(r.get("pid") or "-"), r["path"]) for r in rows])
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
            if not entry_owns_process(entry):
                if entry.get("pid") and group_alive(entry.get("pgid", entry["pid"])):
                    print("%s: PID %s no longer matches the process we started; not killing it"
                          % (branch, entry["pid"]))
                else:
                    print("%s: already stopped" % branch)
            elif stop_group(entry["pgid"]):
                print("%s: stopped (port %d free)" % (branch, entry["port"]))
            else:
                print("%s: could not stop process group %d" % (branch, entry["pgid"]))
                continue
            entry.update(status="stopped", pid=None, pgid=None, token=None)
        save_state(state_dir, state)
    return 0


def cmd_clean(args):
    main, state_dir = repo_info(args.repo)
    with locked(state_dir):
        state = load_state(state_dir)
        for branch in sorted(state["branches"]):
            entry = state["branches"][branch]
            if entry_owns_process(entry):
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
        if r.get("note"):
            print("%s: %s" % (r["branch"], r["note"]))
        if r.get("log") and r.get("healthy"):
            print("%s: log %s" % (r["branch"], r["log"]))


# ------------------------------------------------------------------------ CLI

def build_parser():
    parser = argparse.ArgumentParser(prog="pb.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", default=os.getcwd(), help="path inside the git repo (default: cwd)")
    sub = parser.add_subparsers(dest="command", required=True)

    up = sub.add_parser("up", help="create worktrees and start one dev server per branch")
    up.add_argument("branches", nargs="+")
    up.add_argument("--cmd", required=True,
                    help="dev command; {PORT} and {HMR_PORT} are replaced, and both are also exported")
    up.add_argument("--create", action="store_true", help="create the branch from HEAD if it does not exist")
    up.add_argument("--worktree-dir", help="where to put new worktrees (default: <repo>.worktrees)")
    up.add_argument("--timeout", type=int, default=60, help="seconds to wait for the server (default 60)")
    up.add_argument("--health-path", default="/", help="URL path used for the health check")
    up.add_argument("--no-verify-owner", action="store_true",
                    help="skip the check that our process owns the port (for servers that daemonize)")
    up.add_argument("--dry-run", action="store_true", help="show ports and commands, start nothing")
    up.add_argument("--json", action="store_true")
    up.set_defaults(func=cmd_up)

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
