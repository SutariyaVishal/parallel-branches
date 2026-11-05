# parallel-branches

> **Status: early development (v0.3.0).** Core commands, stack detection, dependency handling and the dev-mode browser work and are tested on macOS. Windows is untested. The repository is private for now. See [PLAN.md](PLAN.md) for the design.

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

## Commands

| Command | Purpose |
|---|---|
| `up <branch...>` | Create worktrees, install dependencies, start servers, print URLs |
| `status` | List branches, ports, URLs, PIDs and health |
| `browser <branch>` | Open a dev-mode browser (web security off, throwaway profile) |
| `down <branch>` | Stop that branch's servers |
| `clean` | Remove worktrees this tool created (dry run unless `--yes`) |
| `detect` | Show the detected stack and install plan |

## Limits

A dev-mode browser fixes CORS only. It does not fix OAuth or OIDC redirect URI mismatches, servers that reject on `Origin`, or cookies shared across localhost ports. The skill warns about these.

## Install

Claude Code (needs access to the private repo until it is made public):

```
/plugin install parallel-branches --marketplace SutariyaVishal/parallel-branches
```

Other agents: copy `skills/parallel-branches` into `.agents/skills/`. Requires git and Python 3.9+.

## Try it

```
python3 skills/parallel-branches/scripts/pb.py up feature-a feature-b --dry-run   # plan: stack, command, ports
python3 skills/parallel-branches/scripts/pb.py up feature-a feature-b             # add --cmd '...{PORT}...' if detection finds nothing
python3 skills/parallel-branches/scripts/pb.py status
python3 skills/parallel-branches/scripts/pb.py browser feature-a                  # only if you hit CORS errors
python3 skills/parallel-branches/scripts/pb.py down --all
python3 skills/parallel-branches/scripts/pb.py clean --yes
```

Run the tests with `python3 -m unittest discover tests/scripts`.

## Testing

```
python3 tests/validate_skill.py
python3 -m unittest discover tests/scripts
python3 tests/evals/run_evals.py --dry-run   # evals against real models; needs --yes to spend money
```

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License
MIT. See [LICENSE](LICENSE).
