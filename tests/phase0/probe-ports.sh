#!/usr/bin/env bash
# Phase 0: which port mechanism does each fixture honor? Prints one result line per probe.
# Usage: tests/phase0/probe-ports.sh   (needs fixtures installed: see PLAN.md)
set -u
ROOT="$(cd "$(dirname "$0")/../fixtures" && pwd)"
LOG="${TMPDIR:-/tmp}/pb-probe"; mkdir -p "$LOG"

probe() { # label dir port env-assignments... -- command...
  local label="$1" dir="$2" port="$3"; shift 3
  local envs=(PB_PROBE=1); while [ "$1" != "--" ]; do envs+=("$1"); shift; done; shift
  ( cd "$ROOT/$dir" && env "${envs[@]}" "$@" >"$LOG/$label.log" 2>&1 & echo $! >"$LOG/$label.pid"; disown )
  local ok=no code=000
  for _ in $(seq 1 25); do
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 1 "http://localhost:$port/" || true)
    [ "$code" = "200" ] && { ok=yes; break; }; sleep 0.4
  done
  local other; other=$(grep -Eo 'localhost:[0-9]+|:[0-9]{4,5}' "$LOG/$label.log" | head -2 | tr '\n' ' ')
  printf '%-34s expected :%-5s honored=%-3s  log-ports: %s\n' "$label" "$port" "$ok" "$other"
  pkill -P "$(cat "$LOG/$label.pid")" 2>/dev/null; kill "$(cat "$LOG/$label.pid")" 2>/dev/null
  lsof -ti "tcp:$port" 2>/dev/null | xargs kill 2>/dev/null; sleep 0.5
}

probe "node-http PORT env"          node-http 4411 PORT=4411 -- node server.js
probe "vite PORT env"               vite-app  4411 PORT=4411 -- ./node_modules/.bin/vite
probe "vite --port flag"            vite-app  4412 -- ./node_modules/.bin/vite --port 4412 --strictPort
probe "flask PORT env"              flask-app 4411 PORT=4411 -- .venv/bin/flask --app app run
probe "flask FLASK_RUN_PORT env"    flask-app 4413 FLASK_RUN_PORT=4413 -- .venv/bin/flask --app app run
probe "flask --port flag"           flask-app 4414 -- .venv/bin/flask --app app run --port 4414
