"""Tests for lifecycle hooks. Run: python3 -m unittest discover tests/scripts"""

import json
import os
import sys
import unittest

from test_pb import FAIL_CMD, PB, POSIX_ONLY, PY, ROOT, SERVER, SLEEP_CMD, PbCase, http_ok, pb

sys.path.insert(0, os.path.dirname(PB))
import hooks  # noqa: E402

PROBE = os.path.join(ROOT, "tests", "helpers", "hookprobe.py")


class ParseTests(unittest.TestCase):
    def test_accepts_strings_and_lists(self):
        cmds, timeout = hooks.parse({"hooks": {"pre_start": "a", "post_start": ["b", "c"]}})
        self.assertEqual(cmds, {"pre_start": ["a"], "post_start": ["b", "c"]})
        self.assertEqual(timeout, hooks.DEFAULT_TIMEOUT)

    def test_no_hooks_is_fine(self):
        self.assertEqual(hooks.parse({})[0], {})

    def test_rejects_unknown_event_and_bad_shapes(self):
        for bad in ({"hooks": {"post_merge": "x"}}, {"hooks": ["x"]}, {"hooks": {"pre_start": 3}},
                    {"hooks": {"pre_start": []}}, {"hooks": {"pre_start": ["ok", ""]}},
                    {"hook_timeout": 0}, {"hook_timeout": "5"}, {"hook_timeout": True},
                    {"hook_timeout": 99999}):
            with self.assertRaises(ValueError, msg=bad):
                hooks.parse(bad)

    def test_a_mistyped_variable_name_is_an_error_not_a_literal(self):
        for text in ("curl {url}/seed", "x {port}", "x {Branch_Slug}"):
            with self.assertRaisesRegex(ValueError, "the name is"):
                hooks.parse({"hooks": {"post_start": text}})
        hooks.parse({"hooks": {"post_start": "awk '{print}' ${PORT} {URL} {a,b}"}})  # other braces are fine

    def test_rejects_reference_to_an_unknown_service(self):
        with self.assertRaises(ValueError):
            hooks.parse({"hooks": {"post_start": "curl {url.api}"}}, {"web"})
        hooks.parse({"hooks": {"post_start": "curl {url.api}"}}, {"api", "web"})


class RenderTests(unittest.TestCase):
    CTX = hooks.context("feat/x", "feat-x", "/w/feat-x", "/repo", {"api": (4001, 4002), "web": (4003, 4004)}, "web")

    def test_fills_values_and_service_references(self):
        text = hooks.render("{PORT} {HMR_PORT} {URL} {ports.api} {url.api} {branch_slug}", self.CTX)
        self.assertEqual(text, "4003 4004 http://localhost:4003 4001 http://localhost:4001 feat-x")

    def test_shell_variables_and_unknown_tokens_are_left_alone(self):
        self.assertEqual(hooks.render("echo ${PORT} {nope} {url.gone}", self.CTX), "echo ${PORT} {nope} {url.gone}")

    @POSIX_ONLY
    def test_branch_names_cannot_inject_shell_syntax(self):
        ctx = hooks.context("a$(touch${IFS}x)", "a", "/w", "/repo", {None: (4001, 4002)}, None)
        self.assertEqual(hooks.render("echo {branch}", ctx), "echo 'a$(touch${IFS}x)'")

    def test_db_name_is_safe_and_distinguishes_lookalike_branches(self):
        a, b = hooks.db_name("feat/a"), hooks.db_name("feat-a")
        self.assertNotEqual(a, b)
        for name in (a, b, hooks.db_name("Ünï/cødé!!"), hooks.db_name("///")):
            self.assertRegex(name, r"^[a-z0-9_]+$")


class HookCase(PbCase):
    def setUp(self):
        super().setUp()
        self.out = os.path.join(self.tmp, "probe.jsonl")

    def probe(self, label, *args):
        """A hook command that records itself under `label`."""
        return " ".join([PY, '"%s"' % PROBE, '"%s"' % self.out, label] + list(args))

    def config(self, hook_map, **top):
        # Left uncommitted in the main checkout: new worktrees fall back to it.
        with open(os.path.join(self.repo, ".parallel-branches.json"), "w") as fh:
            json.dump(dict({"hooks": hook_map}, **top), fh)

    def records(self):
        try:
            with open(self.out) as fh:
                return [json.loads(line) for line in fh if line.strip()]
        except OSError:
            return []

    def labels(self):
        return [r["label"] for r in self.records()]


