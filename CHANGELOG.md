# Changelog

## 0.13.0
- Concurrent hook commands: a step can be an object of named commands, alone or inside a list (`["createdb {db_name}", {"deps": "npm ci", "assets": "make assets"}, "npm run migrate"]`). They start together; the step waits for all of them, fails if any failed or passed `hook_timeout` (one deadline per step), and the list stops there. Output is logged per command (`$ [deps] ...`). Dry runs and `pb hooks` show `together: [deps] ... [assets] ...`. Strings and lists work as before.

## 0.12.0
- Background `post_start`: with `"background_hooks": ["post_start"]` in `.parallel-branches.json`, `up` returns as soon as the servers are healthy and the hook runs in a detached process. The result is recorded when it ends (`pb hooks`: `running in the background`, then `last run ok` or `FAILED`), `down` stops a run that is still going, `pb hook post_start` refuses a second run meanwhile, and the dry run shows `(background)`. Other events cannot be listed.

## 0.11.0
- Warning when copied env files point every branch at the same database (#9, thanks @sunxiuguo). `up` and `up --dry-run` name the file and variable, never the value. It looks for `DATABASE_URL`, `DB_URL`, `MONGO_URI`, `MONGODB_URI`, `REDIS_URL`, database-name keys (`DB_NAME`, `DB_DATABASE`, `PGDATABASE`, `POSTGRES_DB`, `MYSQL_DATABASE`) and database URL schemes, including SQLAlchemy `+driver` and `jdbc:` forms. It reads `.env`, `.env.*`, `*.env` and `.envrc` files, including those inside copied directories, without following symlinks. A real `up` checks after `post_create`, so a hook that removes the setting clears the warning. `references/stacks.md` has a "Shared state" section with the hooks workaround.
- Copied directories that are dependency or build/cache folders (`node_modules`, `.venv`, `dist` and the rest of the usual list) are not walked for that warning, so packages' own env files cause no false warnings (#11). An env file listed by itself is still checked.
- `copied_env` is kept when a branch is stopped and started again (it used to reset to an empty list).
- Evals: scenario `shared-database-warning` and grader `final_not_mentions`, ten scenarios in all.

## 0.10.0
- Copying git-ignored files now also copies git-ignored directories named in `.worktreeinclude` or the config `copy` list (`.cache/`, `out/`), cloned copy-on-write where possible, so warm build caches carry over. A directory git would track is never copied; nothing in the worktree is overwritten.
- `up --dry-run` lists the ignored files and directories a new worktree would get (`would_copy` in the JSON).

## 0.9.0
- New command `pb hooks [BRANCH] [-v] [--json]`: shows the hooks stored for a tracked branch (all tracked branches without a name) with values filled in, `(user)` marks, and the last result of each event. `-v` adds every value a hook can use (`db_name`, `PORT`, `url.NAME` and the rest). Read only: no lock, nothing written.

## 0.8.0
- User-level hooks: a per-user file (`$PB_USER_CONFIG`, else `~/.config/parallel-branches/config.json`, `%APPDATA%` on Windows) with `hooks` and `hook_timeout`. Its hooks run in every repository, before the repository's own hooks for the same event, and are marked `(user)` in `up --dry-run`, `clean` and `pb hook --dry-run` (and as `sources` in the JSON). They are stored with the branch at `up` like the others, so teardown runs what was shown. `--no-hooks` skips both sets. A bad user config stops `up` with exit code 2 before any worktree exists. Example in `assets/parallel-branches.user.example.json`.
- Tests and evals no longer read a real user config (`PB_USER_CONFIG` is switched off for them).

## 0.7.0
- New hook `pre_remove`: runs in `clean --yes` inside the worktree, just before it is removed (database dumps, saving local data). Unlike the other teardown hooks it can stop the removal: if it fails, that worktree is kept and `clean` says so. `clean --no-hooks` removes it anyway. `clean` without `--yes` lists it before `post_remove`.
- Evals: two scenarios for `pre_remove` (`hooks-clean-confirm`, `hooks-clean-run`), nine in all, with graders `no_clean_yes`, `dry_run_before_clean`, `state_worktree_gone` and `state_repo_file`.
- New command `pb hook EVENT BRANCH [--dry-run] [--hook-timeout N]`: runs one stored hook of a tracked branch by hand, for example to seed a database again. It runs the commands stored when the branch started, with the ports it has now. `--dry-run` prints them with values filled in and takes no lock. Exit code 1 when the hook fails.

## 0.6.0
- Lifecycle hooks: `hooks` in `.parallel-branches.json` with `post_create`, `pre_start`, `post_start`, `pre_stop` and `post_remove`. Commands may use `{branch}`, `{db_name}`, `{PORT}`, `{url.NAME}` and more (shell-quoted), and get the same values as `PB_*` environment variables. `post_create` and `pre_start` failures stop `up`; the others only report. Per-command timeout (`hook_timeout`, `up --hook-timeout`), one log per event, `--no-hooks` on `up`, `down` and `clean`.
- `up --dry-run` prints every hook with its values filled in. `clean` without `--yes` lists the `post_remove` commands. `down` and `clean` run the commands stored when the branch started, not the current config.
- Install now finishes for every service before the first one starts, so `pre_start` sits between the two.
- `SKILL.md`: after opening the dev-mode browser, say what it does not fix (found by the evals: Sonnet skipped it once).
- Evals: two hooks scenarios (`hooks-confirm-first`, `hooks-run`) and a `state_hook_ok` check, seven scenarios in all.
- Docs: Playwright MCP attached with `--cdp-endpoint` is verified (0.0.84). Demo GIF. `CODEOWNERS` and branch and tag rulesets, to apply once the repo is public.

## 0.5.4
- Docs for a public release: rewritten README (real example session, comparison with other tools, limits), `docs/DESIGN.md` replaces the working plan, `SECURITY.md`, issue and pull-request templates.

## 0.5.3
- Fix (Windows): lockfile comparison ignores line endings. Git for Windows commits a lockfile with LF and checks it out with CRLF, so the dry run saw identical lockfiles as different and planned a reinstall instead of a `node_modules` clone.

## 0.5.2
- Fix: `up --dry-run` created `.git/parallel-branches/` (a lock file and an empty state file). A dry run now touches nothing.
- Dry run now shows the hardcoded-URL and CORS warnings, and says whether `node_modules` would be cloned or installed, by comparing the lockfile committed on the branch with the main checkout's.
- The risk scan drops duplicate hits, lists what a dev-mode browser cannot fix (OAuth/OIDC redirects) first, and keeps up to 40 hits so one file cannot hide the others. Text output groups plain API hosts per file.

## 0.5.1
- Windows support: process and lock handling moved to `procs.py` (`taskkill /T`, `msvcrt`); tests run on `windows-latest`. Stopping a service there is a forced kill of its process tree, and the port-owner check is skipped.
- Fix: the install note claimed a `node_modules` clone after the clone failed and an install ran instead.
- Fix: the dev-mode browser profile is now deleted with retries (Windows keeps files locked briefly).
- Fix: `{PY}` is quoted when the project path has spaces, and the Python install command no longer hardcodes `.venv/bin`.
- CI: Ubuntu, Windows and macOS (own workflow). macOS tests had been timing out because the test servers used `python -m http.server`, whose bind took 35 s there; they now use `tests/helpers/serve.py`.

## 0.5.0
- `restart BRANCH [SERVICE]`: restart one service or all of them on the same ports.
- Monorepo detection: `init` suggests a `services` config from workspaces (npm, yarn, pnpm, lerna) or conventional folders, `detect` reports it, and `up` points at `init` instead of failing blindly.

## 0.4.0
- Multi-service branches: `services` in `.parallel-branches.json` runs several processes per branch (for example an API and a web app) with their own ports, `depends_on` ordering, `{url.NAME}` and `{ports.NAME}` templating, and `http` or `tcp` health checks. `status` shows one row per service, `down` stops all of them, and a failed start stops the services already started.
- Fix: stopping a service that this same `pb` run had started crashed on macOS (`EPERM` on a zombie process group) or waited out the full grace period. Own children are now reaped.
- Fix: a service whose `cwd` was omitted crashed with `KeyError`.
- Timeouts now say `timed out after Ns (last health detail)`.
- Evals: a `multi-service` scenario.

## 0.3.1
- Skill: `SKILL.md` now says to write the full `pb.py` path (no shell variables) so approval rules can match, and states plainly that servers must not start before the user approves the plan. The description also covers stopping and cleaning up.
- Evals: `--repeat`, cost and skill-use reporting. First real-model results: see docs/DESIGN.md.

## 0.3.0
- Evals: four scenarios with deterministic graders, a runner for the Claude Code CLI, and scripted-agent tests.
- `tests/validate_skill.py` checks the skill and plugin manifests against the Agent Skills rules.
- GitHub Actions: tests on Ubuntu and macOS with Python 3.9 and 3.12, plus lint.
- `PB_CHROME_ARGS` adds extra flags to the dev-mode browser (for sandboxed runners).

## 0.2.0
- Stack detection for Node frameworks, Django, Flask, FastAPI, Rails, Spring Boot and Go.
- Dependency handling: clone `node_modules` when the lockfile matches, otherwise install.
- Env-file copying, `.parallel-branches.json`, hardcoded-URL and CORS-risk warnings.
- `detect` and `browser` commands, `up --browser`, the real `SKILL.md` and references.

## 0.1.0
- `up`, `status`, `down` and `clean` with worktrees, port allocation, process-group handling and health checks.
