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

Hooks can also come from a per-user file (see `user_config_path`). Those run first, in every repository,
and are labelled "(user)" wherever hooks are listed. Both sets are stored with the branch when it starts.

Commands may use {branch} {branch_slug} {db_name} {worktree} {repo} {PORT} {HMR_PORT} {URL}
{ports.NAME} {url.NAME}. Substituted values are shell-quoted, because a branch name can contain
characters the shell treats specially. The same values are exported as PB_* environment variables.
"""

import hashlib
import json
import os
import re
import subprocess

import procs
import stacks

EVENTS = ("post_create", "pre_start", "post_start", "pre_stop", "pre_remove", "post_remove")
BLOCKING = ("post_create", "pre_start", "pre_remove")
DEFAULT_TIMEOUT = 120
BACKGROUND_EVENTS = ("post_start",)  # events that may run detached (`background_hooks`)
MAX_TIMEOUT = 3600
# `$` before the brace means the shell's own ${VAR}, which is not ours to touch.
TOKEN_RE = re.compile(r"(?<!\$)\{(branch_slug|branch|db_name|worktree|repo|PORT|HMR_PORT|URL"
                      r"|ports\.[a-z0-9-]+|url\.[a-z0-9-]+)\}")


NAMES = ("branch", "branch_slug", "db_name", "worktree", "repo", "PORT", "HMR_PORT", "URL")
WORDLIKE_RE = re.compile(r"(?<!\$)\{([A-Za-z_]+)\}")


USER_CONFIG_KEYS = ("hooks", "hook_timeout")


def user_config_path():
    """Where the per-user config lives, or None when it is switched off.

    PB_USER_CONFIG overrides the location (an empty value switches the file off, which the tests use).
    Otherwise: $XDG_CONFIG_HOME or ~/.config on POSIX, %APPDATA% on Windows, plus parallel-branches/config.json.
    """
    override = os.environ.get("PB_USER_CONFIG")
    if override is not None:
        return override or None
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "parallel-branches", "config.json")


def load_user():
    """The per-user config as (dict, path). ({}, None) when there is no such file.

    Only `hooks` and `hook_timeout` are accepted, so a misplaced key is an error and not silently ignored.
    Hook contents are checked here for shape only: names like {url.api} depend on the repository, so
    `parse` checks those per branch. Raises ValueError, with the path in the message.
    """
    path = user_config_path()
    if not path or not os.path.isfile(path):
        return {}, None
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ValueError("%s: cannot read it as JSON (%s)" % (path, exc))
    if not isinstance(cfg, dict):
        raise ValueError("%s: expected a JSON object" % path)
    unknown = sorted(set(cfg) - set(USER_CONFIG_KEYS))
    if unknown:
        raise ValueError("%s: unknown key %s; the user config takes only %s"
                         % (path, ", ".join(repr(k) for k in unknown), " and ".join(USER_CONFIG_KEYS)))
    try:
        parse(cfg, None)
    except ValueError as exc:
        raise ValueError("%s: %s" % (path, exc))
    return cfg, path


def merge(user, project):
    """One command list per event, user commands first. Returns (commands, sources), where
    sources[event] says "user" or "project" for each command. Sources are None when there are no user hooks."""
    merged, sources = {}, {}
    for event in EVENTS:
        mine, theirs = user.get(event, []), project.get(event, [])
        if mine or theirs:
            merged[event] = list(mine) + list(theirs)
            sources[event] = ["user"] * len(mine) + ["project"] * len(theirs)
    return merged, (sources if user else None)


def parse(cfg, service_names=()):
    """Validate the hooks in a config. Returns ({event: [commands]}, timeout seconds).

    `service_names` is the set of services {ports.X} and {url.X} may refer to; None skips that check.
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
                if name and service_names is not None and name not in service_names:
                    raise ValueError("hook %s refers to unknown service %r" % (event, name))
        commands[event] = list(items)
    return commands, timeout


def parse_background(cfg):
    """The events listed in `background_hooks`, whose commands run detached. Raises ValueError."""
    raw = cfg.get("background_hooks", [])
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        raise ValueError("background_hooks must be a list of event names")
    for event in raw:
        if event not in BACKGROUND_EVENTS:
            raise ValueError("background_hooks: only %s can run in the background, not %r"
                             % (", ".join(BACKGROUND_EVENTS), event))
    return list(dict.fromkeys(raw))


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


def preview(commands, ctx, sources=None):
    """Each event's commands as they would run, for a dry run. `sources` (from `merge`) adds a "sources"
    list of "user" or "project" per command."""
    rows = []
    for event in EVENTS:
        if event in commands:
            row = {"event": event, "commands": [render(text, ctx) for text in commands[event]]}
            if sources:
                row["sources"] = sources[event]
            rows.append(row)
    return rows


def label(event_sources, index):
    """" (user)" for a command that came from the user config, else an empty string."""
    return " (user)" if event_sources and event_sources[index:index + 1] == ["user"] else ""


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
