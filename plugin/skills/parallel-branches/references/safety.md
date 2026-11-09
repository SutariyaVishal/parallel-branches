# Safety

## What the tool touches

- Creates git worktrees next to the repository, in `<repo>.worktrees/` by default.
- Writes state and logs under the repository's git common directory: `.git/parallel-branches/`.
- Starts dev servers and, when asked, installs dependencies and opens a dev-mode browser.
- Copies git-ignored env files from the main checkout into new worktrees.

It does not edit tracked files, change git config, push, or touch the network except through the commands it runs for you.

## Guard rails

| Risk | Guard |
|---|---|
| Killing the wrong process | Only process groups it started. A recorded start time must still match, or `down` refuses. |
| Port answered by something else | The health check requires that the process listening on the port belongs to the group it started (needs `lsof`). |
| Losing work | `clean` is a dry run without `--yes`, skips running servers, skips worktrees with uncommitted changes unless `--force`, and never removes a worktree it did not create. |
| Two runs at once | State is written under a file lock. |
| Option injection | Branch names starting with `-` are rejected, and git is called with argument lists, never through a shell. |

## Environment and credentials

Dev servers, install commands, hooks and the dev-mode browser inherit the environment of the shell that ran `pb`, so they see any variable you have set, such as `NPM_TOKEN`. `pb` does not read, store or send credentials itself. It reads only its own `PB_*` variables and the config-directory variables `XDG_CONFIG_HOME` and `APPDATA`, and its health checks go to `localhost`. It does copy git-ignored `.env` files into new worktrees, and `up --dry-run` lists them first.

## Commands from a repository

`cmd`, `install` and `env` in `.parallel-branches.json`, and the `scripts` in `package.json`, run in a shell on the user's machine. A repository you do not trust can use them to run anything. Always show these commands (`up --dry-run`) and get a yes before the first run in an unfamiliar repository.

## Known limits

- `python -m http.server` can take over 30 seconds to open its port on hosts where reverse DNS is slow (measured: 35 s on a GitHub macOS runner), because its bind does a `getfqdn()` lookup. The process looks alive while the port is closed, so `pb up` reports `timed out ... nothing answering on port N`. Raise `--timeout`, or use a server that does not look up its own name.
- Windows: stopping a service force-kills its whole process tree (no graceful signal), and the check that our process owns the port is skipped. The tests that need a POSIX shell are skipped there.
- A server that daemonizes escapes its process group. Use `--no-verify-owner`, and stop it yourself.
- If another program takes a port between the free-port check and the server's bind, the start fails and is reported. It is not retried.
- Each dev server costs real memory. Three or four at once is a sensible ceiling on a laptop.
