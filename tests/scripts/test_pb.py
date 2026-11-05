"""Tests for skills/parallel-branches/scripts/pb.py. Run: python3 -m unittest discover tests/scripts"""

import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PB = os.path.join(ROOT, "skills", "parallel-branches", "scripts", "pb.py")
SERVER = "python3 -m http.server {PORT} --bind 127.0.0.1"
GROUP_SERVER = "sh -c 'python3 -m http.server {PORT} --bind 127.0.0.1 & wait'"

spec = importlib.util.spec_from_file_location("pb", PB)
pb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pb)


def http_ok(port):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=2) as resp:
            return resp.status == 200
    except OSError:
        return False


def listening_socket(port):
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", port))
        sock.listen(1)
    except OSError:
        sock.close()
        raise
    return sock


class UnitTests(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(pb.slugify("feature/AS-123 login!"), "feature-AS-123-login")
        self.assertEqual(pb.slugify("///"), "branch")

    def test_preferred_port_is_stable_and_in_range(self):
        port = pb.preferred_port("feature/x")
        self.assertEqual(port, pb.preferred_port("feature/x"))
        self.assertTrue(pb.PORT_BASE <= port < pb.PORT_BASE + pb.PORT_SPAN)

    def test_allocate_skips_busy_port(self):
        want = pb.preferred_port("busy-branch")
        sock = listening_socket(want)
        try:
            port, hmr = pb.allocate_ports("busy-branch", {"branches": {}})
        finally:
            sock.close()
        self.assertNotIn(want, (port, hmr))
        self.assertEqual(hmr, port + 1)

    def test_allocate_skips_ports_claimed_by_other_branches(self):
        want = pb.preferred_port("claimed-branch")
        state = {"branches": {"other": {"port": want, "hmr_port": want + 1}}}
        port, hmr = pb.allocate_ports("claimed-branch", state)
        self.assertNotIn(port, (want, want + 1))
        self.assertNotIn(hmr, (want, want + 1))

    def test_allocate_keeps_own_ports(self):
        want = pb.preferred_port("mine")
        state = {"branches": {"mine": {"port": want, "hmr_port": want + 1}}}
        self.assertEqual(pb.allocate_ports("mine", state)[0], want)

    def test_allocate_never_returns_reserved_ports(self):
        for name in ("a", "b", "c", "d", "e", "f"):
            port, hmr = pb.allocate_ports(name, {"branches": {}})
            self.assertFalse({port, hmr} & pb.RESERVED_PORTS)

    def test_corrupt_state_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "state.json"), "w") as fh:
                fh.write("{not json")
            self.assertEqual(pb.load_state(tmp)["branches"], {})

    def test_state_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            pb.save_state(tmp, {"version": 1, "branches": {"x": {"port": 4001}}})
            self.assertEqual(pb.load_state(tmp)["branches"]["x"]["port"], 4001)

    def test_render_command(self):
        self.assertEqual(pb.render_command("dev -p {PORT} --hmr {HMR_PORT}", 4001, 4002),
                         "dev -p 4001 --hmr 4002")


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="pb-test-"))
        self.repo = os.path.join(self.tmp, "proj")
        os.makedirs(self.repo)
        self.git("init", "-q", "-b", "main")
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init")
        for name in ("feat-a", "feat-b"):
            self.git("branch", name)
        self.wt = os.path.join(self.tmp, "wts")

    def tearDown(self):
        self.pb("down", "--all")
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args, cwd=None):
        return subprocess.run(["git", "-C", cwd or self.repo] + list(args), check=True,
                              stdout=subprocess.PIPE, universal_newlines=True).stdout

    def pb(self, *args):
        return subprocess.run([sys.executable, PB, "--repo", self.repo] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)

    def up(self, *branches, cmd=SERVER, extra=()):
        proc = self.pb("up", *branches, "--cmd", cmd, "--worktree-dir", self.wt,
                       "--timeout", "20", "--json", *extra)
        return proc, (json.loads(proc.stdout) if proc.stdout.strip().startswith("[") else None)

    def state_path(self):
        return os.path.join(self.repo, ".git", "parallel-branches", "state.json")

    def test_two_branches_run_side_by_side(self):
        proc, rows = self.up("feat-a", "feat-b")
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        ports = [r["port"] for r in rows]
        self.assertEqual(len(set(ports)), 2)
        for row in rows:
            self.assertTrue(http_ok(row["port"]))
            self.assertEqual(self.git("branch", "--show-current", cwd=row["path"]).strip(), row["branch"])
        status = json.loads(self.pb("status", "--json").stdout)
        self.assertEqual({r["live"] for r in status}, {"healthy"})

    def test_down_stops_the_whole_process_group(self):
        _, rows = self.up("feat-a", cmd=GROUP_SERVER)
        port = rows[0]["port"]
        self.assertTrue(http_ok(port))
        self.assertIn("stopped", self.pb("down", "feat-a").stdout)
        self.assertFalse(http_ok(port))
        self.assertTrue(pb.port_is_free(port))
        self.assertEqual(json.loads(self.pb("status", "--json").stdout)[0]["live"], "stopped")

    def test_failed_start_reports_log_and_exits_nonzero(self):
        proc, rows = self.up("feat-a", cmd="echo boom >&2; exit 3")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("boom", rows[0]["note"])
        self.assertIn("exited with code 3", rows[0]["note"])
        self.assertEqual(json.loads(self.pb("status", "--json").stdout)[0]["live"], "failed")

    def test_up_twice_is_idempotent(self):
        _, first = self.up("feat-a")
        _, second = self.up("feat-a")
        self.assertEqual(second[0]["note"], "already running")
        self.assertEqual(first[0]["pid"], second[0]["pid"])
        self.assertEqual(first[0]["port"], second[0]["port"])

    def test_dry_run_starts_nothing(self):
        proc, rows = self.up("feat-a", extra=("--dry-run",))
        self.assertEqual(proc.returncode, 0)
        self.assertIn("dry run", rows[0]["note"])
        self.assertFalse(os.path.exists(self.wt))
        self.assertTrue(pb.port_is_free(rows[0]["port"]))

    def test_unknown_branch_needs_create_flag(self):
        proc, _ = self.up("nope")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--create", proc.stderr)
        proc, rows = self.up("nope", extra=("--create",))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(http_ok(rows[0]["port"]))

    def test_up_avoids_a_port_someone_else_holds(self):
        want = pb.preferred_port("feat-a")
        sock = listening_socket(want)
        try:
            _, rows = self.up("feat-a")
        finally:
            sock.close()
        self.assertNotEqual(rows[0]["port"], want)
        self.assertTrue(http_ok(rows[0]["port"]))

    def test_down_refuses_when_pid_was_reused(self):
        _, rows = self.up("feat-a")
        pid, port = rows[0]["pid"], rows[0]["port"]
        with open(self.state_path()) as fh:
            state = json.load(fh)
        real = state["branches"]["feat-a"]["token"]
        state["branches"]["feat-a"]["token"] = "Thu Jan  1 00:00:00 1970"
        with open(self.state_path(), "w") as fh:
            json.dump(state, fh)
        out = self.pb("down", "feat-a").stdout
        self.assertIn("not killing", out)
        os.kill(pid, 0)  # still alive
        self.assertTrue(http_ok(port))
        state["branches"]["feat-a"]["token"] = real
        with open(self.state_path(), "w") as fh:
            json.dump(state, fh)

    def test_clean_is_dry_run_without_yes(self):
        _, rows = self.up("feat-a")
        self.pb("down", "feat-a")
        out = self.pb("clean").stdout
        self.assertIn("rerun with --yes", out)
        self.assertTrue(os.path.isdir(rows[0]["path"]))

    def test_clean_skips_dirty_worktree_unless_forced(self):
        _, rows = self.up("feat-a")
        path = rows[0]["path"]
        self.pb("down", "feat-a")
        with open(os.path.join(path, "scratch.txt"), "w") as fh:
            fh.write("uncommitted")
        self.assertIn("uncommitted changes", self.pb("clean", "--yes").stdout)
        self.assertTrue(os.path.isdir(path))
        self.assertIn("removed worktree", self.pb("clean", "--yes", "--force").stdout)
        self.assertFalse(os.path.isdir(path))

    def test_clean_leaves_running_and_foreign_worktrees(self):
        _, rows = self.up("feat-a")
        self.assertIn("still running", self.pb("clean", "--yes").stdout)
        self.assertTrue(os.path.isdir(rows[0]["path"]))
        # main's own branch is reused, not created, so clean must never remove it
        _, main_rows = self.up("main")
        self.assertEqual(main_rows[0]["path"], os.path.realpath(self.repo))
        self.pb("down", "--all")
        self.assertIn("not created by parallel-branches", self.pb("clean", "--yes").stdout)
        self.assertTrue(os.path.isdir(self.repo))


if __name__ == "__main__":
    unittest.main()
