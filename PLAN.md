# parallel-branches: plan

## Context
Developer works on two tasks, each on its own branch. Today they can't run both branches' dev servers at once and compare them in the browser. Goal: a generic, stack-agnostic, open-source skill that creates a worktree per branch, installs/shares dependencies, allocates ports, starts each dev server, health-checks it, and reports URLs. Nothing is built yet.

## Research findings (condensed)
**Prior art is crowded.** Worktrunk (~11k stars, hooks + hash_port + copy-ignored), Portless (Vercel Labs, ~12.8k, port + proxy), Conductor (closed Mac app), Wirasm/worktree-manager-skill (Claude skill, port pool 8100-8199, 138 stars), git-worktree-runner, wtp, lazyworktree, Claude Code built-in `--worktree`/`.worktreeinclude`, superpowers `using-git-worktrees` (creates worktree + installs deps, NO dev server/ports/env/cleanup).
**Gap a new skill can own:** zero-config, stack-aware discovery for an unknown repo; multi-port/federated setups (host + remotes); warning about hardcoded ports / OAuth / CORS; one lifecycle (up/status/down/clean) with health check and a status table; safe cleanup.
**Cross-stack:** `PORT` env is NOT universal (honored by CRA, Next, Rails, Nuxt; Angular per docs - unverified). Vite, Astro, webpack, Django, Flask, uvicorn, Go need flag/other env. So: per-stack command template + always export `PORT` + user override.
**Hard problems:** HMR websocket ports; check-then-bind port race; cookies ignore ports (localStorage doesn't); OAuth redirect URIs/CORS allowlists pinned to one port (RFC 8252 allows loopback any-port, Auth0 doesn't, Keycloak unclear); `npm run` child processes orphaned when only parent killed; macOS AirPlay holds 5000/7000; RAM per dev server (Vite ~0.1-0.5GB, Next/Turbopack 1-7GB) so cap ~3-4.
**Packaging:** Agent Skills open standard (SKILL.md; name <=64 chars lowercase-hyphen = dir name; description <=1024; body <500 lines; scripts/ references/ assets/). Works in Claude Code, Codex, Gemini CLI, Cursor, Copilot, opencode (secondary source). Claude Code distribution via `.claude-plugin/marketplace.json` + `plugin.json`. Avoid "claude"/"anthropic" in skill name.

## Recommended design
Name: `parallel-branches` (working name; avoid "claude").

**Commands (user-invoked workflow):** `up <branch...>`, `status`, `down <branch>`, `clean`.

**Flow for `up`:** detect stack -> print plan (dry-run by default, ask once) -> `git worktree add` -> deps -> env files -> allocate ports -> start detached -> poll health URL -> print table (branch, URL, PID, log path).

**One bundled script** `scripts/pb.py` (Python 3 stdlib only, subcommands up/status/down/clean; macOS+Linux, Windows best-effort). SKILL.md holds judgment/workflow; script holds deterministic work.

**Stack detection** (`references/stacks.md`): marker file -> default dev command -> port mechanism -> dep strategy. Covers Node (npm/pnpm/yarn: Vite, Next, CRA, Angular, Nuxt, Astro, SvelteKit, webpack), Python (Django, Flask, uvicorn), Rails, Spring Boot, Go, Docker Compose. Unknown stack -> ask user for the command.

**Optional repo config** `.parallel-branches.json`: custom command, port flag template, `services[]` for multi-port setups (e.g. host + remote with `{ports.remote}` templating), files to copy, env overrides, health URL.

**Ports:** hash(branch) -> preferred port in 4000-4999 (skip 5000/7000), linear probe if taken, persist in registry; reserve base+0 app, base+1 HMR; use strict-port flags where supported so failures are loud.

**Dependencies (ladder):** pnpm -> per-worktree install (hard-linked store). npm/yarn -> if lockfile hash equals main checkout, reflink/clone copy of node_modules (`cp -c` macOS, `--reflink=auto` Linux), else install. Python -> per-worktree venv (uv if present). Cargo -> per-worktree target. Never symlink node_modules by default (git status noise + divergence).

