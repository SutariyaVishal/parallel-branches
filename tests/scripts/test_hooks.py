"""Tests for lifecycle hooks. Run: python3 -m unittest discover tests/scripts"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

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


class UserConfigUnitTests(unittest.TestCase):
    def test_path_override_default_xdg_and_off_switch(self):
        with mock.patch.dict(os.environ, {"PB_USER_CONFIG": "/x/y.json"}):
            self.assertEqual(hooks.user_config_path(), "/x/y.json")
        with mock.patch.dict(os.environ, {"PB_USER_CONFIG": ""}):
            self.assertIsNone(hooks.user_config_path())
        env = {k: v for k, v in os.environ.items() if k != "PB_USER_CONFIG"}
        with mock.patch.dict(os.environ, dict(env, XDG_CONFIG_HOME="/cfg", APPDATA="/appdata"), clear=True):
            expected = "/appdata" if os.name == "nt" else "/cfg"
            self.assertEqual(hooks.user_config_path(), os.path.join(expected, "parallel-branches", "config.json"))

    def write(self, text):
        fd, path = tempfile.mkstemp(suffix=".json")
        self.addCleanup(os.remove, path)
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        return path

    def test_load_user_reads_hooks_and_timeout(self):
        path = self.write(json.dumps({"hooks": {"post_start": "x"}, "hook_timeout": 9}))
        with mock.patch.dict(os.environ, {"PB_USER_CONFIG": path}):
            cfg, where = hooks.load_user()
        self.assertEqual((cfg["hook_timeout"], where), (9, path))

    def test_missing_file_is_not_an_error(self):
        with mock.patch.dict(os.environ, {"PB_USER_CONFIG": "/no/such/file.json"}):
            self.assertEqual(hooks.load_user(), ({}, None))

    def test_bad_files_are_errors_that_name_the_file(self):
        for text, expect in (("{nope", "cannot read it as JSON"), ("[1]", "expected a JSON object"),
                             (json.dumps({"services": []}), "unknown key 'services'"),
                             (json.dumps({"hooks": {"post_merge": "x"}}), "unknown hook"),
                             (json.dumps({"hook_timeout": 0}), "hook_timeout")):
            path = self.write(text)
            with mock.patch.dict(os.environ, {"PB_USER_CONFIG": path}):
                with self.assertRaisesRegex(ValueError, expect) as ctx:
                    hooks.load_user()
            self.assertIn(path, str(ctx.exception))

    def test_merge_puts_user_commands_first_and_tracks_sources(self):
        merged, sources = hooks.merge({"post_start": ["u1"], "pre_stop": ["u2"]},
                                      {"post_start": ["p1", "p2"], "post_remove": ["p3"]})
        self.assertEqual(merged, {"post_start": ["u1", "p1", "p2"], "pre_stop": ["u2"], "post_remove": ["p3"]})
        self.assertEqual(sources["post_start"], ["user", "project", "project"])
        self.assertEqual(hooks.merge({}, {"post_start": ["p"]}), ({"post_start": ["p"]}, None))

    def test_label(self):
        self.assertEqual(hooks.label(["user", "project"], 0), " (user)")
        self.assertEqual(hooks.label(["user", "project"], 1), "")
        self.assertEqual(hooks.label(None, 0), "")


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

    def state_text(self):
        with open(os.path.join(self.repo, ".git", "parallel-branches", "state.json")) as fh:
            return fh.read()


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

    def test_clean_dry_run_shows_the_remove_hooks_in_order(self):
        self.config({"pre_remove": self.probe("pre_remove"), "post_remove": self.probe("post_remove")})
        self.up("feat-a")
        self.pb("down", "feat-a")
        proc = self.pb("clean")
        self.assertRegex(proc.stdout, r"would first run hook pre_remove[^\n]*\n[^\n]*would then run hook post_remove")
        self.assertEqual(self.labels(), [])

    def test_pre_remove_runs_in_the_worktree_before_post_remove(self):
        self.config({"pre_remove": self.probe("pre_remove", "{branch_slug}"),
                     "post_remove": self.probe("post_remove")})
        _, rows = self.up("feat-a")
        self.pb("down", "feat-a")
        proc = self.pb("clean", "--yes")
        self.assertIn("hook pre_remove ok", proc.stdout)
        self.assertEqual(self.labels(), ["pre_remove", "post_remove"])
        before, after = self.records()
        self.assertEqual((before["args"], before["cwd"]), (["feat-a"], rows[0]["path"]))
        self.assertEqual(after["cwd"], self.repo)


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

    def test_failing_pre_remove_keeps_the_worktree_until_no_hooks_is_given(self):
        self.config({"pre_remove": FAIL_CMD, "post_remove": self.probe("post_remove")})
        _, rows = self.up("feat-a")
        self.pb("down", "feat-a")
        proc = self.pb("clean", "--yes")
        self.assertIn("WARNING hook pre_remove failed", proc.stdout)
        self.assertIn("worktree kept because pre_remove failed", proc.stdout)
        self.assertTrue(os.path.isdir(rows[0]["path"]))
        self.assertEqual(self.labels(), [])  # post_remove has nothing to follow
        self.assertEqual([r["branch"] for r in json.loads(self.pb("status", "--json").stdout)], ["feat-a"])
        proc = self.pb("clean", "--yes", "--no-hooks")
        self.assertIn("removed worktree", proc.stdout)
        self.assertFalse(os.path.exists(rows[0]["path"]))

    def test_pre_remove_does_not_run_for_a_worktree_clean_skips(self):
        self.config({"pre_remove": self.probe("pre_remove")})
        _, rows = self.up("feat-a")
        self.pb("down", "feat-a")
        with open(os.path.join(rows[0]["path"], "scratch.txt"), "w") as fh:
            fh.write("unsaved")
        proc = self.pb("clean", "--yes")
        self.assertIn("uncommitted changes, skipped", proc.stdout)
        self.assertEqual(self.labels(), [])

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


class HookCommandTests(HookCase):
    def test_runs_the_stored_hook_with_the_ports_the_branch_has(self):
        self.config({"post_start": self.probe("seed", "{URL}")})
        _, rows = self.up("feat-a")
        url = "http://localhost:%d" % rows[0]["port"]
        proc = self.pb("hook", "post_start", "feat-a")
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("feat-a: hook post_start ok", proc.stdout)
        self.assertEqual(self.labels(), ["seed", "seed"])  # once from `up`, once by hand
        last = self.records()[-1]
        self.assertEqual((last["args"], last["cwd"], last["fetched"][url]), ([url], rows[0]["path"], 200))
        self.assertTrue(json.loads(self.pb("status", "--json").stdout)[0]["hook_runs"]["post_start"]["ok"])

    def test_dry_run_prints_the_command_and_runs_nothing(self):
        self.config({"pre_stop": self.probe("pre_stop", "{db_name}")})
        self.up("feat-a")
        before = self.state_text()
        proc = self.pb("hook", "pre_stop", "feat-a", "--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertRegex(proc.stdout, r"feat-a: hook pre_stop: .*pre_stop feat_a_[0-9a-f]{4}")
        self.assertEqual(self.labels(), [])
        self.assertEqual(self.state_text(), before)

    def test_runs_what_was_stored_not_what_the_config_says_now(self):
        self.config({"post_start": self.probe("approved")})
        self.up("feat-a")
        self.config({"post_start": self.probe("swapped-in-later")})
        self.pb("hook", "post_start", "feat-a")
        self.assertEqual(self.labels(), ["approved", "approved"])

    def test_a_failing_hook_exits_1_and_says_so(self):
        self.config({"post_start": FAIL_CMD})
        self.up("feat-a")
        proc = self.pb("hook", "post_start", "feat-a")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("WARNING hook post_start failed", proc.stdout)
        self.assertFalse(json.loads(self.pb("status", "--json").stdout)[0]["hook_runs"]["post_start"]["ok"])

    def test_hook_timeout_applies_to_this_run_only(self):
        self.config({"pre_stop": SLEEP_CMD})
        self.up("feat-a")
        proc = self.pb("hook", "pre_stop", "feat-a", "--hook-timeout", "1")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("timed out after 1s", proc.stdout)
        entry = json.loads(self.pb("status", "--json").stdout)[0]
        self.assertNotEqual(entry.get("hook_timeout"), 1)

    def test_untracked_branch_and_missing_hook_and_unknown_event(self):
        proc = self.pb("hook", "post_start", "nope")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("not tracked", proc.stderr)
        self.config({"post_start": self.probe("x")})
        self.up("feat-a")
        proc = self.pb("hook", "pre_stop", "feat-a")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("no pre_stop hook stored", proc.stdout)
        self.assertEqual(self.pb("hook", "post_merge", "feat-a").returncode, 2)  # argparse rejects it


class HooksShowTests(HookCase):
    def test_lists_stored_hooks_with_values_filled_in_and_last_run(self):
        self.config({"post_start": self.probe("seed", "{URL}"), "pre_stop": self.probe("bye", "{db_name}")})
        _, rows = self.up("feat-a")
        proc = self.pb("hooks", "feat-a")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("feat-a: hook post_start, last run ok", proc.stdout)
        self.assertIn("http://localhost:%d" % rows[0]["port"], proc.stdout)
        self.assertIn("feat-a: hook pre_stop, never run", proc.stdout)
        self.assertNotIn("values (the same", proc.stdout)

    def test_verbose_prints_every_value_and_json_has_them(self):
        self.config({"post_start": self.probe("seed")})
        _, rows = self.up("feat-a")
        proc = self.pb("hooks", "feat-a", "-v")
        self.assertIn("values (the same as PB_* environment variables)", proc.stdout)
        self.assertRegex(proc.stdout, r"db_name\s+feat_a_[0-9a-f]{4}")
        self.assertRegex(proc.stdout, r"PORT\s+%d" % rows[0]["port"])
        data = json.loads(self.pb("hooks", "--json").stdout)
        self.assertEqual(data[0]["branch"], "feat-a")
        self.assertEqual(data[0]["variables"]["PORT"], rows[0]["port"])
        self.assertEqual(data[0]["hooks"][0]["event"], "post_start")

    def test_marks_user_hooks_and_reads_what_was_stored(self):
        with open(self.user_cfg, "w") as fh:
            json.dump({"hooks": {"post_start": self.probe("mine")}}, fh)
        self.config({"post_start": self.probe("ours")})
        self.up("feat-a")
        out = self.pb("hooks", "feat-a").stdout
        self.assertRegex(out, r"mine.*\(user\)")
        self.assertNotRegex(out, r"ours.*\(user\)")

    def test_writes_nothing_and_handles_no_hooks_and_unknown_branch(self):
        self.up("feat-a")
        before = self.state_text()
        proc = self.pb("hooks")
        self.assertIn("feat-a: no hooks stored", proc.stdout)
        self.assertEqual(self.state_text(), before)
        proc = self.pb("hooks", "nope")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("not tracked", proc.stderr)


class UserConfigTests(HookCase):
    def user_config(self, hook_map=None, **top):
        cfg = dict(top)
        if hook_map is not None:
            cfg["hooks"] = hook_map
        with open(self.user_cfg, "w") as fh:
            json.dump(cfg, fh)

    def test_user_hooks_run_before_the_repo_hooks_and_apply_in_any_repo(self):
        self.user_config({"post_start": self.probe("user")})
        self.config({"post_start": self.probe("project")})
        proc, _ = self.up("feat-a")
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertEqual(self.labels(), ["user", "project"])

    def test_user_hooks_alone_are_enough(self):
        self.user_config({"post_create": self.probe("user_create")})
        self.up("feat-a")
        self.assertEqual(self.labels(), ["user_create"])

    def test_dry_run_labels_user_hooks_and_json_has_the_sources(self):
        self.user_config({"post_start": "echo from-user {branch_slug}"})
        self.config({"post_start": "echo from-project"})
        proc = self.pb("up", "feat-a", "--cmd", SERVER, "--worktree-dir", self.wt, "--dry-run")
        self.assertIn("feat-a: hook post_start (user): echo from-user feat-a", proc.stdout)
        self.assertIn("feat-a: hook post_start: echo from-project", proc.stdout)
        self.assertLess(proc.stdout.index("from-user"), proc.stdout.index("from-project"))
        rows = json.loads(self.pb("up", "feat-a", "--cmd", SERVER, "--worktree-dir", self.wt,
                                  "--dry-run", "--json").stdout)
        self.assertEqual(rows[0]["hook_preview"][0]["sources"], ["user", "project"])
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".git", "parallel-branches")))

    def test_stored_with_the_branch_and_shown_with_labels_at_teardown(self):
        self.user_config({"pre_stop": self.probe("approved_user"), "pre_remove": self.probe("rm_user")})
        self.up("feat-a")
        self.user_config({"pre_stop": self.probe("swapped_in_later")})  # edited after the branch started
        proc = self.pb("hook", "pre_stop", "feat-a", "--dry-run")
        self.assertIn("hook pre_stop (user):", proc.stdout)
        self.pb("down", "feat-a")
        self.assertEqual(self.labels(), ["approved_user"])
        proc = self.pb("clean")
        self.assertIn("would first run hook pre_remove (user):", proc.stdout)
        self.pb("clean", "--yes")
        self.assertEqual(self.labels(), ["approved_user", "rm_user"])

    def test_a_failing_user_pre_start_stops_up_before_the_repo_hook_runs(self):
        self.user_config({"pre_start": FAIL_CMD})
        self.config({"pre_start": self.probe("project")})
        proc, rows = self.up("feat-a")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("hook pre_start", rows[0]["note"])
        self.assertEqual(self.labels(), [])

    def test_no_hooks_skips_user_hooks_and_stores_none(self):
        self.user_config({"pre_stop": self.probe("user")})
        self.up("feat-a", extra=["--no-hooks"])
        self.pb("down", "feat-a")
        self.assertEqual(self.labels(), [])

    def test_timeout_user_then_repo_then_flag(self):
        def stored(*extra):
            self.pb("down", "--all")
            self.up("feat-a", extra=extra)
            return json.loads(self.pb("status", "--json").stdout)[0].get("hook_timeout")
        self.user_config({"pre_stop": "echo x"}, hook_timeout=7)
        self.assertEqual(stored(), 7)
        self.config({"pre_stop": "echo y"}, hook_timeout=9)  # the repo's own value wins over the user's
        self.assertEqual(stored(), 9)
        self.assertEqual(stored("--hook-timeout", "5"), 5)

    def test_a_bad_user_config_stops_everything_before_a_worktree_exists(self):
        for text in ("{nope", json.dumps({"services": []}), json.dumps({"hooks": {"post_merge": "x"}})):
            with open(self.user_cfg, "w") as fh:
                fh.write(text)
            for extra in ([], ["--dry-run"]):
                proc = self.pb("up", "feat-a", "--cmd", SERVER, "--worktree-dir", self.wt, *extra)
                self.assertEqual(proc.returncode, 2, text)
                self.assertIn("bad user config: " + self.user_cfg, proc.stderr)
            self.assertFalse(os.path.exists(os.path.join(self.wt, "feat-a")))

    def test_a_service_name_that_this_repo_lacks_is_reported_against_the_user_file(self):
        self.user_config({"post_start": "curl {url.api}/seed"})
        proc = self.pb("up", "feat-a", "--cmd", SERVER, "--worktree-dir", self.wt, "--dry-run")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("bad hooks in " + self.user_cfg, proc.stderr)
        self.assertIn("unknown service 'api'", proc.stderr)

    def test_the_other_commands_do_not_read_the_user_config(self):
        self.up("feat-a")
        with open(self.user_cfg, "w") as fh:
            fh.write("{nope")  # broken, but only `up` reads it
        for args in (("status",), ("down", "feat-a"), ("clean",)):
            self.assertEqual(self.pb(*args).returncode, 0, args)


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