class LifecycleTests(HookCase):
    def test_every_event_runs_in_order_with_the_real_ports(self):
        self.config({"post_create": self.probe("post_create", "{PORT}"),
                     "pre_start": self.probe("pre_start", "{URL}"),
                     "post_start": self.probe("post_start", "{URL}"),
                     "pre_stop": self.probe("pre_stop", "{URL}"),
                     "post_remove": self.probe("post_remove", "{branch_slug}")})
        proc, rows = self.up("feat-a")
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        row = rows[0]
        url = "http://localhost:%d" % row["port"]
        self.assertEqual(self.labels(), ["post_create", "pre_start", "post_start"])
        created, before, after = self.records()
        self.assertEqual(created["args"], [str(row["port"])])
        self.assertEqual(created["cwd"], row["path"])
        self.assertNotEqual(before["fetched"][url], 200)  # nothing is listening yet
        self.assertEqual(after["fetched"][url], 200)  # post_start runs once the server answers
        self.assertEqual([x["event"] for x in row["hook_results"]], ["post_create", "pre_start", "post_start"])

        proc = self.pb("down", "feat-a")
        self.assertIn("hook pre_stop ok", proc.stdout)
        self.assertEqual(self.records()[-1]["fetched"][url], 200)  # pre_stop runs while it is still up
        self.assertFalse(http_ok(row["port"]))

        proc = self.pb("clean", "--yes")
        self.assertIn("hook post_remove ok", proc.stdout)
        last = self.records()[-1]
        self.assertEqual((last["label"], last["args"], last["cwd"]), ("post_remove", ["feat-a"], self.repo))

    def test_post_create_runs_once_but_pre_start_runs_every_time(self):
        self.config({"post_create": self.probe("post_create"), "pre_start": self.probe("pre_start")})
        self.up("feat-a")
        self.pb("down", "feat-a")
        self.up("feat-a")
        self.assertEqual(self.labels(), ["post_create", "pre_start", "pre_start"])

    def test_environment_carries_the_same_values(self):
        self.config({"post_start": self.probe("env")})
        _, rows = self.up("feat-a")
        env = self.records()[0]["env"]
        self.assertEqual((env["PB_HOOK"], env["PB_BRANCH"], env["PB_PORT"]),
                         ("post_start", "feat-a", str(rows[0]["port"])))
        self.assertEqual(env["PB_URL"], "http://localhost:%d" % rows[0]["port"])
        self.assertTrue(env["PB_DB_NAME"].startswith("feat_a_"))

    def test_services_see_each_others_urls(self):
        self.config({"post_start": self.probe("svc", "{url.api}", "{url.web}")},
                    services=[{"name": "api", "cmd": SERVER, "install": False},
                              {"name": "web", "cmd": SERVER, "install": False, "depends_on": ["api"]}])
        proc, rows = self.up("feat-a", cmd=None)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        record = self.records()[0]
        self.assertEqual(sorted(record["fetched"].values()), [200, 200])  # both servers were up
        self.assertEqual(record["env"]["PB_URL_API"], "http://localhost:%d" % rows[0]["extras"][0]["port"])

    def test_dry_run_lists_hooks_with_values_filled_in_and_starts_nothing(self):
        self.config({"post_start": "seed --port {PORT} --db {db_name}", "post_remove": "drop {db_name}"})
        proc = self.pb("up", "feat-a", "--cmd", SERVER, "--worktree-dir", self.wt, "--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertRegex(proc.stdout, r"feat-a: hook post_start: seed --port \d+ --db feat_a_[0-9a-f]{4}")
        self.assertIn("feat-a: hook post_remove: drop feat_a_", proc.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".git", "parallel-branches")))
        self.assertFalse(os.path.exists(os.path.join(self.wt, "feat-a")))

    def test_clean_dry_run_shows_the_post_remove_hook(self):
        self.config({"post_remove": self.probe("post_remove")})
        self.up("feat-a")
        self.pb("down", "feat-a")
        proc = self.pb("clean")
        self.assertIn("would then run hook post_remove", proc.stdout)
        self.assertEqual(self.labels(), [])