**Env:** copy gitignored env files (honor `.worktreeinclude`), write `.env.local` overlay; never edit tracked files.

**Cross-reference check:** grep for hardcoded `localhost:<port>` in config/env; WARN about OAuth redirect/CORS/remote URLs; never silently rewrite tracked files.

**State:** registry JSON + logs under `$(git rev-parse --git-common-dir)/parallel-branches/`.

**Safety:** kill only PIDs the skill started (process-group kill, `setsid`); refuse worktree removal when dirty without confirmation; `--dry-run` and confirm before destroy; narrow `allowed-tools` (`Bash(${CLAUDE_SKILL_DIR}/scripts/pb.py *)`); `disable-model-invocation: true` for down/clean; warn at >3 concurrent servers.

## Browser dev mode (CORS bypass)
Requirement: when the skill opens a browser or a browser agent for a branch and there is CORS risk, it must use a dev-mode browser with web security off.

**When it triggers (generic):** `pb.py up` detects that the app on `localhost:<port>` calls API origins that differ from its own (hardcoded `localhost:<other>` or remote hosts in env/config), or the user asks. Otherwise a normal browser is used.

**How:** new `pb.py browser <branch>` launches a separate Chrome/Chromium with a throwaway profile dir (per branch, under the state dir) and `--disable-web-security --disable-site-isolation-trials`, plus `--remote-debugging-port=<port>`. Browser agents attach over CDP, or the skill writes a Playwright MCP config with `browser.launchOptions.args`. Built into the skill's `up` output: it prints the dev-mode URL for each branch.

**Findings (web search, partly unverified):**
- Chrome ignores `--disable-web-security` unless a non-default `--user-data-dir` is also set; modern Chrome also needs `--disable-site-isolation-trials`. Sources are 2016-2022 posts, so behavior on current Chrome must be tested in Phase 0.
- Playwright MCP takes `--config <file>` with `browser.launchOptions.args`. The `contextOptions.bypassCSP` key name is unverified. A running MCP server cannot be reconfigured live, so the skill must launch its own browser and attach, or generate a config for the next MCP start.
- The Claude-in-Chrome extension drives the user's real Chrome profile; flags cannot be applied to it, so it is not used for dev mode.

**What this does NOT fix (be honest in docs):** OAuth/OIDC `redirect_uri` mismatch (server-side rejection, not CORS), servers that reject on `Origin`/CSRF checks, SameSite/cookie rules, and cookies shared across localhost ports.

**Safety:** dedicated throwaway profile only, never the user's real profile; visible warning that web security is off; restrict use to localhost/dev URLs; close the instance on `down`; log that dev mode is active.

## Repo layout (open source)
```
parallel-branches/
  README.md  LICENSE  CONTRIBUTING.md  CHANGELOG.md  AGENTS.md
  .claude-plugin/{marketplace.json,plugin.json}
  skills/parallel-branches/
    SKILL.md
    scripts/pb.py
    references/{stacks.md,ports-and-env.md,safety.md}
    assets/parallel-branches.example.json
  tests/{scripts/ (pytest), evals/ (>=3 scenarios + no-skill baseline)}
  .github/ (CI: skills-ref validate, claude plugin validate, ruff, pytest)
```
Install: Claude Code `/plugin marketplace add <owner>/<repo>`; other agents copy `skills/parallel-branches` into `.agents/skills/`.

## Phases
0. **Done** (see docs/phase0-results.md). Generic validation (no coupling to any one framework or auth provider):** build tiny throwaway sample apps in `tests/fixtures/` (e.g. Vite, Flask, Express, plus one multi-port host+remote pair) and verify per-stack port mechanisms from the research table. Auth/CORS: CORS handled by the dev-mode browser; non-CORS auth failures stay a generic "hardcoded port/redirect" warning, never provider-specific logic. Also verify the dev-mode Chrome flags work on the current Chrome version, and that a Playwright-driven agent can attach (CDP) to it.
1. `pb.py` core: worktree, ports, detached start/stop, registry, health check, tests. **Done** (see Phase 1 status below).
2. SKILL.md + references; stack detection for top ~8 stacks. **Done** (see Phase 2 status below).
3. Evals (3+ scenarios, test on Haiku/Sonnet/Opus) and script tests. **Built; real-model runs not done yet** (see Phase 3 status below).
4. Package (plugin/marketplace, README with demo GIF), license, CI, publish.

