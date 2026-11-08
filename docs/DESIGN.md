# Design notes

How `parallel-branches` works and why, what was measured, and what is still open. Facts about other tools were gathered in November 2026 and will age.

## Why this exists

Working on two tasks means two branches, and checking both in a browser means stash, switch, restart, look, repeat. Git worktrees give each branch its own folder, but nothing installs dependencies, chooses ports, starts the servers, reports the URLs, and cleans up. The goal is that whole loop, for a repository nobody has configured, usable from an AI agent or by hand.

## Prior art

The space is crowded, so this is positioned on the whole loop and on zero configuration, not on worktree features.

- **Worktree managers:** Worktrunk (hooks, a `hash_port` template filter, copying ignored files), git-worktree-runner, gwq, wtp, lazyworktree. Good at worktrees; ports and dev servers are left to hooks you write.
- **Ports:** Portless (random port plus a local proxy).
- **Apps:** Conductor (closed source), Vibe Kanban, Crystal.
- **Agent tooling:** Claude Code's built-in worktrees (`.worktreeinclude`), obra/superpowers `using-git-worktrees` (creates the worktree and installs dependencies; no dev server, ports, env files or cleanup), and a few community skills with port pools.

What none of them did, as far as I found: read an unknown repository, work out how its dev server takes a port, wire up several services that must find each other, health-check them, warn about what will break on a new port, and stop and clean up safely, all in one tool.

## What it does, as built

**One script, `pb.py`** (Python 3.9+, standard library only), with `SKILL.md` holding the judgment (when to ask, what to show) and the script holding the deterministic work. `stacks.py` detects stacks and plans installs, `procs.py` holds everything that differs between POSIX and Windows, `devbrowser.py` launches the dev-mode browser.

**Ports.** A preferred port from a hash of the branch name (4000–4999), probing upward if taken, never 5000 or 7000 (macOS AirPlay), with the next port reserved for HMR. The choice is stored, so a branch keeps its URL. Strict-port flags are used where the tool has one, so a lost race fails loudly instead of silently moving.

`PORT` is not universal. Measured: Vite and Flask ignore it, and take `--port` (Flask also `FLASK_RUN_PORT`). So each stack has a command template, `PORT` is always exported as well, and `--cmd` or `.parallel-branches.json` overrides both.

**Dependencies.** If the main checkout has `node_modules` and the lockfile is identical (compared ignoring line endings), clone it with copy-on-write; if cloning fails or the lockfile differs, install. pnpm installs from its store; Python gets a per-worktree virtual environment. `node_modules` is never symlinked: branches with different dependencies would corrupt each other.

**State** lives under `.git/parallel-branches/` (registry, logs, dev-browser profiles), behind a file lock. The first service of a branch is the flat registry entry and further services are `extras`, which kept single-service code paths and old state files unchanged when multi-service was added.

**Safety.** Process groups, not PIDs, are stopped, only those the tool started, and a recorded start time must still match, so a reused PID is never killed. A health check requires that the process on the port belongs to the group that was started (POSIX with `lsof`). `clean` is a dry run without `--yes`, skips worktrees with uncommitted work unless forced, and never removes a worktree it did not create. A dry run takes no lock and writes nothing.

**Hooks** (`hooks.py`) run shell commands at six points: `post_create`, `pre_start`, `post_start`, `pre_stop`, `pre_remove`, `post_remove`, and `pb hook EVENT BRANCH` runs a stored one by hand. The comparison that shaped them is Worktrunk's hook system (ten hooks, TOML, a `hash_port` filter, approval on first run). Worktrunk's `hash_port` derives a number from the branch name; ours receives the port that was probed and allocated, plus every other service's URL, and `post_start` waits for the health check. Teardown hooks run the commands stored with the branch when it started, which gives the effect of Worktrunk's re-approval without an approval store: what was shown in the dry run is what runs later. Substituted values are shell-quoted because branch names may contain `$`, parentheses and backticks. Failures follow what can be undone: before anything starts, abort; after, report and carry on, so a broken hook never traps a branch running. `pre_remove` is the one teardown hook that blocks, because it runs before something irreversible (a dump before the worktree goes); `--no-hooks` is the way out, so a broken hook cannot trap a worktree on disk either. `post_start` can run in the background (`background_hooks`): a detached `pb.py` process runs the stored commands and records the result under the lock, and only if the branch's start time still matches, so a late result never lands on a newer run; `down` stops it with the same start-time check as servers. Only `post_start`, because every other hook either gates what comes next or runs during teardown. Not built, on purpose: commit and merge hooks (no merge workflow here), concurrent hook tables, an approval store. The config stays JSON because TOML needs Python 3.11 and we support 3.9.

**The dev-mode browser** exists because CORS is the most common thing that breaks when the same app moves to another port. It uses a new empty profile (Chrome ignores `--disable-web-security` without a non-default `--user-data-dir`, which is also the safety property), opens one `localhost` URL, and exposes a DevTools port an agent can attach to. It does not fix OAuth/OIDC redirect URI mismatches, `Origin` or CSRF checks, or cookie rules, and the docs say so.

**Cross-service wiring.** `{url.NAME}` and `{ports.NAME}` are filled in after every service's port has been chosen, so wiring works in either start order.

## Measured