class FailureTests(HookCase):
    def test_failing_pre_start_stops_the_branch_before_anything_starts(self):
        self.config({"pre_start": FAIL_CMD})
        proc, rows = self.up("feat-a")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("FAILED: hook pre_start: command 1 exited with code 3", rows[0]["note"])
        self.assertIn("boom", rows[0]["note"])
        self.assertTrue(pb.port_is_free(rows[0]["port"]))
        self.assertEqual(json.loads(self.pb("status", "--json").stdout)[0]["live"], "failed")

    def test_failing_post_create_stops_the_branch_too(self):
        self.config({"post_create": FAIL_CMD, "pre_start": self.probe("pre_start")})
        proc, rows = self.up("feat-a")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("hook post_create", rows[0]["note"])
        self.assertEqual(self.labels(), [])

    def test_failing_post_start_only_warns(self):
        self.config({"post_start": FAIL_CMD})
        proc, rows = self.up("feat-a")
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("WARNING: hook post_start failed", rows[0]["note"])
        self.assertTrue(http_ok(rows[0]["port"]))

    def test_failing_pre_stop_never_keeps_a_branch_from_stopping(self):
        self.config({"pre_stop": FAIL_CMD})
        _, rows = self.up("feat-a")
        proc = self.pb("down", "feat-a")
        self.assertIn("WARNING hook pre_stop failed", proc.stdout)
        self.assertIn("stopped", proc.stdout)
        self.assertFalse(http_ok(rows[0]["port"]))

    def test_failing_post_remove_still_removes_the_worktree(self):
        self.config({"post_remove": FAIL_CMD})
        _, rows = self.up("feat-a")
        self.pb("down", "feat-a")
        proc = self.pb("clean", "--yes")
        self.assertIn("removed worktree", proc.stdout)
        self.assertIn("WARNING hook post_remove failed", proc.stdout)
        self.assertFalse(os.path.exists(rows[0]["path"]))

    def test_a_hook_that_hangs_is_killed_after_the_timeout(self):
        self.config({"pre_start": SLEEP_CMD})
        started = __import__("time").time()
        proc = self.pb("up", "feat-a", "--cmd", SERVER, "--worktree-dir", self.wt, "--timeout", "20",
                       "--hook-timeout", "1", "--json")
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("timed out after 1s", json.loads(proc.stdout)[0]["note"])
        self.assertLess(__import__("time").time() - started, 30)

    def test_the_first_failing_command_in_a_list_ends_the_list(self):
        self.config({"pre_start": [self.probe("one"), FAIL_CMD, self.probe("three")]})
        proc, rows = self.up("feat-a")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("command 2 exited", rows[0]["note"])
        self.assertEqual(self.labels(), ["one"])

    def test_bad_hooks_config_is_reported_and_nothing_is_started(self):
        self.config({"post_merge": "x"})
        proc = self.pb("up", "feat-a", "--cmd", SERVER, "--worktree-dir", self.wt)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("bad hooks", proc.stderr)
        self.assertIn("unknown hook 'post_merge'", proc.stderr)
        self.assertEqual(json.loads(self.pb("status", "--json").stdout), [])
        dry = self.pb("up", "feat-a", "--cmd", SERVER, "--worktree-dir", self.wt, "--dry-run")
        self.assertEqual(dry.returncode, 2)  # a dry run catches it before any worktree exists


class TrustTests(HookCase):
    def test_no_hooks_flag_runs_nothing_now_or_at_teardown(self):
        self.config({"pre_start": self.probe("pre_start"), "pre_stop": self.probe("pre_stop")})
        self.up("feat-a", extra=["--no-hooks"])
        self.pb("down", "feat-a")
        self.assertEqual(self.labels(), [])

    def test_down_skips_hooks_with_no_hooks(self):
        self.config({"pre_stop": self.probe("pre_stop")})
        self.up("feat-a")
        self.pb("down", "feat-a", "--no-hooks")
        self.assertEqual(self.labels(), [])

    def test_teardown_runs_what_was_stored_at_up_not_what_the_config_says_now(self):
        self.config({"pre_stop": self.probe("approved")})
        self.up("feat-a")
        self.config({"pre_stop": self.probe("swapped-in-later")})  # e.g. a different branch was checked out
        self.pb("down", "feat-a")
        self.assertEqual(self.labels(), ["approved"])

    @POSIX_ONLY
    def test_a_branch_name_cannot_run_commands_through_a_hook(self):
        branch = "a$(touch${IFS}pwned)"
        self.git("branch", branch)
        self.config({"post_start": self.probe("quoted", "{branch}")})
        proc, rows = self.up(branch)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertEqual(self.records()[0]["args"], [branch])
        self.assertFalse(os.path.exists(os.path.join(rows[0]["path"], "pwned")))


if __name__ == "__main__":
    unittest.main()
