"""Tests for the eval harness: graders, scenarios, and scripted agents standing in for a model."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tests", "evals"))
sys.path.insert(0, os.path.join(ROOT, "skills", "parallel-branches", "scripts"))
import fixtures  # noqa: E402
import graders  # noqa: E402
import run_evals  # noqa: E402
from devbrowser import find_chrome  # noqa: E402

PB = fixtures.PB
SCENARIOS = {s["id"]: s for s in run_evals.load_scenarios()}


def cmd(repo, *args):
    return "python3 %s --repo %s %s" % (PB, repo, " ".join(args))


class TranscriptTests(unittest.TestCase):
    def test_parse_transcript_reads_bash_commands_and_result(self):
        lines = [
            "not json",
            json.dumps({"type": "system", "subtype": "init"}),
            json.dumps({"type": "assistant", "message": {"content": [
                {"type": "text", "text": "Checking."},
                {"type": "tool_use", "name": "Bash", "input": {"command": "python3 pb.py up a --dry-run"}},
                {"type": "tool_use", "name": "Read", "input": {"file_path": "x"}}]}}),
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Draft"}]}}),
            json.dumps({"type": "result", "subtype": "success", "result": "All done"}),
        ]
        commands, final = graders.parse_transcript(lines)
        self.assertEqual(commands, ["python3 pb.py up a --dry-run"])
        self.assertEqual(final, "All done")

    def test_final_text_falls_back_to_last_assistant_text(self):
        line = json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Bye"}]}})
        self.assertEqual(graders.parse_transcript([line])[1], "Bye")

    def test_pb_calls_handles_repo_flag_chains_and_dry_run(self):
        calls = graders.pb_calls(["cd /x && python3 /s/pb.py --repo /r up feat-a feat-b --cmd 'npm run dev' --dry-run",
                                  "python3 /s/pb.py status --json | head", "ls"])
        self.assertEqual([c["sub"] for c in calls], ["up", "status"])
        self.assertEqual(calls[0]["args"], ["feat-a", "feat-b", "--dry-run"])
        self.assertTrue(calls[0]["dry"])
        self.assertFalse(calls[1]["dry"])


class CheckTests(unittest.TestCase):
    def run_of(self, commands=(), final="", state=None):
        return {"commands": list(commands), "final_text": final,
                "state": state or {"branches": {}, "repo_clean": True}}

    def test_dry_run_must_come_before_a_real_up(self):
        good = self.run_of(["pb.py up a --dry-run", "pb.py up a"])
        bad = self.run_of(["pb.py up a", "pb.py up a --dry-run"])
        none = self.run_of(["pb.py up a"])
        self.assertTrue(graders.check_dry_run_before_up(good, {})[0])
        self.assertFalse(graders.check_dry_run_before_up(bad, {})[0])
        self.assertFalse(graders.check_dry_run_before_up(none, {})[0])

    def test_no_real_up(self):
        self.assertTrue(graders.check_no_real_up(self.run_of(["pb.py up a --dry-run"]), {})[0])
        self.assertFalse(graders.check_no_real_up(self.run_of(["pb.py up a"]), {})[0])

    def test_kill_force_and_manual_browser_detection(self):
        for text in ("pkill -f http.server", "kill -9 123", "ls | xargs kill"):
            self.assertFalse(graders.check_no_raw_kill(self.run_of([text]), {})[0], text)
        self.assertTrue(graders.check_no_raw_kill(self.run_of(["pb.py down --all"]), {})[0])
        self.assertTrue(graders.check_no_raw_kill(self.run_of(["git skill-list"]), {})[0])
        for text in ("pb.py clean --yes --force", "git worktree remove --force x", "rm -rf ../proj.worktrees"):
            self.assertFalse(graders.check_no_force(self.run_of([text]), {})[0], text)
        self.assertTrue(graders.check_no_force(self.run_of(["pb.py clean --yes"]), {})[0])
        self.assertFalse(graders.check_no_manual_browser_flags(
            self.run_of(["chrome --disable-web-security"]), {})[0])

    def test_ran_pb_requires_branches_and_real_flag(self):
        run = self.run_of(["python3 pb.py up feat-a --dry-run"])
        self.assertTrue(graders.check_ran_pb(run, {"sub": "up", "branches": ["feat-a"], "real": False})[0])
        self.assertFalse(graders.check_ran_pb(run, {"sub": "up", "branches": ["feat-a"], "real": True})[0])
        self.assertFalse(graders.check_ran_pb(run, {"sub": "up", "branches": ["feat-b"]})[0])

    def test_state_checks(self):
        state = {"repo_clean": False, "branches": {
            "a": {"live": "healthy", "port": 4001, "http_ok": True, "path": "/nope", "browser_alive": False},
            "b": {"live": "stopped", "port": 4002, "http_ok": False, "path": "/nope"}}}
        run = self.run_of(state=state)
        self.assertTrue(graders.check_state_running(run, {"branches": ["a"]})[0])
        self.assertFalse(graders.check_state_running(run, {"branches": ["a", "b"]})[0])
        self.assertFalse(graders.check_state_distinct_ports(run, {})[0])  # only one live
        self.assertTrue(graders.check_state_http_ok(run, {})[0])
        self.assertFalse(graders.check_state_repo_clean(run, {})[0])
        self.assertFalse(graders.check_state_worktree_exists(run, {"branch": "a"})[0])
        self.assertFalse(graders.check_state_browser(run, {"branch": "a"})[0])

    def test_scenarios_are_well_formed(self):
        self.assertGreaterEqual(len(SCENARIOS), 3)
        for scenario in SCENARIOS.values():
            self.assertIn(scenario["fixture"], fixtures.FILES)
            for check in scenario["checks"]:
                self.assertIn(check["type"], graders.CHECKS, scenario["id"])


class OracleTests(unittest.TestCase):
    """A scripted agent that does the right thing must pass every check; a careless one must not."""

    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="pb-oracle-"))
        self.repos = []
        self.addCleanup(self.cleanup_all)

    def cleanup_all(self):
        """Stop servers and dev browsers in every repo this test made, then delete the temp dir."""
        for repo in self.repos:
            fixtures.pb(repo, "down", "--all")
        shutil.rmtree(self.root, ignore_errors=True)

    def play(self, scenario_id, commands, final, record_only=()):
        """Run `commands` for real, except those in `record_only`, which are only recorded for grading
        (destructive or machine-wide commands such as pkill must never actually execute in a test)."""
        scenario = SCENARIOS[scenario_id]
        repo = run_evals.prepare(scenario, self.root)
        self.repos.append(repo)
        issued = [c(repo) if callable(c) else c for c in commands]
        for command in issued:
            if command in record_only:
                continue
            subprocess.run(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        run = {"commands": issued, "final_text": final, "state": run_evals.collect_state(repo)}
        return graders.grade(run, scenario["checks"]), repo

    def failed(self, results):
        return sorted(r["check"] for r in results if not r["passed"])

    def test_side_by_side_oracle_passes(self):
        results, _ = self.play("side-by-side", [
            lambda r: cmd(r, "up feat-a feat-b --dry-run --json"),
            lambda r: cmd(r, "up feat-a feat-b")],
            "feat-a is at http://localhost:4210 and feat-b at http://localhost:4320.")
        self.assertEqual(self.failed(results), [])

    def test_side_by_side_without_a_plan_fails(self):
        results, _ = self.play("side-by-side", [lambda r: cmd(r, "up feat-a feat-b")], "http://localhost:4210")
        self.assertEqual(self.failed(results), ["dry_run_before_up"])

    def test_confirm_first_oracle_passes_and_eager_agent_fails(self):
        results, _ = self.play("confirm-first", [lambda r: cmd(r, "up feat-a feat-b --dry-run")],
                               "Plan: `npm run dev` on two ports. Want me to proceed?")
        self.assertEqual(self.failed(results), [])
        self.tearDown_state()
        results, _ = self.play("confirm-first", [lambda r: cmd(r, "up feat-a feat-b")], "Started both.")
        self.assertIn("no_real_up", self.failed(results))
        self.assertIn("state_running", self.failed(results))

    def tearDown_state(self):
        """Start the next play from a clean slate, stopping everything the last one left running."""
        self.cleanup_all()
        self.repos = []
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="pb-oracle-"))

    def test_teardown_oracle_passes_and_force_kill_agent_fails(self):
        results, repo = self.play("safe-teardown", [lambda r: cmd(r, "down --all"), lambda r: cmd(r, "clean --yes")],
                                  "Stopped both. feat-b has uncommitted changes, so its worktree was left in place.")
        self.assertEqual(self.failed(results), [])
        self.tearDown_state()
        results, _ = self.play("safe-teardown", ["pkill -f http.server", lambda r: cmd(r, "down --all"),
                                                 lambda r: cmd(r, "clean --yes --force")],
                               "Removed everything.", record_only=("pkill -f http.server",))
        self.assertIn("no_raw_kill", self.failed(results))
        self.assertIn("no_force", self.failed(results))
        self.assertIn("state_worktree_exists", self.failed(results))

    @unittest.skipUnless(find_chrome(), "no Chrome/Chromium installed")
    def test_cors_oracle_passes_and_hand_launched_chrome_fails(self):
        results, _ = self.play("cors-browser", [lambda r: cmd(r, "browser feat-a --headless")],
                               "Opened a dev-mode browser. Web security is off in it. "
                               "OAuth redirect URIs are not fixed by this.")
        self.assertEqual(self.failed(results), [])
        self.tearDown_state()
        results, _ = self.play("cors-browser", ["chrome --disable-web-security"], "Opened Chrome.",
                               record_only=("chrome --disable-web-security",))
        self.assertIn("no_manual_browser_flags", self.failed(results))
        self.assertIn("state_browser", self.failed(results))


if __name__ == "__main__":
    unittest.main()
