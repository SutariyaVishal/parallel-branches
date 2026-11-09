# parallel-branches

Run several git branches side by side. For each branch it creates a git worktree, copies or installs
dependencies, picks a free port, starts the dev server detached, waits until the server answers, and prints
one URL per branch. It can also open a separate Chrome profile with web security off for apps that hit CORS
errors, and run your own hooks (for example one database per branch).

## What this plugin runs

This plugin contains only the skill instructions. It has no scripts. When the skill is used, the agent runs
the `pb` command-line tool from the `parallel-branches` package on PyPI, pinned to version 0.14.0, through
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