- **Port mechanisms, real servers:** see [phase0-results.md](phase0-results.md). Node, Vite 5 and Flask 3.
- **Dev-mode Chrome 156, headless and headed:** a no-CORS API on another port is blocked in a normal Chrome and readable with the flags; `--disable-site-isolation-trials` was not needed. An agent attached over CDP with `playwright-core`, and again with the stock `@playwright/mcp` 0.0.84 server started with `--cdp-endpoint` (stdio, driven by a script): a page on the branch's port read an API on another port that sends no CORS headers.
- **A CI-only failure worth knowing about:** on a GitHub macOS runner `socket.getfqdn("127.0.0.1")` took 35 s, so `python -m http.server` (which calls it when binding) took 35 s to open its port while its process looked alive. The tests now use a server that does not do that lookup.
- **Real application, dry run only:** on a large Angular application, detection, the start command and the install plan were right, and the scan found the hardcoded `localhost:4200` redirect URI and the identity-provider host, tagged as things a dev-mode browser cannot fix. Nothing was started there.

### Evals with real models

Five scenarios graded by deterministic rules (`tests/evals/`), run with Claude Haiku, Sonnet and Opus through the Claude Code CLI. Each agent was limited to this repository's `pb.py`, read-only git and reads inside a temporary directory. About $1.15 for a full pass of the first four scenarios on three models.

| Run | Result | What it taught |
|---|---|---|
| 1 | 8 of 12 perfect | Four failures were the allowlist denying `PB=...; python3 $PB` and read-only discovery commands. The agents reported the denial and did not try to route around it. |
| 2, after fixes | 10 of 12 | Haiku started servers without approval in one run; one grader pattern missed wording. |
| Approval wording | Haiku skipped approval in about 2 of 7 runs before the rule was strengthened and 0 of 10 after | Sonnet and Opus never skipped it. |
| Final skill text | 12 of 12, then 6 of 6 on the multi-service scenario | |
| Hooks scenarios (`hooks-confirm-first`, `hooks-run`) | 6 of 6 runs perfect on Haiku, Sonnet and Opus, $0.59 | Also 5 of 5 on Haiku with the hook wording removed from `SKILL.md`, so these scenarios do not prove the wording is needed |
| `pre_remove` scenarios (`hooks-clean-confirm`, `hooks-clean-run`) | Sonnet and Opus 4 of 4 perfect ($0.60 for that pass). Haiku 9 of 12 perfect | The first wording ("get rid of its worktree") never loaded the skill: 5 of 6 agents ran `git worktree remove`, which skips every hook. Saying "dev server" and "clean up" in the prompt fixed it. Haiku's three misses: two `hooks-clean-run` runs that removed nothing (the one inspected asked for approval again after the dry run, despite being told to go ahead; the other failed the same three checks) and one run that skipped the skill |
| After hooks (0.6.0) | 14 of 15 perfect, all five scenarios on three models, $1.63. The miss: Sonnet opened the dev browser without saying what it cannot fix (OAuth redirects, `Origin` checks, cookies) | `SKILL.md` step 4 now says to name those limits. After that: Sonnet 4 of 4 perfect, Haiku 9 of 10 (one 4/6 run, cause not captured) |

Skill changes the evals drove: write the literal `pb.py` path in every command (approval rules match literal text, so a shell variable can never be pre-approved), mention stopping and cleaning up in the description (Sonnet did not load the skill for a teardown request), and state the approval rule explicitly.

Caveats: small samples; the graders were adjusted after reading agent output, so the pass rate partly reflects grader tuning; there is no no-skill baseline (under a narrow allowlist it mostly measures denials); and the agent ran in the author's own account, not a sandbox.

## Decisions

- Standalone and zero-configuration, with no dependency on other worktree tools.
- Python 3.9 standard library only, because 3.9 is the default `python3` on macOS. Tests use `unittest` for the same reason.
- MIT licence.
- No framework-, auth-provider- or company-specific code or defaults. Auth problems are reported generically.
- Services are configured in `.parallel-branches.json`; monorepo layouts are only suggested (`pb init`), never applied without `--write`.
- Hooks: six events, shell-quoted variables, stored commands for teardown, no approval store (the dry run is the approval). A per-user hooks file runs before the repository's hooks and is labelled in every preview; it takes only `hooks` and `hook_timeout`, so a misplaced key is an error.

## Known gaps

- Only Vite, Flask and plain Node ports were measured on real servers. The other stacks follow documentation. Angular detection was checked by a dry run on a real application, not by running it.
- A port lost between the free-port check and the server's bind is reported, not retried.
- Windows stops a service by force-killing its process tree and skips the port-owner check.
- Chrome versions other than 156 were not tried with the dev-mode browser.
- Not detected: Docker Compose, Rust, .NET, PHP (use `--cmd`).
- `status` does not show per-service logs, or hook results (`pb hooks` and `status --json` do).
- Hooks have no concurrent form; only `post_start` can run in the background. There is one per-user file and one per-repository file, and no other level. `restart` runs none.
- A config committed on a branch can only be read after its worktree exists, so a mistake in it (hooks or services) is reported after the worktree was created and the worktree is not tracked for `clean`. The dry run reads the main checkout's config first.
- The two hooks scenarios pass with and without the hook wording in `SKILL.md` (Haiku, 5 of 5 each way), because the dry run prints the hooks and models repeat them anyway. They guard against hooks being lost on the way to the user, but do not show that the wording is needed. A scenario that does would need a repo whose hooks are easy to overlook, for example a long dry-run output.
- Removing a worktree with plain `git worktree remove` skips every hook. A request that does not mention a dev server or cleaning up may not load the skill (seen in the `pre_remove` evals), so the agent can do exactly that.
- The evals have not been run against models other than Claude's.
