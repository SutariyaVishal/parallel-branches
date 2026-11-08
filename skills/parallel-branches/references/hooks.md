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

## User-level hooks

A per-user file holds hooks that apply in every repository, for example a log line or a notification when a branch is up. It is a separate file from the repository's config. Copy `assets/parallel-branches.user.example.json` to start.

- **Location:** `$PB_USER_CONFIG` if set (an empty value switches the file off), otherwise `$XDG_CONFIG_HOME/parallel-branches/config.json` (`~/.config/...` by default) on macOS and Linux, and `%APPDATA%\parallel-branches\config.json` on Windows.
- **Contents:** `hooks` and `hook_timeout`, in the same format as the repository config. Any other key is an error, so a `services` block put there by mistake is not silently ignored.
- **Order:** for each event the user's commands run first, then the repository's, as one list. The first failing command ends the list, so a failing user `pre_start` stops `up` before the repository's `pre_start` runs.
- **Labels:** `up --dry-run`, `clean` without `--yes` and `pb hook --dry-run` mark each user command with `(user)`. The JSON rows carry a `sources` list.
- **Stored like the rest:** the merged commands are stored with the branch at `up`, so `down` and `clean` run what was shown even if either file changes later. `--no-hooks` skips both sets.
- **Timeout:** `--hook-timeout`, then the repository's `hook_timeout`, then the user's, then 120.
- **Only `up` reads the file.** A file that is not valid JSON, or has an unknown key or event, stops `up` with exit code 2 before any worktree exists, and the message names the file.
- **Keep it generic.** Names like `{url.api}` must exist in the repository the hook runs in, or `up` stops with an error that names the user file. Put hooks that need a particular service in that repository's config.

The file is yours and is not part of any repository, so nothing a repository ships can edit it. But it runs everywhere, including in repositories you do not know, so keep to commands you would be happy to run in any of them.

## Trust

Hook commands run in a shell on your machine, like a start command. So:

- `up --dry-run` prints every hook with its values filled in. Read them before approving.
- The commands are stored with the branch when it starts. `down` and `clean` run those stored commands, not whatever the config file says by then, so a different checkout cannot swap in new teardown commands after you approved the old ones.
- `clean` without `--yes` prints the `pre_remove` and `post_remove` commands it would run.
- `--no-hooks` skips them (on `up` it also stores none, so nothing runs at teardown). Pass it to `down` and `clean` to skip stored ones.

## Running one by hand

`pb hook EVENT BRANCH` runs the stored commands of one event for a tracked branch, with the ports and names the branch has now, and prints `hook EVENT ok` or the failure. Use it to seed a database again or to try a hook without stopping and starting everything. `--dry-run` prints the commands with values filled in and runs nothing (no lock, no state written). `--hook-timeout N` applies to that run only. The exit code is 1 when the hook fails and 2 for an untracked branch. A hook the branch did not store (for example one added to the config after `up`) is not run: start the branch again so the dry run shows it and it is stored. It works on a branch that is stopped, and `post_remove` runs in the main checkout as it does in `clean`.

## Seeing what is stored

`pb hooks [BRANCH]` lists the hooks stored for a tracked branch (every tracked branch without a name): each event, its commands with values filled in, `(user)` on those from the user config, and the last result (`ok`, `FAILED` with the log path, or `never run`). `-v` also prints every value a hook can use (`branch`, `branch_slug`, `db_name`, `worktree`, `repo`, `PORT`, `HMR_PORT`, `URL`, `ports.NAME`, `url.NAME`); they are also the `PB_*` environment variables. `--json` gives the same with the unfilled templates. It takes no lock and writes nothing.

## Timeouts and logs

Each command gets `hook_timeout` seconds (default 120, at most 3600). `up --hook-timeout N` overrides it, and the value is stored for teardown. A command that runs longer has its whole process group killed.

Output goes to `.git/parallel-branches/logs/<branch_slug>.hook-<event>.log`. `status --json` shows the last result of each event under `hook_runs`. Text `status` does not; use `pb hooks` for that.

## Limits

- Commands in one list run one after another. There is no concurrent form.
- A hook that starts a background process and leaves it running is not tracked or stopped by `down`. Use a `services` entry for processes that should live as long as the branch.
- Removing a worktree with `git worktree remove` instead of `pb clean` runs no hooks, so `pre_remove` and `post_remove` never fire.
- Hooks come from the repository config and the one per-user file. There is no other level, and no per-directory or per-organisation config.
- A config committed on the branch can only be read once its worktree exists. A mistake in `hooks` there is reported after the worktree was created. `--dry-run` reads the main checkout's config and catches it first.
