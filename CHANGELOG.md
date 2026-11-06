# Changelog

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
