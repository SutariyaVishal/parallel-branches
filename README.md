# parallel-branches

> **Status: planning.** No working code yet. See [PLAN.md](PLAN.md) for the research and design.

An agent skill that lets you work on several git branches at the same time and run each branch's dev server side by side, so you can compare them in the browser.

It is meant to be stack-agnostic: any language, framework or repository.

## The problem

You have two tasks, each on its own branch. You cannot run both branches at once, so you stash, switch and restart to check each one in the browser.

## What it will do

- Create a git worktree per branch.
- Install or share dependencies.
- Allocate a free port per branch (and a second one for HMR when needed).
- Start each dev server detached, health-check it and print the URLs.
- Open a throwaway dev-mode browser profile with web security off when CORS is a risk (localhost only).
- Show status, stop only the processes it started, and clean up safely.

## Commands (planned)

| Command | Purpose |
|---|---|
| `up <branch...>` | Create worktrees, start servers, print URLs |
| `status` | List branches, ports, URLs, PIDs and health |
| `browser <branch>` | Open a dev-mode browser for a branch |
| `down <branch>` | Stop that branch's servers |
| `clean` | Remove worktrees that are no longer needed |

## Limits

A dev-mode browser fixes CORS only. It does not fix OAuth or OIDC redirect URI mismatches, servers that reject on `Origin`, or cookies shared across localhost ports. The skill warns about these.

## License

MIT. See [LICENSE](LICENSE).
