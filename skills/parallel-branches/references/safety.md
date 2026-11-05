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

## Commands from a repository

`cmd`, `install` and `env` in `.parallel-branches.json`, and the `scripts` in `package.json`, run in a shell on the user's machine. A repository you do not trust can use them to run anything. Always show these commands (`up --dry-run`) and get a yes before the first run in an unfamiliar repository.

## Known limits

- Windows is untested. Process-group handling and `lsof` are POSIX.
- A server that daemonizes escapes its process group. Use `--no-verify-owner`, and stop it yourself.
- If another program takes a port between the free-port check and the server's bind, the start fails and is reported. It is not retried.
- Each dev server costs real memory. Three or four at once is a sensible ceiling on a laptop.
