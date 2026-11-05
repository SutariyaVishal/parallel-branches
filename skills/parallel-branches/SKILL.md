---
name: parallel-branches
description: Runs several git branches side by side, each in its own worktree with its own dev server and port, so they can be compared in the browser. Use when the user wants to work on multiple tasks or branches at once, run different branches at the same time, check two branches in a browser together, open a CORS-free dev browser for a locally running app, or stop and clean up the servers and worktrees it started.
license: MIT
compatibility: Requires git and python3 (3.9 or newer). Chrome or Chromium is needed only for the dev-mode browser. macOS and Linux are tested; Windows is not.
---

# parallel-branches

One script, `scripts/pb.py`, manages everything. It uses only the Python standard library. In the examples below, `PB` is shorthand for `python3 <this skill's directory>/scripts/pb.py`. When you run it, write the full path out in every command and do not put it in a shell variable: approval rules match the literal command text, so `python3 /full/path/pb.py up ...` can be pre-approved and `python3 $PB up ...` cannot. Run it from inside the user's repository, or pass `--repo <path>`.

## What it does

For each branch it creates a git worktree, copies or installs dependencies, picks a free port, starts the dev server detached, waits until the server answers, and records everything in `.git/parallel-branches/`. It can also open a separate Chrome profile with web security off for apps that hit CORS errors.

## Workflow

1. **Plan first, then stop and ask.** Run `PB up <branch> [<branch>...] --dry-run --json`. It shows, per branch, the detected stack, the exact start command, the install plan and the port. Show these to the user, and say that a repository can supply its own commands through `.parallel-branches.json`, so the commands deserve a read. Then end your turn with the question. Do not run `PB up` without `--dry-run` in the same turn. A request like "get these running" is not approval of the commands; only a reply to your plan, or a message that explicitly says to go ahead with whatever plan you propose, is.
2. **Start.** Run `PB up <branch> [<branch>...]`. Add `--cmd '<command with {PORT}>'` when detection found nothing or the user wants a specific command. Add `--create` only if the user wants a new branch made from HEAD.
3. **Report.** Give the user one URL per branch. Quote any `WARNING` lines. Say how many servers are running; more than three or four will strain a laptop.
4. **CORS.** If the output says `CORS risk found`, or the user reports CORS errors, run `PB browser <branch>`. It opens a throwaway Chrome profile with web security off. Tell the user that web security is off in that window and that it only opens the app's own localhost URL.
5. **Check later.** `PB status` lists branches, health and dev browsers. `PB status --json` is for parsing.
6. **Finish.** Ask before `PB down <branch>` or `PB down --all`. Ask again, separately, before `PB clean --yes`. Plain `PB clean` is a dry run and is safe.

## Branches with several services

Some branches need more than one process, such as a web front end plus an API. The repository describes them in `.parallel-branches.json` under `services`; see `references/stacks.md`. Then:

- `PB up --dry-run` lists every service with its own port and the exact command, in start order. Show all of them.
- A service starts only after the services it `depends_on` answer. If one fails, the ones already started are stopped again.
- `{url.NAME}` and `{ports.NAME}` in a command or env value become the other service's address, so the web app can find its API.
- The primary service (marked `primary`, otherwise the last listed) is the one whose URL you give the user. Mention the others too.
- `PB status` shows one row per service, as `branch:service`. `PB down` stops all of them.
- A service that calls another one on a different port is a CORS risk. Treat the `cross-service` warning the same way as any other CORS risk and offer `PB browser`.
- Passing `--cmd` ignores `services` and runs that single command.

## Commands

| Command | Purpose |
|---|---|
| `PB up BRANCH... [--cmd CMD] [--dry-run] [--no-install] [--browser] [--json]` | Create worktrees, install, start, health-check |
| `PB status [--json]` | Show branches, URLs, health, dev-browser endpoints |
| `PB browser BRANCH [--path /x] [--headless]` | Open a dev-mode browser for a running branch |
| `PB down BRANCH...` or `--all` | Stop only the processes this tool started, and close their dev browsers |
| `PB clean [--yes] [--force]` | Remove worktrees this tool created. Dry run without `--yes` |
| `PB detect [PATH]` | Show the detected stack, config and install plan |

`{PORT}` and `{HMR_PORT}` in `--cmd` are replaced, and both are also exported as environment variables. `BROWSER=none` is always set so dev servers do not open a browser by themselves.

## Reading the results

- **`FAILED: ... exited with code N`**: the log tail is printed. Fix the cause or pass `--cmd`.
- **Low confidence stack** (Create React App, Go, an unknown Node project): only the `PORT` variable is set. If the server ignores it, the health check times out. Find the right port flag and pass `--cmd`.
- **`port N is answered by another process`**: something else owns the port. Use `--no-verify-owner` only for servers that daemonize on purpose, such as Docker.
- **`WARNING file:line url [other-local-port]`**: the app has a hardcoded URL that will not match the new port. A dev-mode browser does not fix lines marked auth/redirect: OAuth and OIDC redirect URIs, `Origin` checks and shared cookies are server-side or cookie problems. Tell the user plainly.
- **More than one stack found**: `detect` lists `alternatives`. Ask which one to run.

## Rules

- Never start servers (`PB up` without `--dry-run`) before the user has approved the plan you showed. The commands come from the repository and run in a shell on their machine.
- Never kill a process this tool did not start. `down` checks a recorded start time before killing, and refuses if the PID was reused.
- Never open the user's real browser profile with web security off. Only `PB browser` does this, and it always uses an empty throwaway profile.
- Do not use `clean --force` without telling the user it removes uncommitted work.
- Do not edit tracked files to change ports. Pass the port through the command or environment.

## Attaching a browser agent

`PB browser` prints a DevTools endpoint such as `http://127.0.0.1:9222`. A Playwright-based agent can attach to it over CDP. The Claude in Chrome extension drives the user's own profile and cannot use this endpoint. Playwright MCP takes its endpoint when it starts, so attaching it to this browser means starting it with `--cdp-endpoint` (not tested here).

## More detail

- `references/stacks.md`: supported stacks, install rules and the `.parallel-branches.json` format.
- `references/browser-dev-mode.md`: what the dev-mode browser fixes and what it cannot.
- `references/safety.md`: what the tool touches and what to be careful about.
