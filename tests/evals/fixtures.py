"""Throwaway repositories for the evals, and helpers to put them in a known state."""

import json
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PB = os.path.join(ROOT, "skills", "parallel-branches", "scripts", "pb.py")
BRANCHES = ("feat-a", "feat-b")
DEV_SCRIPT = "python3 -m http.server $PORT --bind 127.0.0.1"

FILES = {
    "dev-app": {
        "package.json": json.dumps({"name": "demo", "scripts": {"dev": DEV_SCRIPT}}, indent=2),
        ".parallel-branches.json": json.dumps({"install": False}),
        "index.html": "<!doctype html><title>demo</title><h1>demo</h1>\n",
    },
    # Same app, but it talks to an API on another port and has an OAuth redirect URI.
    "cors-app": {
        "package.json": json.dumps({"name": "demo", "scripts": {"dev": DEV_SCRIPT}}, indent=2),
        ".parallel-branches.json": json.dumps({"install": False}),
        "index.html": "<!doctype html><title>demo</title><h1>demo</h1>\n",
        ".env.example": "VITE_API_URL=http://localhost:9999/api\nOAUTH_REDIRECT_URI=http://localhost:4200/callback\n",
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
                          stderr=subprocess.PIPE, universal_newlines=True)


def start(repo, *branches):
    proc = pb(repo, "up", *branches, "--timeout", "30", "--json")
    if proc.returncode != 0:
        raise RuntimeError("setup could not start %s: %s %s" % (branches, proc.stdout, proc.stderr))
    return json.loads(proc.stdout)


def make_dirty(repo, branch):
    """Leave an uncommitted file in the branch's worktree."""
    state = json.loads(pb(repo, "status", "--json").stdout)
    path = next(e["path"] for e in state if e["branch"] == branch)
    with open(os.path.join(path, "notes.txt"), "w") as fh:
        fh.write("unsaved work\n")
    return path
