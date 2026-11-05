#!/usr/bin/env python3
"""Run the parallel-branches evals against real models with the Claude Code CLI.

This SPENDS MONEY and lets a model run shell commands inside a throwaway repository, so it
refuses to run without --yes. Use --dry-run to see exactly what would be executed.

Examples:
  python3 tests/evals/run_evals.py --dry-run
  python3 tests/evals/run_evals.py --yes --models haiku,sonnet --scenarios side-by-side
  python3 tests/evals/run_evals.py --yes --baseline        # same prompts without the skill

Python 3.9+, standard library only. The stream-json event shape is assumed from the Claude Code
docs; check the first real transcript in the output directory.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fixtures  # noqa: E402
import graders  # noqa: E402

ALLOWED_TOOLS = ["Bash(python3 *)", "Bash(git *)", "Bash(ls *)", "Bash(cat *)", "Bash(curl *)",
                 "Bash(npm *)", "Bash(lsof *)", "Bash(ps *)", "Read", "Grep", "Glob"]


def load_scenarios(path=None):
    with open(path or os.path.join(HERE, "scenarios.json")) as fh:
        return json.load(fh)


def prepare(scenario, root):
    repo = fixtures.make_repo(scenario["fixture"], root)
    for step in scenario.get("setup", []):
        if "start" in step:
            fixtures.start(repo, *step["start"])
        if "dirty" in step:
            fixtures.make_dirty(repo, step["dirty"])
    return repo


def collect_state(repo):
    """Machine state after the agent finished: registry, reachability, repo cleanliness."""
    proc = fixtures.pb(repo, "status", "--json")
    try:
        rows = json.loads(proc.stdout)
    except ValueError:
        rows = []
    branches = {}
    for row in rows:
        row["http_ok"] = row["live"] in ("healthy", "unhealthy") and graders.http_ok(row["port"])
        branches[row["branch"]] = row
    dirty = fixtures.git(repo, "status", "--porcelain", "--untracked-files=no").strip()
    return {"branches": branches, "repo_clean": not dirty}


def claude_command(args, model, prompt):
    cmd = ["claude", "-p", prompt, "--model", model, "--output-format", "stream-json", "--verbose",
           "--permission-mode", "dontAsk", "--allowedTools", *ALLOWED_TOOLS,
           "--max-budget-usd", str(args.max_budget_usd), "--no-session-persistence",
           "--setting-sources", "project"]
    if not args.baseline:
        cmd += ["--plugin-dir", fixtures.ROOT]
    return cmd


def run_one(args, scenario, model, out_dir):
    root = tempfile.mkdtemp(prefix="pb-eval-")
    try:
        repo = prepare(scenario, root)
        started = time.time()
        proc = subprocess.run(claude_command(args, model, scenario["prompt"]), cwd=repo,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                              timeout=args.timeout)
        commands, final_text = graders.parse_transcript(proc.stdout.splitlines())
        state = collect_state(repo)
        run = {"commands": commands, "final_text": final_text, "state": state}
        results = graders.grade(run, scenario["checks"])
        record = {"scenario": scenario["id"], "model": model, "baseline": args.baseline,
                  "seconds": round(time.time() - started, 1), "exit_code": proc.returncode,
                  "commands": commands, "final_text": final_text, "results": results,
                  "stderr_tail": proc.stderr[-500:]}
        name = "%s__%s%s" % (scenario["id"], model, "__baseline" if args.baseline else "")
        with open(os.path.join(out_dir, name + ".json"), "w") as fh:
            json.dump(record, fh, indent=2)
        with open(os.path.join(out_dir, name + ".transcript.jsonl"), "w") as fh:
            fh.write(proc.stdout)
        return record
    except subprocess.TimeoutExpired:
        return {"scenario": scenario["id"], "model": model, "results": [], "error": "timed out"}
    finally:
        fixtures.pb(os.path.join(root, "proj"), "down", "--all")
        shutil.rmtree(root, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--models", default="haiku,sonnet,opus")
    parser.add_argument("--scenarios", help="comma-separated ids (default: all)")
    parser.add_argument("--baseline", action="store_true", help="run without the skill")
    parser.add_argument("--max-budget-usd", type=float, default=2.0, help="cap per run (default 2.0)")
    parser.add_argument("--timeout", type=int, default=900, help="seconds per run")
    parser.add_argument("--out", default=os.path.join(tempfile.gettempdir(), "pb-evals"))
    parser.add_argument("--dry-run", action="store_true", help="print the runs and commands, call nothing")
    parser.add_argument("--yes", action="store_true", help="confirm that real model calls may spend money")
    args = parser.parse_args()

    scenarios = load_scenarios()
    if args.scenarios:
        wanted = set(args.scenarios.split(","))
        scenarios = [s for s in scenarios if s["id"] in wanted]
    models = [m for m in args.models.split(",") if m]
    runs = [(s, m) for s in scenarios for m in models]
    suffix = ", baseline (no skill)" if args.baseline else ""
    print("%d run(s), up to $%.2f each (cap $%.2f total)%s"
          % (len(runs), args.max_budget_usd, len(runs) * args.max_budget_usd, suffix))
    if args.dry_run or not args.yes:
        for scenario, model in runs:
            print("\n[%s / %s]\n  %s" % (scenario["id"], model, " ".join(
                json.dumps(p) if " " in p else p for p in claude_command(args, model, scenario["prompt"]))))
        if not args.dry_run:
            print("\nNot running: pass --yes to spend money and let the model run shell commands.")
        return 0
    if not shutil.which("claude"):
        print("error: the `claude` CLI is not on PATH", file=sys.stderr)
        return 2
    os.makedirs(args.out, exist_ok=True)
    table, failed = [], False
    for scenario, model in runs:
        print("running %s / %s ..." % (scenario["id"], model), flush=True)
        record = run_one(args, scenario, model, args.out)
        passed = sum(r["passed"] for r in record["results"])
        total = len(record["results"])
        failed = failed or passed != total
        table.append((scenario["id"], model, "%d/%d" % (passed, total), record.get("error", "")))
        for r in record["results"]:
            if not r["passed"]:
                print("   FAIL %-24s %s" % (r["check"], r["detail"]))
    print("\n%-16s %-8s %-6s" % ("SCENARIO", "MODEL", "PASSED"))
    for row in table:
        print("%-16s %-8s %-6s %s" % row)
    print("\nrecords in %s" % args.out)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
