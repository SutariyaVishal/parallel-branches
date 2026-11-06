# parallel-branches

[![ci](https://github.com/SutariyaVishal/parallel-branches/actions/workflows/ci.yml/badge.svg)](https://github.com/SutariyaVishal/parallel-branches/actions/workflows/ci.yml)
[![ci-macos](https://github.com/SutariyaVishal/parallel-branches/actions/workflows/ci-macos.yml/badge.svg)](https://github.com/SutariyaVishal/parallel-branches/actions/workflows/ci-macos.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Run several git branches side by side, each with its own dev server on its own port, so you can compare them in the browser without stashing, switching and restarting.

It is an [agent skill](https://agentskills.io) (for Claude Code, Codex, Gemini CLI, Cursor and others that read `SKILL.md`) built around one dependency-free Python script, `pb.py`. You can also use the script on its own.

> **Status: beta (v0.5.4).** Tested in CI on Linux, macOS and Windows with Python 3.9 and newer. Port handling was measured against real Vite, Flask and plain Node servers; the other stacks follow each tool's documentation. See [Limits](#limits).

## The problem

You are working on two tasks, each on its own branch. To look at both in a browser you stash, switch, restart the dev server, look, and do it all again in reverse. Git worktrees give each branch its own folder, but they do not install dependencies, pick ports, start the servers, or clean up afterwards. That part is what this does.

## What it does

For each branch you name, `pb`:

1. creates a git worktree for it,
2. clones `node_modules` from your main checkout when the lockfile is identical (copy-on-write where the filesystem allows it), and otherwise installs dependencies,
3. copies your git-ignored `.env` files over,
4. picks a free, stable port per branch,
5. starts the dev server detached, and waits until it really answers,
6. prints one URL per branch, with warnings about hardcoded ports and URLs that will break on a new port.

It stops only the processes it started, refuses to delete worktrees that have uncommitted work, and can open a throwaway browser profile with web security off when CORS is in your way.

## Install

**Claude Code**

```
/plugin install parallel-branches --marketplace SutariyaVishal/parallel-branches
```

**Other agents that read `SKILL.md`:** copy `skills/parallel-branches` into `.agents/skills/` (project) or `~/.agents/skills/` (user).

**Just the script:** `skills/parallel-branches/scripts/pb.py`. It needs git and Python 3.9 or newer, nothing else. In the examples below, `pb` stands for `python3 /path/to/skills/parallel-branches/scripts/pb.py`, run inside your repository.

## Quick start

A real session, with paths shortened. The project is a Node app whose `dev` script reads `PORT`.

```
$ pb up feature-login fix-checkout --dry-run
BRANCH         URL  PORT  PID  PATH
feature-login  -    4403  -    -
fix-checkout   -    4174  -    -
feature-login: stack node (low confidence)
feature-login: command npm run dev
feature-login: dry run, nothing started (stack and warnings read from the main checkout)
...

$ pb up feature-login fix-checkout
BRANCH         URL                    PORT  PID    PATH
feature-login  http://localhost:4403  4403  86641  ~/code/myapp.worktrees/feature-login
fix-checkout   http://localhost:4174  4174  86686  ~/code/myapp.worktrees/fix-checkout
feature-login: HTTP 200
fix-checkout: HTTP 200

$ pb status
BRANCH         URL                    STATUS   PID    DEV-BROWSER  PATH
feature-login  http://localhost:4403  healthy  86641  -            ~/code/myapp.worktrees/feature-login
fix-checkout   http://localhost:4174  healthy  86686  -            ~/code/myapp.worktrees/fix-checkout

$ pb down --all
feature-login: stopped (port 4403 free)
fix-checkout: stopped (port 4174 free)

$ pb clean --yes
feature-login: removed worktree ~/code/myapp.worktrees/feature-login
fix-checkout: removed worktree ~/code/myapp.worktrees/fix-checkout
```

Always start with `--dry-run`. The start command comes from your repository (`package.json` scripts, `.parallel-branches.json`) and runs in a shell on your machine, so read it first. A dry run changes nothing: no worktree, no process, no files under `.git`.

When detection finds nothing, or guesses wrong, say what to run: `pb up feature-a --cmd 'my-server --port {PORT}'`.

## Commands

| Command | What it does |
|---|---|
| `pb up BRANCH... [--dry-run] [--cmd CMD] [--no-install] [--browser]` | Create worktrees, install, start, health-check |
| `pb status [--json]` | Branches, URLs, health, dev-browser endpoints |
| `pb restart BRANCH [SERVICE]` | Restart one service or all of them, on the same ports |
| `pb down BRANCH...` or `--all` | Stop what `pb` started, and close its dev browsers |
| `pb clean [--yes] [--force]` | Remove worktrees `pb` created. A dry run unless `--yes` |
| `pb browser BRANCH` | Open a dev-mode browser (web security off, throwaway profile) |
| `pb init [--write]` | Suggest a `services` config for a monorepo |
| `pb detect [PATH]` | Show the detected stack, install plan and any monorepo suggestion |

`{PORT}` and `{HMR_PORT}` in a command are replaced, and both are also exported as environment variables. Every `pb` command takes `--repo PATH` to run from outside the repository.

## Which stacks

Detected automatically: Next.js, Nuxt, Angular, Astro, SvelteKit, Vite, webpack-dev-server, Create React App, other Node projects with a `dev`, `start` or `serve` script (npm, yarn, pnpm and bun), Django, Flask, FastAPI, Rails, Spring Boot (Maven and Gradle) and Go.

Not every tool reads `PORT`: Vite, Flask, Django and others need a flag or a different variable, so each stack has its own command template. Stacks where the port cannot be set from outside (Create React App, Go, unknown Node projects) are marked low confidence, and a failed start tells you to pass `--cmd`. Details, the dependency rules, and the full config format are in [`skills/parallel-branches/references/stacks.md`](skills/parallel-branches/references/stacks.md).

## Several services per branch

A branch can need more than one process. Describe them in `.parallel-branches.json`:

```json
{
  "services": [
    {"name": "api", "cwd": "backend", "cmd": "python3 manage.py runserver 127.0.0.1:{PORT}"},
    {"name": "web", "cwd": "frontend", "cmd": "npm run dev -- --port {PORT} --strictPort",
     "env": {"API_URL": "{url.api}"}, "depends_on": ["api"], "primary": true}
  ]
}
```

Every service gets its own port; `{url.api}` and `{ports.api}` become the other service's address; services start in dependency order, and if one fails the others are stopped. In a monorepo, `pb init` suggests this file from your workspaces or folder layout and writes nothing unless you pass `--write`.

## The dev-mode browser, and what it cannot fix

`pb browser BRANCH` opens Chrome or Chromium with a **new empty profile** and `--disable-web-security`, on that branch's own `localhost` URL, and prints a DevTools endpoint a browser agent can attach to. `pb down` closes it and deletes the profile.

It fixes CORS: a page on one port can read an API on another port or host that sends no CORS headers. It does not fix:

- OAuth or OpenID Connect redirect URI mismatches. The identity server rejects a callback on a port it has not registered, and no browser setting changes that. If your login is pinned to one port, only that one branch can log in.
- Servers that reject requests by `Origin`, CSRF token or `Host`.
- Cookie rules, or cookies shared between ports on `localhost`.

Use that window only for the app it opened. With web security off, any site loaded in it can read other origins.

## How it compares

This space has good tools, and you may prefer one of them:

- [Worktrunk](https://github.com/max-sixty/worktrunk), [git-worktree-runner](https://github.com/coderabbitai/git-worktree-runner) and [gwq](https://github.com/d-kuro/gwq) manage worktrees well, with hooks and editor or agent integration, but you wire up the ports and dev servers yourself.
- [Portless](https://github.com/vercel-labs/portless) handles port allocation and a local proxy.
- Claude Code's built-in worktrees and obra/superpowers' `using-git-worktrees` create worktrees (the latter also installs dependencies) but do not run dev servers.

`parallel-branches` aims at the whole loop for a repository you have not configured: detect the stack, share or install dependencies, allocate ports, start and health-check the servers, warn about what will break, and stop and clean up safely. It trades breadth of worktree features for that.

## Safety

- Only process groups that `pb` started are stopped, and a recorded start time must still match, so a reused PID is never killed.
- `clean` is a dry run without `--yes`, skips running servers, and skips worktrees with uncommitted changes unless you pass `--force`.
- Branch names starting with `-` are rejected, git is called with argument lists, and state is written under a file lock.
- Commands in `.parallel-branches.json` and in `package.json` run in a shell. Treat an unfamiliar repository the way you would before running `npm run dev` in it.

More in [`skills/parallel-branches/references/safety.md`](skills/parallel-branches/references/safety.md). To report a vulnerability, see [SECURITY.md](SECURITY.md).

## Limits

- **Windows:** stopping a service force-kills its whole process tree (there is no gentle signal), and the check that `pb`'s own process owns the port is skipped. A handful of tests that need a POSIX shell are skipped there.
- **Memory:** every dev server is a real process. Three or four at once is a sensible ceiling on a laptop; some frameworks (for example Angular with a large heap setting) need much more per server.
- **Port race:** if another program takes a port between the free-port check and the server's bind, the start fails and is reported, not retried.
- **Daemonizing servers** (for example Docker) escape their process group. Use `--no-verify-owner` and stop them yourself.
- **Slow reverse DNS:** `python -m http.server` can take 30 seconds or more to open its port on hosts where reverse DNS is slow, which shows up as `timed out ... nothing answering`. Raise `--timeout` or use another server.
- Detection beyond Vite, Flask and plain Node has not been exercised against real servers.

## Development

```
python3 tests/validate_skill.py                 # skill and manifest rules
python3 -m unittest discover tests/scripts      # unit and CLI tests (about a minute)
ruff check .                                    # lint
```

Behaviour of the skill with real models is checked by `tests/evals/` (five scenarios, graded by deterministic rules). See [CONTRIBUTING.md](CONTRIBUTING.md) for how to add a stack and how to run the evals, which spend API money. [docs/DESIGN.md](docs/DESIGN.md) records the design decisions, measurements and known gaps, and [CHANGELOG.md](CHANGELOG.md) the releases.

## License

MIT. See [LICENSE](LICENSE).
