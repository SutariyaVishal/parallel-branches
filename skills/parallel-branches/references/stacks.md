# Stacks, installs and configuration

## Detection

`pb.py` inspects the worktree and picks the first match. `PB detect` prints the result and lists other candidates under `alternatives`.

| Stack | Detected by | Start command | How the port is set | Confidence |
|---|---|---|---|---|
| Next.js | `next` in package.json, a `dev`/`start`/`serve` script | `<pm> run dev -p {PORT}` | flag | high |
| Nuxt | `nuxt` | `<pm> run dev --port {PORT}` | flag | high |
| Angular | `@angular/cli` or `angular.json` | `<pm> run start --port {PORT}` | flag | high |
| Astro | `astro` | `<pm> run dev --port {PORT}` | flag | high |
| SvelteKit | `@sveltejs/kit` | `<pm> run dev --port {PORT} --strictPort` | flag | high |
| Vite | `vite` | `<pm> run dev --port {PORT} --strictPort` | flag | high |
| webpack-dev-server | `webpack-dev-server` | `<pm> run start --port {PORT}` | flag | high |
| Create React App | `react-scripts` | `<pm> run start` | `PORT` env only | low |
| Other Node | a `dev`, `start` or `serve` script | `<pm> run dev` | `PORT` env only | low |
| Django | `manage.py` | `{PY} manage.py runserver 127.0.0.1:{PORT}` | positional | high |
| Flask | `flask` in requirements and `app.py`, `wsgi.py` or `application.py` | `{PY} -m flask run --port {PORT}` | flag | high |
| FastAPI | `fastapi` or `uvicorn`, and `main.py` or `app/main.py` | `{PY} -m uvicorn main:app --port {PORT}` | flag | high |
| Rails | `Gemfile` and `bin/rails` | `bin/rails server -p {PORT}` | flag | high |
| Spring Boot | Maven or Gradle with spring-boot | `./mvnw spring-boot:run` or `./gradlew bootRun` | `SERVER_PORT` env | high |
| Go | `go.mod` and `main.go` | `go run .` | app decides; `PORT` env only | low |

`<pm>` is npm, pnpm, yarn or bun, chosen from the lockfile. npm gets `--` before the flags; the others do not. `{PY}` becomes `.venv/bin/python` when the worktree has a virtual environment, otherwise `python3`.

Not detected: Docker Compose, Rust, .NET, PHP. Pass `--cmd`. Verified by running real servers: Vite, Flask and a plain Node server (see `docs/phase0-results.md` in the repository). The rest follows each framework's documentation.

## Dependencies

| Family | Rule |
|---|---|
| Node, npm / yarn / bun | If the main checkout has `node_modules` and the lockfile is byte-identical, clone it (`cp -c` on macOS, `--reflink=auto` on Linux). If the clone fails or the lockfile differs, install: `npm ci` (or `npm install` with no lockfile), `yarn install --frozen-lockfile` (`--immutable` for Yarn 2+), `bun install --frozen-lockfile`. |
| Node, pnpm | `pnpm install --frozen-lockfile`. pnpm hard-links from its store, so this is cheap. |
| Python | With `uv.lock` and `uv`: `uv sync`. With `requirements.txt`: a `.venv` created by `uv` or `python3 -m venv`, then installed. |
| Ruby | `bundle install` when `Gemfile.lock` exists. |
| Others | Nothing; the build tool fetches what it needs when it runs. |

A worktree that already has `node_modules` or `.venv` is left alone. `node_modules` is never symlinked: branches with different dependencies would corrupt each other. Pass `--no-install` to skip all of this.

## Ports

Each branch gets a preferred port from a hash of its name (4000-4999) and probes upward when that is taken. 5000 and 7000 are skipped (macOS AirPlay). A second port, `{HMR_PORT}`, is reserved next to it. The choice is stored, so a branch keeps its URL across restarts.

## Environment files

When a worktree is created, git-ignored `.env` and `.env.*` files, and anything listed in `.worktreeinclude` or the config `copy` list, are copied from the main checkout. Existing files are never overwritten, and tracked files are never copied.

## `.parallel-branches.json`

Optional, at the repository root. The worktree's copy wins over the main checkout's.

```json
{
  "cmd": "npm run dev -- --port {PORT} --strictPort",
  "install": "npm ci",
  "env": { "API_URL": "http://localhost:8080" },
  "health_path": "/health",
  "copy": ["config/local.json"]
}
```

| Key | Meaning |
|---|---|
| `cmd` | Start command. Overridden by `--cmd`. |
| `install` | A shell command, or `false` to skip installing. |
| `env` | Extra environment variables. Values may use `{PORT}` and `{HMR_PORT}`. |
| `health_path` | Path requested during the health check. Default `/`. |
| `copy` | Extra glob patterns of git-ignored files to copy into new worktrees. |

These strings run in a shell. Read a repository's config before running it, the same way you would read its `package.json` scripts.