## Pros
- Real pain, broad audience (any multi-branch dev, plus AI agents running parallel tasks).
- Fills specific gap: zero-config + multi-port + warnings + status/cleanup in one skill.
- Portable standard (SKILL.md), low maintenance (stdlib Python).

## Cons / risks
- Crowded space: Worktrunk + Portless cover much of it; need clear differentiation or reuse (e.g. delegate to `wt` if installed).
- Auth/CORS pinned to one port cannot be fixed client-side; skill can only warn.
- "Stack-agnostic" means a long tail of stacks; maintenance burden grows.
- Resource ceiling: ~3-4 servers on a laptop.
- Windows support weaker.
- Cross-agent claims partly from secondary sources; need testing per agent.

## Verification (when built)
- pytest for port allocation, registry, kill-group, dirty-guard.
- Manual: two branches of a Vite app and an Angular app running at once on different ports; `status` shows both; `down` leaves no orphan (`lsof -nP -iTCP -sTCP:LISTEN`).
- `skills-ref validate`, `claude plugin validate .`, evals pass on three models.

## Decisions
- Positioning: standalone, zero-config (no dependency on Worktrunk/Portless).
- Script: Python 3 stdlib only.
- License: MIT.
- Scope: platform-agnostic across frameworks, languages and repos. No framework-, auth-provider- or company-specific code or defaults.
- Name still working title `parallel-branches` (not yet confirmed).

## Phase 1 status
Implemented in `skills/parallel-branches/scripts/pb.py` (Python 3.9+, stdlib only): `up`, `status`, `down`, `clean`.
- The dev command comes from `--cmd` with `{PORT}` and `{HMR_PORT}` placeholders (both also exported as env vars). Stack detection is Phase 2.
- Tests use `unittest` instead of pytest, to keep the project dependency-free: `python3 -m unittest discover tests/scripts` (20 tests).
- Safety implemented: process-group kill of only the processes we started, a start-time token to avoid killing a reused PID, owner-verified health check, dirty-worktree guard, `clean` is a dry run without `--yes`.

Not yet done (Phase 2 or later): dependency install/sharing, env-file copying, stack auto-detection, `.parallel-branches.json` config, `browser` command (dev-mode Chrome), hardcoded-port warnings, retry on a lost port race, Windows support.

## Phase 2 status
Added: stack detection for 15 stack variants (Node frameworks, Django, Flask, FastAPI, Rails, Spring Boot, Go), dependency handling (clone `node_modules` on an identical lockfile, otherwise install), env-file copying, `.parallel-branches.json`, a hardcoded-URL and CORS-risk scan, the `detect` and `browser` commands, `up --browser`, the real `SKILL.md` and `references/`. 66 tests.
Verified by real runs: headed and headless dev-mode Chrome 156 bypasses CORS on a host-plus-remote fixture, an agent attaches over CDP with `playwright-core`, and `down` closes the browser and deletes its profile.

Still open: multi-service setups (one branch needing a host plus remotes started together), a retry when a port is lost in a race, evals, CI, Windows, attaching through Playwright MCP itself, and trying the detection rules on real projects beyond the fixtures.

## Phase 3 status
Added: `tests/validate_skill.py` (Agent Skills rules and manifest checks), four eval scenarios with deterministic graders (`tests/evals/`), a runner for the Claude Code CLI that refuses to spend money without `--yes`, scripted-agent tests proving every scenario is satisfiable and that careless agents fail, a GitHub Actions workflow (Ubuntu and macOS, Python 3.9 and 3.12, plus ruff and shellcheck), `PB_CHROME_ARGS`, CONTRIBUTING, CHANGELOG and AGENTS.md. 81 tests.

Not done: running the evals against Haiku, Sonnet and Opus (needs approval to spend money), the `stream-json` transcript shape is assumed from docs and unconfirmed against a real transcript, and CI has not run yet on hosted runners.
