# Changelog

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
- Evals: `--repeat`, cost and skill-use reporting. First real-model results: see PLAN.md.

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
