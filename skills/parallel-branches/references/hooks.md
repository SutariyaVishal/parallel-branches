# Hooks

Hooks are shell commands that run at fixed points in a branch's life. Typical uses: create a database for the branch, seed it once the API is up, drop it when the worktree is removed.

They go under `hooks` in `.parallel-branches.json`:

```json
{
  "hooks": {
    "post_create": "createdb {db_name}",
    "pre_start": ["npm run migrate"],
    "post_start": "curl -fsS -X POST {url.api}/seed",
    "pre_stop": "pg_dump {db_name} > ../{branch_slug}.sql",
    "pre_remove": "git status --short > ../{branch_slug}.leftovers",
    "post_remove": "dropdb --if-exists {db_name}"
  },
  "hook_timeout": 120
}
```

Each event takes one command string or a list of strings. A list runs in order and stops at the first failure.

## Events

| Event | Runs | Working directory | If it fails |
|---|---|---|---|
| `post_create` | Once, when `up` has just created the worktree and copied env files, before dependencies are installed | the worktree | `up` stops, nothing starts |
| `pre_start` | On every `up`, after install and before any service starts | the worktree | `up` stops, nothing starts |
| `post_start` | After every service answered its health check | the worktree | Reported as a warning. The servers stay up |
| `pre_stop` | In `down`, while the services are still running | the worktree | Reported. The services are stopped anyway |
| `pre_remove` | In `clean --yes`, after the dirty check and just before the worktree is removed | the worktree | That worktree is kept and `clean` says so. `clean --no-hooks` removes it anyway |
| `post_remove` | In `clean --yes`, after the worktree is removed | the main checkout | Reported. The worktree stays removed |

`restart` runs no hooks. It is for picking up a code change, not for setting a branch up again.

## Variables

Substituted into the command text, and always shell-quoted, because a branch name can contain characters the shell treats specially:

| Variable | Value |
|---|---|
| `{branch}` | The branch name |
| `{branch_slug}` | The branch name made safe for file names |
| `{db_name}` | A lowercase, underscore-only name for databases and schemas, with a short hash so `feat/a` and `feat-a` differ |
| `{worktree}`, `{repo}` | Paths of the branch's worktree and of the main checkout |
| `{PORT}`, `{HMR_PORT}`, `{URL}` | Port, HMR port and `http://localhost:PORT` of the primary service |
| `{ports.NAME}`, `{url.NAME}` | Port and URL of another service, as in service commands |

Unknown `{names}` and shell variables such as `${HOME}` are left alone. A `{url.NAME}` for a service that does not exist is an error when the config is read.

The same values are exported as environment variables, which is the safest way to use a branch name: `PB_HOOK`, `PB_BRANCH`, `PB_BRANCH_SLUG`, `PB_DB_NAME`, `PB_WORKTREE`, `PB_REPO`, `PB_PORT`, `PB_HMR_PORT`, `PB_URL`, and for every named service `PB_PORT_<NAME>` and `PB_URL_<NAME>`.

The ports are the ones actually allocated, after probing for free ones. They are not a number derived from the branch name, so a hook can safely hand them to another program.

## Trust

Hook commands run in a shell on your machine, like a start command. So:

- `up --dry-run` prints every hook with its values filled in. Read them before approving.
- The commands are stored with the branch when it starts. `down` and `clean` run those stored commands, not whatever the config file says by then, so a different checkout cannot swap in new teardown commands after you approved the old ones.
- `clean` without `--yes` prints the `pre_remove` and `post_remove` commands it would run.
- `--no-hooks` skips them (on `up` it also stores none, so nothing runs at teardown). Pass it to `down` and `clean` to skip stored ones.

## Running one by hand

`pb hook EVENT BRANCH` runs the stored commands of one event for a tracked branch, with the ports and names the branch has now, and prints `hook EVENT ok` or the failure. Use it to seed a database again or to try a hook without stopping and starting everything. `--dry-run` prints the commands with values filled in and runs nothing (no lock, no state written). `--hook-timeout N` applies to that run only. The exit code is 1 when the hook fails and 2 for an untracked branch. A hook the branch did not store (for example one added to the config after `up`) is not run: start the branch again so the dry run shows it and it is stored. It works on a branch that is stopped, and `post_remove` runs in the main checkout as it does in `clean`.

## Timeouts and logs

Each command gets `hook_timeout` seconds (default 120, at most 3600). `up --hook-timeout N` overrides it, and the value is stored for teardown. A command that runs longer has its whole process group killed.

Output goes to `.git/parallel-branches/logs/<branch_slug>.hook-<event>.log`. `status --json` shows the last result of each event under `hook_runs`. Text `status` does not.

## Limits

- Commands in one list run one after another. There is no concurrent form.
- A hook that starts a background process and leaves it running is not tracked or stopped by `down`. Use a `services` entry for processes that should live as long as the branch.
- Hooks come from the repository config only. There is no user-level config file.
- A config committed on the branch can only be read once its worktree exists. A mistake in `hooks` there is reported after the worktree was created. `--dry-run` reads the main checkout's config and catches it first.
