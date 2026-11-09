"""Build plugin/, the docs-only copy of the skill that goes to the Claude plugin directory.

The directory refuses scripts from organizations that are not Verified, so plugin/ holds only
Markdown, JSON and PNG files. Its SKILL.md runs the published PyPI package with uvx instead of
bundled scripts. Everything in plugin/ is generated from skills/ and the root manifest.

Usage:
  python3 tools/build_directory_plugin.py            write plugin/
  python3 tools/build_directory_plugin.py --check    fail if plugin/ is stale or holds a refused file
"""

import json
import os
import re
import shutil
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SKILL = os.path.join(ROOT, "skills", "parallel-branches")
OUT = os.path.join(ROOT, "plugin")
ALLOWED_SUFFIXES = (".md", ".json", ".png")
COMPATIBILITY = ("Requires git and uv (https://docs.astral.sh/uv/), which fetches Python and the pinned "
                 "parallel-branches package from PyPI on first use. Chrome or Chromium is needed only for the "
                 "dev-mode browser. Tested in CI on Linux, macOS and Windows; Windows has limits (see "
                 "references/safety.md).")
INTRO_START = "One script, `scripts/pb.py`, manages everything."
README = """# parallel-branches

Run several git branches side by side. For each branch it creates a git worktree, copies or installs
dependencies, picks a free port, starts the dev server detached, waits until the server answers, and prints
one URL per branch. It can also open a separate Chrome profile with web security off for apps that hit CORS
errors, and run your own hooks (for example one database per branch).

## What this plugin runs

This plugin contains only the skill instructions. It has no scripts. When the skill is used, the agent runs
the `pb` command-line tool from the `parallel-branches` package on PyPI, pinned to version {version}, through
`uvx` (part of [uv](https://docs.astral.sh/uv/)). The first run downloads that package from PyPI into an
isolated environment. The package source is in this repository under `skills/parallel-branches/scripts/`.

The skill tells the agent to show the plan (`pb up --dry-run`) and get your approval before starting anything.

## What the tool touches

- Creates git worktrees next to your repository, in `<repo>.worktrees/`.
- Writes state and logs under the repository's git directory, in `.git/parallel-branches/`.
- Starts dev servers, and installs dependencies when asked. These commands come from your repository
  (`package.json`, `.parallel-branches.json`) and run in a shell with your shell's environment.
- Copies your git-ignored `.env` files into new worktrees. `pb up --dry-run` lists them first.
- Makes no network request itself, except health checks to `localhost`. Package installs and your own hooks
  use the network the way the commands you approved do.
- It does not read, store or send credentials.

## More

Full documentation, the Claude Code plugin with the bundled script, and the issue tracker are at
https://github.com/SutariyaVishal/parallel-branches. MIT licensed.
"""


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def version():
    return json.loads(read(os.path.join(ROOT, ".claude-plugin", "plugin.json")))["version"]


def skill_md(ver):
    text = read(os.path.join(SKILL, "SKILL.md"))
    text = re.sub(r"^compatibility: .*$", lambda _: "compatibility: " + COMPATIBILITY, text, count=1, flags=re.M)
    start = text.index(INTRO_START)
    end = text.index("\n", start)
    intro = ("One command-line tool, `pb`, manages everything. It is the `parallel-branches` package from PyPI, "
             "run with `uvx --from parallel-branches==%s pb`. In the examples below, `PB` is shorthand for that "
             "command. When you run it, write the full command out every time and do not put it in a shell "
             "variable: approval rules match the literal command text, so a command written out in full can be "
             "pre-approved and `$PB up ...` cannot. Run it from inside the user's repository, or pass "
             "`--repo <path>`." % ver)
    text = text[:start] + intro + text[end:]
    assert "scripts/" not in text and "pb.py" not in text, "SKILL.md still mentions the bundled script"
    return text


def build_files():
    """Return {relative path under plugin/: bytes}."""
    ver = version()
    files = {}
    plugin = json.loads(read(os.path.join(ROOT, ".claude-plugin", "plugin.json")))
    files[".claude-plugin/plugin.json"] = (json.dumps(plugin, indent=2) + "\n").encode()
    with open(os.path.join(ROOT, ".claude-plugin", "icon.png"), "rb") as fh:
        files[".claude-plugin/icon.png"] = fh.read()
    files["LICENSE"] = read(os.path.join(ROOT, "LICENSE")).encode()
    files["README.md"] = README.format(version=ver).encode()
    files["skills/parallel-branches/SKILL.md"] = skill_md(ver).encode()
    for folder in ("references", "assets"):
        for name in sorted(os.listdir(os.path.join(SKILL, folder))):
            body = read(os.path.join(SKILL, folder, name))
            if folder == "references":
                body = body.replace("pb.py", "pb")
            files["skills/parallel-branches/%s/%s" % (folder, name)] = body.encode()
    return files


def refused(rel, data):
    """Why the directory would refuse this file from an unverified organization, or None."""
    if not (rel == "LICENSE" or rel.endswith(ALLOWED_SUFFIXES)):
        return "type is not Markdown, JSON or PNG"
    if data.startswith(b"#!"):
        return "starts with #!"
    if not rel.endswith(".png"):
        if b"\0" in data:
            return "holds a NUL byte"
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            return "is not UTF-8"
    return None


def disk_files():
    found = {}
    for base, _dirs, names in os.walk(OUT):
        for name in names:
            path = os.path.join(base, name)
            with open(path, "rb") as fh:
                found[os.path.relpath(path, OUT).replace(os.sep, "/")] = fh.read()
    return found


def check():
    problems = []
    wanted, actual = build_files(), disk_files()
    for rel in sorted(set(wanted) | set(actual)):
        if rel not in actual:
            problems.append("plugin/%s is missing" % rel)
        elif rel not in wanted:
            problems.append("plugin/%s should not exist" % rel)
        elif actual[rel] != wanted[rel]:
            problems.append("plugin/%s is out of date" % rel)
    for rel, data in sorted(actual.items()):
        why = refused(rel, data)
        if why:
            problems.append("plugin/%s would be refused by the directory: %s" % (rel, why))
    pyproject = read(os.path.join(ROOT, "pyproject.toml"))
    match = re.search(r'^version = "([^"]+)"', pyproject, flags=re.M)
    if not match or match.group(1) != version():
        problems.append("pyproject.toml version %s does not match plugin.json %s"
                        % (match.group(1) if match else None, version()))
    return problems


def main(argv):
    if "--check" in argv:
        problems = check()
        for problem in problems:
            print("FAIL", problem)
        print("plugin/: %d problem(s)" % len(problems))
        return 1 if problems else 0
    shutil.rmtree(OUT, ignore_errors=True)
    for rel, data in build_files().items():
        path = os.path.join(OUT, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
    print("wrote plugin/ (%d files)" % len(build_files()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
