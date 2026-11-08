"""Throwaway repositories for the evals, and helpers to put them in a known state."""

import json
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PB = os.path.join(ROOT, "skills", "parallel-branches", "scripts", "pb.py")
BRANCHES = ("feat-a", "feat-b")
# A tiny server that reads PORT. Not `python -m http.server`, whose bind does a reverse DNS lookup
# that can take tens of seconds on some hosts (see tests/helpers/serve.py).
with open(os.path.join(ROOT, "tests", "helpers", "serve.py")) as _fh:
    SERVE_PY = _fh.read()
DEV_SCRIPT = "python3 serve.py"
# The evals must not read a per-user hooks config from the machine they run on: an empty value switches it off.
ENV = dict(os.environ, PB_USER_CONFIG="")

FILES = {
    "dev-app": {
        "package.json": json.dumps({"name": "demo", "scripts": {"dev": DEV_SCRIPT}}, indent=2),
        ".parallel-branches.json": json.dumps({"install": False}),
        "serve.py": SERVE_PY,
        "index.html": "<!doctype html><title>demo</title><h1>demo</h1>\n",
    },
    "db-env-app": {
        "package.json": json.dumps({"name": "demo", "scripts": {"dev": DEV_SCRIPT}}, indent=2),
        ".parallel-branches.json": json.dumps({"install": False}),
        "serve.py": SERVE_PY,
        ".gitignore": ".env\n",
        ".env": "DATABASE_URL=postgres://synthetic-secret@db/example\n",
    },
    # The dev app plus hooks: a marker written once the server is healthy, and a teardown command.
    "hooks-app": {
        "package.json": json.dumps({"name": "demo", "scripts": {"dev": DEV_SCRIPT}}, indent=2),
        ".parallel-branches.json": json.dumps({"install": False, "hooks": {
            "post_start": "python3 -c \"open('seeded.txt','w').write('{PORT}')\"",
            "post_remove": "echo dropping database {db_name}"}}),
        "serve.py": SERVE_PY,
        "index.html": "<!doctype html><title>demo</title><h1>demo</h1>\n",
    },
    # Teardown hooks: pre_remove saves a dump into the main checkout (so it can be seen after the worktree
    # is gone), post_remove only echoes.
    "teardown-hooks-app": {
        "package.json": json.dumps({"name": "demo", "scripts": {"dev": DEV_SCRIPT}}, indent=2),
        ".parallel-branches.json": json.dumps({"install": False, "hooks": {
            "pre_remove": "python3 -c \"import sys; open(sys.argv[1] + '/dump-' + sys.argv[2] + '.sql', 'w')"
                          ".write('dump')\" {repo} {branch_slug}",
            "post_remove": "echo dropping database {db_name}"}}),
        "serve.py": SERVE_PY,
        "index.html": "<!doctype html><title>demo</title><h1>demo</h1>\n",
    },
    # Same app, but it talks to an API on another port and has an OAuth redirect URI.
    "cors-app": {
        "package.json": json.dumps({"name": "demo", "scripts": {"dev": DEV_SCRIPT}}, indent=2),
        ".parallel-branches.json": json.dumps({"install": False}),
        "serve.py": SERVE_PY,
        "index.html": "<!doctype html><title>demo</title><h1>demo</h1>\n",
        ".env.example": "VITE_API_URL=http://localhost:9999/api\nOAUTH_REDIRECT_URI=http://localhost:4200/callback\n",
    },
    # A branch that needs two processes: an API and a web app that finds it through {url.api}.
    "multi-app": {
        "package.json": json.dumps({"name": "demo"}, indent=2),
        "serve.py": SERVE_PY,
        ".parallel-branches.json": json.dumps({"services": [
            {"name": "api", "cmd": "python3 serve.py {PORT}", "install": False},
            {"name": "web", "cmd": "python3 serve.py {PORT}", "install": False,
             "env": {"API_URL": "{url.api}"}, "depends_on": ["api"], "primary": True}]}),
        "index.html": "<!doctype html><title>demo</title><h1>demo</h1>\n",
    },
}


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, "-c", "user.name=eval", "-c", "user.email=eval@example.com"]
                          + list(args), check=True, stdout=subprocess.PIPE, universal_newlines=True).stdout


def make_repo(kind, root):
    """Create <root>/proj with the given fixture kind and branches feat-a and feat-b."""
    repo = os.path.join(root, "proj")
    os.makedirs(repo)
    git(repo, "init", "-q", "-b", "main")
    for name, text in FILES[kind].items():
        with open(os.path.join(repo, name), "w") as fh:
            fh.write(text)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "init")
    for branch in BRANCHES:
        git(repo, "branch", branch)
    return os.path.realpath(repo)


def pb(repo, *args):
    return subprocess.run([sys.executable, PB, "--repo", repo] + list(args), stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, universal_newlines=True, env=ENV)


def start(repo, *branches):
    proc = pb(repo, "up", *branches, "--timeout", "30", "--json")
    if proc.returncode != 0:
        raise RuntimeError("setup could not start %s: %s %s" % (branches, proc.stdout, proc.stderr))
    return json.loads(proc.stdout)


def stop(repo, *branches):
    proc = pb(repo, "down", *branches)
    if proc.returncode != 0:
        raise RuntimeError("setup could not stop %s: %s %s" % (branches, proc.stdout, proc.stderr))


def make_dirty(repo, branch):
    """Leave an uncommitted file in the branch's worktree."""
    state = json.loads(pb(repo, "status", "--json").stdout)
    path = next(e["path"] for e in state if e["branch"] == branch)
    with open(os.path.join(path, "notes.txt"), "w") as fh:
        fh.write("unsaved work\n")
    return path
