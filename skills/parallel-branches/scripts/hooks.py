"""Lifecycle hooks: shell commands from `hooks` in .parallel-branches.json.

Used by pb.py. Python 3.9+, standard library only.

Events, in the order a branch meets them:

  post_create  worktree just created and env files copied, before dependencies are installed
  pre_start    dependencies installed, no service started yet (runs on every `up`)
  post_start   every service answered its health check
  pre_stop     `down`, while the services are still running
  pre_remove   `clean --yes`, just before the worktree is removed
  post_remove  `clean --yes`, after the worktree is gone

post_create and pre_start abort `up` when they fail, and pre_remove keeps its worktree in place (`--no-hooks`
removes it anyway). The others only report a failure, so a broken hook can never keep a branch from
stopping or from being removed by `clean --no-hooks`.

Commands may use {branch} {branch_slug} {db_name} {worktree} {repo} {PORT} {HMR_PORT} {URL}
{ports.NAME} {url.NAME}. Substituted values are shell-quoted, because a branch name can contain
characters the shell treats specially. The same values are exported as PB_* environment variables.
"""

import hashlib
import os
import re
import subprocess

import procs
import stacks

EVENTS = ("post_create", "pre_start", "post_start", "pre_stop", "pre_remove", "post_remove")
BLOCKING = ("post_create", "pre_start", "pre_remove")
DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 3600
# `$` before the brace means the shell's own ${VAR}, which is not ours to touch.
TOKEN_RE = re.compile(r"(?<!\$)\{(branch_slug|branch|db_name|worktree|repo|PORT|HMR_PORT|URL"
                      r"|ports\.[a-z0-9-]+|url\.[a-z0-9-]+)\}")


NAMES = ("branch", "branch_slug", "db_name", "worktree", "repo", "PORT", "HMR_PORT", "URL")
WORDLIKE_RE = re.compile(r"(?<!\$)\{([A-Za-z_]+)\}")


def parse(cfg, service_names=()):
    """Validate the hooks in a config. Returns ({event: [commands]}, timeout seconds).

    Raises ValueError with a message the user can act on.
    """
    raw = cfg.get("hooks")
    timeout = cfg.get("hook_timeout", DEFAULT_TIMEOUT)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 0 < timeout <= MAX_TIMEOUT:
        raise ValueError("hook_timeout must be a whole number of seconds between 1 and %d" % MAX_TIMEOUT)
    if raw is None:
        return {}, timeout
    if not isinstance(raw, dict):
        raise ValueError("hooks must be an object keyed by event: %s" % ", ".join(EVENTS))
    commands = {}
    for event, value in raw.items():
        if event not in EVENTS:
            raise ValueError("unknown hook %r; the events are %s" % (event, ", ".join(EVENTS)))
        items = [value] if isinstance(value, str) else value
        if not isinstance(items, list) or not items or not all(isinstance(x, str) and x.strip() for x in items):
            raise ValueError("hook %s must be a command string or a non-empty list of command strings" % event)
        for text in items:
            for word in WORDLIKE_RE.findall(text):  # {url} for {URL}: it would run literally, so refuse
                right = next((n for n in NAMES if n.lower() == word.lower()), None)
                if right and right != word:
                    raise ValueError("hook %s uses {%s}; the name is {%s}" % (event, word, right))
            for match in TOKEN_RE.finditer(text):
                kind, _, name = match.group(1).partition(".")
                if name and name not in service_names:
                    raise ValueError("hook %s refers to unknown service %r" % (event, name))
        commands[event] = list(items)
    return commands, timeout


def db_name(branch):
    """A name that is safe as a database or schema name and differs for branches like a/b and a-b."""
    base = re.sub(r"[^a-z0-9]+", "_", branch.lower()).strip("_")[:32] or "branch"
    return "%s_%s" % (base, hashlib.sha1(branch.encode("utf-8")).hexdigest()[:4])


def context(branch, slug, worktree, main, service_ports, primary):
    """Template values. `service_ports` maps service name (None for a single unnamed one) to
    (port, hmr_port); `primary` is the name of the service whose URL is the branch URL."""
    port, hmr = service_ports[primary]
    named = {name: pair[0] for name, pair in service_ports.items() if name}
    return {"branch": branch, "branch_slug": slug, "db_name": db_name(branch), "worktree": worktree,
            "repo": main, "PORT": port, "HMR_PORT": hmr, "URL": "http://localhost:%d" % port,
            "ports": named}


def context_from_entry(entry, main, slug):
    """The same, from a branch's registry entry (the primary service is stored flat, others in extras)."""
    service_ports = {entry.get("service"): (entry["port"], entry["hmr_port"])}
    for extra in entry.get("extras") or []:
        service_ports[extra["service"]] = (extra["port"], extra["hmr_port"])
    return context(entry["branch"], slug, entry["path"], main, service_ports, entry.get("service"))


def render(text, ctx):
    def fill(match):
        key = match.group(1)
        kind, _, name = key.partition(".")
        if kind in ("ports", "url") and name:
            if name not in ctx["ports"]:
                return match.group(0)
            port = ctx["ports"][name]
            return str(port) if kind == "ports" else "http://localhost:%d" % port
        return stacks.shell_quote(str(ctx[key]))
    return TOKEN_RE.sub(fill, text)


def environment(event, ctx):
    env = dict(os.environ, PB_HOOK=event, PB_BRANCH=ctx["branch"], PB_BRANCH_SLUG=ctx["branch_slug"],
               PB_DB_NAME=ctx["db_name"], PB_WORKTREE=ctx["worktree"], PB_REPO=ctx["repo"],
               PB_PORT=str(ctx["PORT"]), PB_HMR_PORT=str(ctx["HMR_PORT"]), PB_URL=ctx["URL"])
    for name, port in ctx["ports"].items():
        key = re.sub(r"[^A-Z0-9]", "_", name.upper())
        env["PB_PORT_" + key] = str(port)
        env["PB_URL_" + key] = "http://localhost:%d" % port
    return env


def preview(commands, ctx):
    """Each event's commands as they would run, for a dry run."""
    return [{"event": event, "commands": [render(text, ctx) for text in commands[event]]}
            for event in EVENTS if event in commands]


def run(event, commands, ctx, cwd, log_path, timeout):
    """Run the commands one after another, stopping at the first failure. Returns (ok, detail).

    Output goes to `log_path`. A command that outlives `timeout` has its whole process group killed.
    """
    env = environment(event, ctx)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "w") as log:
        for number, template in enumerate(commands, 1):
            cmd = render(template, ctx)
            log.write("$ %s\n" % cmd)
            log.flush()
            proc = procs.spawn(cmd, cwd, env, log)
            try:
                code = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                procs.stop_group(proc.pid)
                proc.poll()
                return False, "command %d timed out after %ds: %s" % (number, timeout, cmd)
            if code != 0:
                return False, "command %d exited with code %d: %s" % (number, code, cmd)
    return True, "%d command%s ran" % (len(commands), "" if len(commands) == 1 else "s")
