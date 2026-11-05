# Phase 0 results

Measured on macOS, Node 20.19.4, Python 3.9.6, Chrome 156.0.8078.80. Reproduce with `tests/phase0/probe-ports.sh` and `tests/phase0/cors-devmode.js`.

## Port mechanisms

| Probe | Expected port | Honored |
|---|---|---|
| node-http, `PORT=4411` (app code reads `PORT`) | 4411 | yes |
| Vite 5.4.11, `PORT=4411` | 4411 | **no**, it listened on 5173 |
| Vite, `--port 4412 --strictPort` | 4412 | yes |
| Flask 3.0.3, `PORT=4411` | 4411 | **no**, it tried 5000 |
| Flask, `FLASK_RUN_PORT=4413` | 4413 | yes |
| Flask, `--port 4414` | 4414 | yes |

Conclusion: `PORT` is not universal. The skill needs a per-stack command template, and should still export `PORT` for stacks that read it.

## Dev-mode browser (CORS)

Fixture: a host page on :4421 fetches JSON from a remote on :4422. The remote sends no CORS headers. Each case used a fresh throwaway `--user-data-dir`, headless Chrome and an attach over CDP with `playwright-core` `connectOverCDP`.

| Browser launch | Result |
|---|---|
| no extra flags | `BLOCKED Failed to fetch` |
| `--disable-web-security --disable-site-isolation-trials` | `OK` |
| `--disable-web-security` only | `OK` |

Conclusions:
- On Chrome 156 with a throwaway profile, `--disable-web-security` bypasses CORS for a simple cross-origin `fetch`.
- `--disable-site-isolation-trials` was not needed in this test. Keep it as an optional flag until a case shows it matters.
- An agent can attach to the launched browser over CDP, so no restart of an agent's own browser is needed.

## Findings that change the design

1. **Python 3.9 is the default `python3` on macOS.** `pb.py` must run on 3.9: no `match`, no `X | Y` type syntax, no 3.10+ stdlib features.
2. **Ports 5000 and 7000 are occupied on macOS (AirPlay Receiver).** They were listening on this machine. Flask's default port 5000 hits this. A health check against a default port can reach the wrong process, so the check must target the allocated port and confirm the PID it started owns it.
3. **bash 3.2 on macOS** treats an empty array as unbound under `set -u`. Shell helpers should avoid that, or the logic should live in Python.
4. **Stacks differ in how a busy port fails.** Vite moves to the next port unless `--strictPort` is set. The skill should always pass the strict flag where one exists.

## Not yet verified

- Whether `--disable-web-security` is ignored when no `--user-data-dir` is given. Not tested on purpose, because it could touch the real profile.
- Whether `--disable-web-security` still works when Chrome is launched in headed mode.
- Attaching through Playwright MCP itself (`--cdp-endpoint`). Only `playwright-core` was tested.
- Express, Next, CRA, Rails, Django, Spring Boot, Go and Docker Compose. Only Node http, Vite and Flask were measured; the rest still rely on documentation.
