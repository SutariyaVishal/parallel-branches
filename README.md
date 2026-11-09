# parallel-branches

[![ci](https://github.com/SutariyaVishal/parallel-branches/actions/workflows/ci.yml/badge.svg)](https://github.com/SutariyaVishal/parallel-branches/actions/workflows/ci.yml)
[![ci-macos](https://github.com/SutariyaVishal/parallel-branches/actions/workflows/ci-macos.yml/badge.svg)](https://github.com/SutariyaVishal/parallel-branches/actions/workflows/ci-macos.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Run several git branches side by side, each with its own dev server on its own port, so you can compare them in the browser without stashing, switching and restarting.

It is an [agent skill](https://agentskills.io) in the open `SKILL.md` format, built around one dependency-free Python script, `pb.py`. It has only been tried with Claude Code. Other agents that read `SKILL.md` (Codex, Gemini CLI, Cursor and others) should be able to use it, but that is untested. You can also use the script on its own.

> **Status: beta (v0.13.0).** Tested in CI on Linux, macOS and Windows with Python 3.9 and newer. Port handling was measured against real Vite, Flask and plain Node servers; the other stacks follow each tool's documentation. See [Limits](#limits).

## The problem

You are working on two tasks, each on its own branch. To look at both in a browser you stash, switch, restart the dev server, look, and do it all again in reverse. Git worktrees give each branch its own folder, but they do not install dependencies, pick ports, start the servers, or clean up afterwards. That part is what this does.

## What it does

For each branch you name, `pb`:

1. creates a git worktree for it,
2. clones `node_modules` from your main checkout when the lockfile is identical (copy-on-write where the filesystem allows it), and otherwise installs dependencies,
3. copies your git-ignored `.env` files over,
4. picks a free, stable port per branch,
5. starts the dev server detached, and waits until it really answers,
6. prints one URL per branch, with warnings about hardcoded ports and URLs that will break on a new port,
7. runs your [hooks](#hooks) at each step, for things like a per-branch database.

It stops only the processes it started, refuses to delete worktrees that have uncommitted work, and can open a throwaway browser profile with web security off when CORS is in your way.

## Install

**Claude Code**

```
/plugin install parallel-branches --marketplace SutariyaVishal/parallel-branches
```

**Other agents that read `SKILL.md` (untested):** copy `skills/parallel-branches` into `.agents/skills/` (project) or `~/.agents/skills/` (user).

**Just the script:** `skills/parallel-branches/scripts/pb.py`. It needs git and Python 3.9 or newer, nothing else. In the examples below, `pb` stands for `python3 /path/to/skills/parallel-branches/scripts/pb.py`, run inside your repository.

## Quick start

![A dry run, then two branches started, listed, stopped and cleaned up](docs/demo.gif)

With an agent, ask in plain words: *"run feature-login and fix-checkout side by side"*. It shows you the plan (a dry run) and waits for your OK before it starts anything. By hand, it looks like this.

The animation replays real `pb` output from a two-branch run (typing and pauses are added, and the screen is cleared after the dry run). Below is the same session as text, with paths shortened. The project is a Node app whose `dev` script reads `PORT`.

```
$ pb up feature-login fix-checkout --dry-run
BRANCH         URL  PORT  PID  PATH
feature-login  -    4403  -    -
fix-checkout   -    4174  -    -
feature-login: stack node (low confidence)
feature-login: command npm run dev
feature-login: install: npm ci
feature-login: dry run, nothing started (stack and warnings read from the main checkout)
fix-checkout: stack node (low confidence)
fix-checkout: command npm run dev
fix-checkout: install: npm ci
fix-checkout: dry run, nothing started (stack and warnings read from the main checkout)

$ pb up feature-login fix-checkout
BRANCH         URL                    PORT  PID   PATH
feature-login  http://localhost:4403  4403  9262  ~/code/myapp.worktrees/feature-login
fix-checkout   http://localhost:4174  4174  9306  ~/code/myapp.worktrees/fix-checkout
feature-login: stack node (low confidence)
feature-login: command npm run dev
feature-login: install: npm ci
feature-login: HTTP 200
feature-login: log ~/code/myapp/.git/parallel-branches/logs/feature-login.log
fix-checkout: stack node (low confidence)
fix-checkout: command npm run dev
fix-checkout: install: npm ci
fix-checkout: HTTP 200
fix-checkout: log ~/code/myapp/.git/parallel-branches/logs/fix-checkout.log

$ pb status
BRANCH         URL                    STATUS   PID   DEV-BROWSER  PATH
feature-login  http://localhost:4403  healthy  9262  -            ~/code/myapp.worktrees/feature-login
fix-checkout   http://localhost:4174  healthy  9306  -            ~/code/myapp.worktrees/fix-checkout

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
| `pb up BRANCH... [--dry-run] [--cmd CMD] [--no-install] [--no-hooks] [--browser]` | Create worktrees, install, start, health-check |
| `pb status [--json]` | Branches, URLs, health, dev-browser endpoints |
| `pb restart BRANCH [SERVICE]` | Restart one service or all of them, on the same ports |
| `pb down BRANCH...` or `--all` | Stop what `pb` started, and close its dev browsers. `--no-hooks` skips `pre_stop` |
| `pb clean [--yes] [--force]` | Remove worktrees `pb` created. A dry run unless `--yes`. `--no-hooks` skips `pre_remove` and `post_remove` |
| `pb hooks [BRANCH] [-v]` | Show the hooks stored for a branch with values filled in, and the last result of each. `-v` adds every value a hook can use. |
| `pb hook EVENT BRANCH [--dry-run]` | Run one stored hook of a tracked branch by hand, or show it |
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

## Hooks

Run your own commands at fixed points: create a database when the worktree appears, seed it once the API answers, drop it when the worktree goes.

```json
{
  "hooks": {
    "post_create": "createdb {db_name}",
    "post_start": "curl -fsS -X POST {URL}/seed",
    "post_remove": "dropdb --if-exists {db_name}"
  }
}
```

Six events: `post_create` (new worktree, before install), `pre_start` (before any service starts), `post_start` (every service healthy), `pre_stop` (`down`, services still up), `pre_remove` (`clean --yes`, just before the worktree goes) and `post_remove` (after it is gone). A failing `post_create` or `pre_start` stops `up`, and a failing `pre_remove` keeps its worktree (`clean --no-hooks` removes it anyway). A failing `post_start`, `pre_stop` or `post_remove` is only reported. `pb hook EVENT BRANCH` runs one by hand.

What sets this apart from a plain "run a command" hook:

- Hooks see the ports that were actually allocated, as `{PORT}`, `{URL}` (or `{url.api}` for a named service) and `PB_PORT`, not a number derived from the branch name.
- A step can be an object of named commands, `{"deps": "npm ci", "assets": "make assets"}`, which start together; the next step waits for all of them.
- `post_start` runs only after the health checks pass. Add `"background_hooks": ["post_start"]` and it runs in the background, so `up` does not wait for a slow seed; `pb hooks` shows the result and `down` stops it if still running.
- Values are shell-quoted, so a branch named `a$(touch${IFS}pwned)`, which git accepts, cannot run anything.
- `up --dry-run` prints every hook with values filled in. `down` and `clean` run the commands stored when the branch started, not whatever the config says later.
- `pb hook post_start feat-a` runs a stored hook again, for example to re-seed a database; `--dry-run` only shows it.
- A per-user file (`~/.config/parallel-branches/config.json`) holds hooks for every repository. They run first and show as `(user)` in dry runs.
- Each command has a timeout and a log file.

Full reference: [`skills/parallel-branches/references/hooks.md`](skills/parallel-branches/references/hooks.md). `parallel-branches` has no commit or merge hooks; it does not commit or merge.

## The dev-mode browser, and what it cannot fix

`pb browser BRANCH` opens Chrome or Chromium with a **new empty profile** and `--disable-web-security`, on that branch's own `localhost` URL, and prints a DevTools endpoint a browser agent can attach to. `pb down` closes it and deletes the profile.

It fixes CORS: a page on one port can read an API on another port or host that sends no CORS headers. It does not fix:

- OAuth or OpenID Connect redirect URI mismatches. The identity server rejects a callback on a port it has not registered, and no browser setting changes that. If your login is pinned to one port, only that one branch can log in.
- Servers that reject requests by `Origin`, CSRF token or `Host`.
- Cookie rules, or cookies shared between ports on `localhost`.

Use that window only for the app it opened. With web security off, any site loaded in it can read other origins.

## How it compares

This space has good tools, and you may prefer one of them. What follows is from each project's own README in November 2026, so it will age, and a feature a README does not mention may still exist.

- [Worktrunk](https://github.com/max-sixty/worktrunk) is the fullest worktree manager: `switch`, `list`, `merge`, an interactive picker, aliases, ten hooks with templates (including a `hash_port` filter that gives each worktree a port) and a `copy-ignored` step. Starting dev servers and installing dependencies are left to hooks you write.
- [git-worktree-runner](https://github.com/coderabbitai/git-worktree-runner) and [gwq](https://github.com/d-kuro/gwq) manage worktrees with hooks or setup commands, editor and AI-tool launching (gtr) and status monitoring (gwq). Neither allocates ports. Dev servers and installs are left to commands you write.
- [Portless](https://github.com/vercel-labs/portless) gives each app a stable `.localhost` URL, with HTTPS and a subdomain per worktree, through a local proxy, and runs your dev command with the port passed in. Its README does not cover installing dependencies or health checks.
- Claude Code's built-in worktrees and obra/superpowers' `using-git-worktrees` create worktrees (the latter also installs dependencies) but do not run dev servers.

### Where parallel-branches leads

None of the tools above does the whole loop for a repository you have not configured. This one does, and these parts are what it has that their READMEs do not describe:

- **One command from branch to URL.** It detects the stack, works out how that dev server takes a port, starts it, and prints the URL. No config file is needed for the common stacks.
- **Dependencies without a hook.** It clones `node_modules` copy-on-write when the lockfile matches your main checkout, and otherwise installs with the right package manager, or builds a per-worktree Python virtual environment.
- **A health check that means something.** It waits until the server answers, and, where `lsof` is available (macOS and most Linux), that the process on the port is the one it started, so a stale server on the same port is not reported as healthy.
- **Warnings before they bite.** It flags hardcoded ports and URLs that will break on a new port, separates what a dev-mode browser cannot fix (OAuth redirects) from what it can (CORS), and opens that throwaway browser for you.
- **Several services that find each other.** `{url.NAME}` and `{ports.NAME}` are filled in after every port is chosen, so a front end and its API start in either order.
- **Safe stop and clean-up.** It stops only process groups it started, checks the recorded start time so a reused PID is never killed, refuses to delete worktrees with uncommitted work, and shows a dry run before anything is removed.
- **Hooks that know the real ports.** They receive the port that was actually allocated and every service's URL, `post_start` runs after the health check (optionally in the background), steps can run commands concurrently, and teardown runs the commands you saw in the dry run.
- **Nothing to install but Python.** It is one standard-library script for Python 3.9 and newer, tested in CI on Linux, macOS and native Windows.

In return it has no `switch`, `list` or `merge`, no stable named URLs or HTTPS (so cookies are still shared between `localhost` ports), no editor or agent launcher, and far less mileage than the tools above. See [Limits](#limits).

## Safety

- Only process groups that `pb` started are stopped, and a recorded start time must still match, so a reused PID is never killed.
- `clean` is a dry run without `--yes`, skips running servers, and skips worktrees with uncommitted changes unless you pass `--force`.
- Branch names starting with `-` are rejected, git is called with argument lists, and state is written under a file lock.
- Commands in `.parallel-branches.json` (start commands and hooks) and in `package.json` run in a shell. Treat an unfamiliar repository the way you would before running `npm run dev` in it.

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
python3 -m unittest discover tests/scripts      # unit and CLI tests (about three minutes)
ruff check .                                    # lint
```

Behaviour of the skill with real models is checked by `tests/evals/` (ten scenarios, graded by deterministic rules). See [CONTRIBUTING.md](CONTRIBUTING.md) for how to add a stack and how to run the evals, which spend API money. [docs/DESIGN.md](docs/DESIGN.md) records the design decisions, measurements and known gaps, and [CHANGELOG.md](CHANGELOG.md) the releases. Everyone taking part is asked to follow the [code of conduct](CODE_OF_CONDUCT.md).

## Feedback

If `parallel-branches` saves you time, a star on GitHub helps other people find it. Bug reports, ideas and pull requests are welcome: [open an issue](https://github.com/SutariyaVishal/parallel-branches/issues).

## License

MIT. See [LICENSE](LICENSE).
