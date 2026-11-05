# Changelog

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
