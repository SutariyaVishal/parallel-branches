"""Tests for stacks.py. Run: python3 -m unittest discover tests/scripts"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPTS = os.path.join(ROOT, "skills", "parallel-branches", "scripts")
spec = importlib.util.spec_from_file_location("stacks", os.path.join(SCRIPTS, "stacks.py"))
stacks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stacks)


class TmpCase(unittest.TestCase):
    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp(prefix="pb-stacks-"))
        self.addCleanup(shutil.rmtree, self.dir, True)

    def write(self, rel, text="", base=None):
        path = os.path.join(base or self.dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(text)
        return path

    def package(self, scripts=None, deps=None, dev=None, base=None):
        body = {"scripts": scripts if scripts is not None else {"dev": "x"},
                "dependencies": deps or {}, "devDependencies": dev or {}}
        self.write("package.json", json.dumps(body), base)


class DetectNodeTests(TmpCase):
    def test_vite_with_npm(self):
        self.package(dev={"vite": "5"})
        stack = stacks.detect_stack(self.dir)
        self.assertEqual(stack["name"], "vite")
        self.assertEqual(stack["cmd"], "npm run dev -- --port {PORT} --strictPort")
        self.assertEqual(stack["confidence"], "high")

    def test_pnpm_has_no_double_dash(self):
        self.package(dev={"vite": "5"})
        self.write("pnpm-lock.yaml")
        self.assertEqual(stacks.detect_stack(self.dir)["cmd"], "pnpm run dev --port {PORT} --strictPort")

    def test_yarn_and_bun(self):
        self.package(deps={"next": "14"})
        self.write("yarn.lock")
        self.assertEqual(stacks.detect_stack(self.dir)["cmd"], "yarn dev -p {PORT}")
        os.remove(os.path.join(self.dir, "yarn.lock"))
        self.write("bun.lockb")
        self.assertEqual(stacks.detect_stack(self.dir)["cmd"], "bun run dev -p {PORT}")

    def test_meta_frameworks_win_over_vite(self):
        self.package(dev={"vite": "5", "@sveltejs/kit": "2"})
        self.assertEqual(stacks.detect_stack(self.dir)["name"], "sveltekit")
        self.package(dev={"vite": "5", "astro": "4"})
        self.assertEqual(stacks.detect_stack(self.dir)["name"], "astro")

    def test_angular_uses_start_script(self):
        self.package(scripts={"start": "ng serve"}, dev={"@angular/cli": "16"})
        stack = stacks.detect_stack(self.dir)
        self.assertEqual(stack["cmd"], "npm run start -- --port {PORT}")

    def test_create_react_app_is_low_confidence_env_only(self):
        self.package(scripts={"start": "react-scripts start"}, deps={"react-scripts": "5"})
        stack = stacks.detect_stack(self.dir)
        self.assertEqual(stack["cmd"], "npm run start")
        self.assertEqual(stack["confidence"], "low")

    def test_unknown_node_project_with_dev_script_is_low_confidence(self):
        self.package(scripts={"dev": "node server.js"})
        stack = stacks.detect_stack(self.dir)
        self.assertEqual((stack["name"], stack["confidence"]), ("node", "low"))

    def test_node_without_dev_script_is_not_detected(self):
        self.package(scripts={"build": "x"})
        self.assertIsNone(stacks.detect_stack(self.dir))


class DetectOtherStacksTests(TmpCase):
    def test_django(self):
        self.write("manage.py")
        self.assertEqual(stacks.detect_stack(self.dir)["cmd"], "{PY} manage.py runserver 127.0.0.1:{PORT}")

    def test_flask_needs_an_app_file(self):
        self.write("requirements.txt", "Flask==3.0\n")
        self.assertIsNone(stacks.detect_stack(self.dir))
        self.write("app.py", "app = None")
        self.assertEqual(stacks.detect_stack(self.dir)["cmd"], "{PY} -m flask run --port {PORT}")

    def test_fastapi(self):
        self.write("requirements.txt", "fastapi\nuvicorn\n")
        self.write("main.py", "from fastapi import FastAPI\napp = FastAPI()\n")
        self.assertEqual(stacks.detect_stack(self.dir)["cmd"], "{PY} -m uvicorn main:app --port {PORT}")

    def test_rails(self):
        self.write("Gemfile")
        self.write("bin/rails")
        self.assertEqual(stacks.detect_stack(self.dir)["cmd"], "bin/rails server -p {PORT}")

    def test_spring_boot_maven_and_gradle(self):
        self.write("pom.xml", "<parent>spring-boot-starter-parent</parent>")
        stack = stacks.detect_stack(self.dir)
        self.assertEqual((stack["cmd"], stack["env"]), ("mvn spring-boot:run", {"SERVER_PORT": "{PORT}"}))
        os.remove(os.path.join(self.dir, "pom.xml"))
        self.write("build.gradle", "id 'org.springframework.boot'")
        self.write("gradlew")
        self.assertEqual(stacks.detect_stack(self.dir)["cmd"], "./gradlew bootRun")

    def test_go(self):
        self.write("go.mod")
        self.write("main.go")
        stack = stacks.detect_stack(self.dir)
        self.assertEqual((stack["cmd"], stack["confidence"]), ("go run .", "low"))

    def test_nothing_detected(self):
        self.assertIsNone(stacks.detect_stack(self.dir))

    def test_alternatives_are_listed(self):
        self.package(dev={"vite": "5"})
        self.write("manage.py")
        stack = stacks.detect_stack(self.dir)
        self.assertEqual(stack["name"], "vite")
        self.assertEqual(stack["alternatives"], ["django"])

    def test_render_replaces_all_placeholders(self):
        system_python = "python3" if os.name != "nt" else "python"
        self.assertEqual(stacks.render("{PY} a {PORT} {HMR_PORT}", 4001, 4002, self.dir),
                         system_python + " a 4001 4002")
        venv_python = os.path.join(".venv", "Scripts", "python.exe") if os.name == "nt" \
            else os.path.join(".venv", "bin", "python")
        self.write(venv_python.replace(os.sep, "/"))
        self.assertIn(venv_python, stacks.render("{PY}", 1, 2, self.dir))

    def test_python_path_with_spaces_is_quoted(self):
        spaced = os.path.join(self.dir, "my project")
        venv_python = os.path.join(".venv", "Scripts", "python.exe") if os.name == "nt" \
            else os.path.join(".venv", "bin", "python")
        self.write(venv_python.replace(os.sep, "/"), base=spaced)
        rendered = stacks.render("{PY} manage.py", 1, 2, spaced)
        self.assertTrue(rendered.startswith(("'", '"')), rendered)
        self.assertIn("my project", rendered)


class InstallPlanTests(TmpCase):
    def setUp(self):
        super().setUp()
        self.main = os.path.join(self.dir, "main")
        self.wt = os.path.join(self.dir, "wt")
        os.makedirs(self.main)
        os.makedirs(self.wt)
        self.node = {"family": "node"}

    def test_npm_ci_when_lockfile_exists(self):
        self.write("package-lock.json", "{}", self.wt)
        self.assertEqual(stacks.plan_install(self.wt, self.main, self.node)["cmd"], "npm ci")

    def test_npm_install_without_lockfile(self):
        self.assertEqual(stacks.plan_install(self.wt, self.main, self.node)["cmd"], "npm install")

    def test_pnpm_and_yarn_commands(self):
        self.write("pnpm-lock.yaml", "", self.wt)
        self.assertEqual(stacks.plan_install(self.wt, self.main, self.node)["cmd"], "pnpm install --frozen-lockfile")
        os.remove(os.path.join(self.wt, "pnpm-lock.yaml"))
        self.write("yarn.lock", "", self.wt)
        self.assertEqual(stacks.plan_install(self.wt, self.main, self.node)["cmd"], "yarn install --frozen-lockfile")
        self.write(".yarnrc.yml", "", self.wt)
        self.assertEqual(stacks.plan_install(self.wt, self.main, self.node)["cmd"], "yarn install --immutable")

    def test_clone_node_modules_when_lockfile_is_identical(self):
        self.write("package-lock.json", '{"a":1}', self.main)
        self.write("package-lock.json", '{"a":1}', self.wt)
        self.write("node_modules/x/index.js", "", self.main)
        plan = stacks.plan_install(self.wt, self.main, self.node)
        self.assertEqual(plan["clone_from"], os.path.join(self.main, "node_modules"))
        self.assertEqual(plan["cmd"], "npm ci")  # fallback if the clone fails

    def test_no_clone_when_lockfile_differs_or_pnpm(self):
        self.write("package-lock.json", '{"a":1}', self.main)
        self.write("package-lock.json", '{"a":2}', self.wt)
        self.write("node_modules/x/index.js", "", self.main)
        self.assertIsNone(stacks.plan_install(self.wt, self.main, self.node)["clone_from"])
        self.write("pnpm-lock.yaml", "x", self.main)
        self.write("pnpm-lock.yaml", "x", self.wt)
        self.assertIsNone(stacks.plan_install(self.wt, self.main, self.node)["clone_from"])

    def test_skip_when_already_installed_unless_fresh(self):
        self.write("node_modules/x/index.js", "", self.wt)
        self.assertIsNone(stacks.plan_install(self.wt, self.main, self.node))
        self.assertIsNotNone(stacks.plan_install(self.wt, self.main, self.node, fresh=True))

    def test_python_requirements_makes_a_venv(self):
        self.write("requirements.txt", "flask", self.wt)
        plan = stacks.plan_install(self.wt, self.main, {"family": "python"})
        self.assertIn(".venv", plan["cmd"])
        self.assertIn("requirements.txt", plan["cmd"])
        if not shutil.which("uv"):
            self.assertIn("-m pip", plan["cmd"])  # no hardcoded bin/ or Scripts/ pip path

    def test_other_families(self):
        self.write("Gemfile.lock", "", self.wt)
        self.assertEqual(stacks.plan_install(self.wt, self.main, {"family": "ruby"})["cmd"], "bundle install")
        self.assertIsNone(stacks.plan_install(self.wt, self.main, {"family": "go"}))
        self.assertIsNone(stacks.plan_install(self.wt, self.main, None))

    def test_run_install_clones_and_falls_back(self):
        self.write("node_modules/x/index.js", "hi", self.main)
        plan = {"cmd": "echo fallback", "clone_from": os.path.join(self.main, "node_modules"), "note": "cloned"}
        ok, note = stacks.run_install(plan, self.wt, os.path.join(self.dir, "i.log"), 30)
        self.assertTrue(ok)
        cloned = os.path.exists(os.path.join(self.wt, "node_modules", "x", "index.js"))
        if sys.platform == "darwin" or sys.platform.startswith("linux"):
            self.assertTrue(cloned or "installed instead" in note)  # a non-CoW filesystem may fall back
        else:
            self.assertFalse(cloned)  # no clone on Windows: the install command must have run instead
        if not cloned:
            self.assertIn("installed instead: echo fallback", note)  # and the note must not claim a clone
        else:
            self.assertEqual(note, "cloned")
        bad = {"cmd": "exit 4", "clone_from": None, "note": "x"}
        ok, note = stacks.run_install(bad, self.wt, os.path.join(self.dir, "i.log"), 30)
        self.assertFalse(ok)
        self.assertIn("exit 4", note)


class EncodingTests(TmpCase):
    # "Á" is C3 81 in UTF-8; 0x81 is undefined in cp1252, so a default-encoding read fails on Windows.
    def test_json_and_text_are_read_as_utf8(self):
        path = os.path.join(self.dir, "package.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('{"name": "demo", "description": "\u00c1\u2713"}')
        self.assertEqual(stacks.read_json(path)["description"], "\u00c1\u2713")
        self.assertIn("\u00c1\u2713", stacks.read_text(path))


class EnvCopyTests(TmpCase):
    def setUp(self):
        super().setUp()
        self.main = os.path.join(self.dir, "main")
        self.wt = os.path.join(self.dir, "wt")
        os.makedirs(self.main)
        os.makedirs(self.wt)
        subprocess.run(["git", "init", "-q", self.main], check=True)

    def test_copies_only_ignored_env_files_and_never_overwrites(self):
        self.write(".gitignore", ".env\n.env.local\nsecrets/\n", self.main)
        self.write(".env", "A=1", self.main)
        self.write(".env.local", "B=2", self.main)
        self.write(".env.example", "C=3", self.main)  # not ignored -> tracked, so not copied
        self.write("secrets/key.txt", "k", self.main)
        self.write(".worktreeinclude", "# comment\nsecrets/*.txt\n", self.main)
        self.write(".env.local", "KEEP", self.wt)
        copied = stacks.copy_env_files(self.main, self.wt)
        self.assertEqual(sorted(copied), [".env", os.path.join("secrets", "key.txt")])
        with open(os.path.join(self.wt, ".env.local")) as fh:
            self.assertEqual(fh.read(), "KEEP")
        self.assertFalse(os.path.exists(os.path.join(self.wt, ".env.example")))

    def test_config_patterns(self):
        self.write(".gitignore", "local.json\n", self.main)
        self.write("local.json", "{}", self.main)
        self.assertEqual(stacks.copy_env_files(self.main, self.wt, ["local.json"]), ["local.json"])

    def test_ignored_directories_are_copied_whole_and_tracked_ones_are_not(self):
        self.write(".gitignore", ".cache/\n", self.main)
        self.write(".cache/a/b.bin", "x", self.main)
        self.write("src/keep.txt", "t", self.main)  # not ignored: tracked content, never copied
        self.write(".worktreeinclude", ".cache/\nsrc\n.cache/a/b.bin\n", self.main)
        copied = stacks.copy_env_files(self.main, self.wt)
        self.assertEqual(copied, [".cache" + os.sep])  # the file inside is not listed again
        self.assertTrue(os.path.isfile(os.path.join(self.wt, ".cache", "a", "b.bin")))
        self.assertFalse(os.path.exists(os.path.join(self.wt, "src")))

    def test_plan_changes_nothing_and_matches_what_is_copied(self):
        self.write(".gitignore", ".env\nout/\n", self.main)
        self.write(".env", "A=1", self.main)
        self.write("out/x", "1", self.main)
        plan = stacks.copy_plan(self.main, self.wt, ["out/"])
        self.assertEqual(plan, [".env", "out" + os.sep])
        self.assertEqual(os.listdir(self.wt), [])
        self.assertEqual(stacks.copy_env_files(self.main, self.wt, ["out/"]), plan)

    def test_existing_directory_in_the_worktree_is_left_alone(self):
        self.write(".gitignore", "out/\n", self.main)
        self.write("out/x", "main", self.main)
        self.write("out/x", "mine", self.wt)
        self.assertEqual(stacks.copy_env_files(self.main, self.wt, ["out/"]), [])
        with open(os.path.join(self.wt, "out", "x")) as fh:
            self.assertEqual(fh.read(), "mine")


class SharedDatabaseTests(TmpCase):
    def test_driver_jdbc_and_mariadb_schemes(self):
        for url in ("postgresql+psycopg://db/x", "postgresql+asyncpg://db/x", "mysql+pymysql://db/x",
                    "jdbc:postgresql://db/x", "jdbc:mysql://db/x", "mariadb://db/x"):
            with self.subTest(url=url):
                self.write(".env", "CUSTOM=" + url)
                self.assertEqual(stacks.shared_database_variables(self.dir, [".env"]),
                                 [{"file": ".env", "variable": "CUSTOM"}])

    def test_database_name_keys_but_not_host_only_settings(self):
        keys = ("DB_NAME", "DB_DATABASE", "PGDATABASE", "POSTGRES_DB", "MYSQL_DATABASE")
        self.write(".envrc", "export DB_HOST=localhost\n" + "\n".join(key + "=synthetic" for key in keys))
        self.assertEqual([w["variable"] for w in stacks.shared_database_variables(self.dir, [".envrc"])], list(keys))

    def test_copied_directories_expand_env_files_and_deduplicate(self):
        self.write("config/nested/.env", "DB_NAME=synthetic")
        self.write("config/.envrc", "export CUSTOM=jdbc:mysql://db/x")
        self.write("config/not-env.txt", "DATABASE_URL=postgres://db/x")
        files = ["config" + os.sep, os.path.join("config", "nested", ".env")]
        self.assertEqual(stacks.shared_database_variables(self.dir, files), [
            {"file": os.path.join("config", ".envrc"), "variable": "CUSTOM"},
            {"file": os.path.join("config", "nested", ".env"), "variable": "DB_NAME"}])

    def test_copied_directory_scan_prunes_dependency_and_cache_directories(self):
        self.write("config/.local/.env", "DB_NAME=synthetic")
        self.write("config/app/.env", "DB_NAME=synthetic")
        for name in stacks.SKIP_DIRS:
            self.write(os.path.join("config", name, "nested", ".env"), "DB_NAME=synthetic")
        with mock.patch.object(stacks.os, "scandir", wraps=os.scandir) as scandir:
            warnings = stacks.shared_database_variables(self.dir, ["config" + os.sep])
        self.assertEqual(warnings, [
            {"file": os.path.join("config", ".local", ".env"), "variable": "DB_NAME"},
            {"file": os.path.join("config", "app", ".env"), "variable": "DB_NAME"}])
        visited = [os.path.relpath(call.args[0], self.dir).split(os.sep) for call in scandir.call_args_list]
        self.assertFalse(any(stacks.SKIP_DIRS.intersection(parts) for parts in visited))

    def test_explicit_copied_dependency_directories_are_not_walked(self):
        for name in ("node_modules", ".venv"):
            self.write(os.path.join(name, "nested", ".env"), "DB_NAME=synthetic")
            self.write(os.path.join("config", name, "nested", ".env"), "DB_NAME=synthetic")
            for rel in (name, os.path.join("config", name), os.path.join(name, "nested")):
                with self.subTest(directory=rel), mock.patch.object(stacks.os, "walk", wraps=os.walk) as walk:
                    self.assertEqual(stacks.shared_database_variables(self.dir, [rel + os.sep]), [])
                    walk.assert_not_called()

    def test_explicit_env_file_in_dependency_directory_is_still_scanned(self):
        rel = os.path.join("node_modules", "example", ".env")
        self.write(rel, "DB_NAME=synthetic")
        self.assertEqual(stacks.shared_database_variables(self.dir, [rel]),
                         [{"file": rel, "variable": "DB_NAME"}])

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_scan_does_not_follow_file_directory_or_ancestor_symlinks(self):
        self.write("elsewhere/.env", "DATABASE_URL=postgres://synthetic/x")
        self.write("config/.env", "PORT=3000")
        try:
            os.symlink(os.path.join(self.dir, "elsewhere"), os.path.join(self.dir, "config", "link"),
                       target_is_directory=True)
            os.symlink(os.path.join(self.dir, "elsewhere", ".env"), os.path.join(self.dir, "config", ".env.link"))
        except OSError as exc:
            self.skipTest(str(exc))
        self.assertEqual(stacks.shared_database_variables(self.dir, ["config/", "config/link/.env",
                                                                    "config/.env.link", "../elsewhere/.env"]), [])

    def test_names_and_schemes_without_values_or_duplicates(self):
        self.write(".env", "export DATABASE_URL='synthetic-secret'\nCUSTOM=postgres://u:synthetic-secret@db/x\n"
                   "MONGO_URI=mongodb://db/x\nCUSTOM=postgres://db/x\nREDIS_URL=redis://db\n")
        self.assertEqual(stacks.shared_database_variables(self.dir, [".env", ".env"]), [
            {"file": ".env", "variable": key} for key in ("DATABASE_URL", "CUSTOM", "MONGO_URI", "REDIS_URL")])

    def test_ordinary_comments_non_env_and_missing_files_are_ignored(self):
        self.write(".env", "# DATABASE_URL=secret\nTEXT=contains postgres://example\nPORT=3000\n"
                   "DATABASE_URL=\nDB_URL= # comment\nREDIS_URL=\"\"\nMONGO_URI=''\n")
        self.write("local.json", "DATABASE_URL=secret")
        self.assertEqual(stacks.shared_database_variables(self.dir, [".env", "local.json", ".env.missing"]), [])

    def test_nested_env_and_other_schemes(self):
        self.write("config/local.env", "A=\"mysql://db/x\"\nB=mongodb+srv://db/x\nC=rediss://db\nD=postgresql://db\n")
        self.assertEqual([w["variable"] for w in stacks.shared_database_variables(self.dir, ["config/local.env"])],
                         ["A", "B", "C", "D"])


class RiskScanTests(TmpCase):
    def test_other_local_port_is_flagged_and_own_port_is_not(self):
        self.write(".env", "API=http://localhost:9999/v1\nSELF=http://localhost:4500\n")
        out = stacks.scan_risks(self.dir, {4500, 4501})
        self.assertTrue(out["cors_risk"])
        self.assertEqual([f["kind"] for f in out["findings"]], ["other-local-port"])
        self.assertEqual(out["findings"][0]["line"], 1)

    def test_remote_origin_is_flagged_and_doc_hosts_are_ignored(self):
        self.write("src/environments/environment.ts",
                   "api: 'https://api.example-corp.io:8443',\nschema: 'http://json-schema.org/draft-07/schema#'\n")
        out = stacks.scan_risks(self.dir, set())
        self.assertEqual([f["kind"] for f in out["findings"]], ["remote-origin"])

    def test_auth_lines_are_tagged(self):
        self.write(".env.local", "REDIRECT_URI=http://localhost:4200/callback\nAPI=http://localhost:8080\n")
        findings = stacks.scan_risks(self.dir, {4500})["findings"]
        self.assertEqual([f["auth"] for f in findings], [True, False])

    def test_duplicates_are_dropped_and_what_a_dev_browser_cannot_fix_comes_first(self):
        self.write(".env", "A=https://api.example-corp.io\nB=http://localhost:9999 http://localhost:9999\n"
                           "C=http://localhost:4200/callback\n")
        findings = stacks.scan_risks(self.dir, set())["findings"]
        self.assertEqual(len(findings), 3)  # the repeated URL on line 2 counts once
        self.assertTrue(findings[0]["auth"])  # redirect first
        self.assertEqual([f["kind"] for f in findings][1:], ["other-local-port", "remote-origin"])

    def test_many_hosts_do_not_hide_later_files(self):
        self.write("a/.env", "".join("H%d=http://h%d.example-corp.io\n" % (i, i) for i in range(30)))
        self.write(".env", "AUTH=http://localhost:4200/callback\n")
        urls = [f["url"] for f in stacks.scan_risks(self.dir, set())["findings"]]
        self.assertIn("http://localhost:4200/callback", urls)

    def test_clean_project_has_no_risk(self):
        self.write(".env", "NAME=app\nPORT=3000\n")
        self.assertEqual(stacks.scan_risks(self.dir, {4500}), {"cors_risk": False, "findings": []})

    def test_node_modules_and_source_files_are_not_scanned(self):
        self.write("node_modules/pkg/vite.config.js", "http://localhost:1234")
        self.write("src/app.js", "fetch('http://localhost:9999')")
        self.assertFalse(stacks.scan_risks(self.dir, set())["cors_risk"])


class ServicesTests(unittest.TestCase):
    def svc(self, name, **extra):
        return dict({"name": name, "cmd": "run"}, **extra)

    def test_no_services_key_means_single_service(self):
        self.assertIsNone(stacks.parse_services({"cmd": "x"}))

    def test_start_order_follows_depends_on_and_primary_defaults_to_last(self):
        order, primary = stacks.parse_services({"services": [
            self.svc("web", depends_on=["api"]), self.svc("api", depends_on=["db"]), self.svc("db")]})
        self.assertEqual([s["name"] for s in order], ["db", "api", "web"])
        self.assertEqual(primary, "db")  # last in the list, not last started

    def test_explicit_primary(self):
        _, primary = stacks.parse_services({"services": [self.svc("api", primary=True), self.svc("web")]})
        self.assertEqual(primary, "api")

    def test_invalid_configs_are_rejected_with_a_reason(self):
        cases = {
            "non-empty list": {"services": []},
            "must be an object": {"services": ["api"]},
            "unknown key": {"services": [self.svc("api", port=1)]},
            "lowercase": {"services": [self.svc("API")]},
            "duplicate": {"services": [self.svc("a"), self.svc("a")]},
            "relative path inside": {"services": [self.svc("a", cwd="../x")]},
            "relative path inside ": {"services": [self.svc("a", cwd="/abs")]},
            "http": {"services": [self.svc("a", health="udp")]},
            "unknown service": {"services": [self.svc("a", depends_on=["zzz"])]},
            "cycle": {"services": [self.svc("a", depends_on=["b"]), self.svc("b", depends_on=["a"])]},
            "refers to unknown": {"services": [self.svc("a", cmd="x {url.nope}")]},
            "only one service can be primary": {"services": [self.svc("a", primary=True), self.svc("b", primary=True)]},
        }
        for fragment, cfg in cases.items():
            with self.assertRaises(ValueError, msg=fragment) as ctx:
                stacks.parse_services(cfg)
            self.assertIn(fragment.strip(), str(ctx.exception))

    def test_render_fills_other_services_ports_and_urls(self):
        ports = {"api": (4100, 4101), "web": (4200, 4201)}
        text = stacks.render("{PORT} {ports.api} {url.api} {url.web}", 4200, 4201, "/tmp", ports)
        self.assertEqual(text, "4200 4100 http://localhost:4100 http://localhost:4200")

    def test_cross_service_refs_ignores_self_references(self):
        pairs = stacks.cross_service_refs([
            self.svc("web", env={"API": "{url.api}"}), self.svc("api", cmd="serve {ports.api}")])
        self.assertEqual(pairs, [("web", "api")])


class MonorepoTests(TmpCase):
    def app(self, rel, scripts=None, dev=None):
        self.write(rel + "/package.json", json.dumps({"name": rel.split("/")[-1], "scripts": scripts or {"dev": "x"},
                                                      "devDependencies": dev or {}}))

    def test_npm_workspaces_find_apps_and_order_backend_first(self):
        self.write("package.json", json.dumps({"private": True, "workspaces": ["apps/*", "packages/*"]}))
        self.app("apps/web", dev={"vite": "5"})
        self.app("apps/api")
        self.write("packages/ui/package.json", json.dumps({"name": "ui", "scripts": {"build": "tsc"}}))  # a library
        found = stacks.detect_services(self.dir)
        self.assertEqual([s["name"] for s in found["services"]], ["api", "web"])
        self.assertEqual(found["services"][1]["cwd"], "apps/web")
        self.assertEqual(found["services"][1]["cmd"], "npm run dev -- --port {PORT} --strictPort")
        self.assertTrue(any("api:" in n for n in found["notes"]))  # low confidence api is called out

    def test_pnpm_workspace_yaml_with_exclusion_and_lerna(self):
        self.write("pnpm-workspace.yaml", "packages:\n  - 'apps/*'\n  - '!apps/legacy'\nother: 1\n")
        for name in ("web", "api", "legacy"):
            self.app("apps/" + name)
        names = [s["name"] for s in stacks.detect_services(self.dir)["services"]]
        self.assertEqual(sorted(names), ["api", "web"])
        os.remove(os.path.join(self.dir, "pnpm-workspace.yaml"))
        self.write("lerna.json", json.dumps({"packages": ["apps/web", "apps/api"]}))
        self.assertEqual(len(stacks.detect_services(self.dir)["services"]), 2)

    def test_conventional_layout_without_workspaces(self):
        self.app("frontend", dev={"vite": "5"})
        self.write("backend/manage.py")
        self.write("docs/readme.md")
        found = stacks.detect_services(self.dir)
        self.assertEqual([(s["name"], s["cwd"]) for s in found["services"]],
                         [("backend", "backend"), ("frontend", "frontend")])
        self.assertEqual(found["services"][0]["cmd"], "{PY} manage.py runserver 127.0.0.1:{PORT}")

    def test_single_app_repos_are_not_monorepos(self):
        self.app(".", dev={"vite": "5"})
        self.assertIsNone(stacks.detect_services(self.dir))

    def test_root_app_with_one_subapp_is_left_alone_but_a_lone_subapp_is_suggested(self):
        self.app(".", dev={"vite": "5"})
        self.app("apps/helper")
        self.assertIsNone(stacks.detect_services(self.dir))
        os.remove(os.path.join(self.dir, "package.json"))
        found = stacks.detect_services(self.dir)
        self.assertEqual([s["name"] for s in found["services"]], ["helper"])

    def test_root_dev_script_in_a_workspace_repo_is_flagged(self):
        self.write("package.json", json.dumps({"workspaces": ["apps/*"], "scripts": {"dev": "turbo run dev"}}))
        self.app("apps/web")
        self.app("apps/api")
        notes = stacks.detect_services(self.dir)["notes"]
        self.assertTrue(any("root" in n and "npm run dev" in n for n in notes))

    def test_names_are_sanitized_deduplicated_and_capped(self):
        self.write("package.json", json.dumps({"workspaces": ["apps/*/web"]}))
        self.app("apps/a/web")
        self.app("apps/b/web")
        names = [s["name"] for s in stacks.detect_services(self.dir)["services"]]
        self.assertEqual(sorted(names), ["web", "web-2"])
        self.write("package.json", json.dumps({"workspaces": ["svc/*"]}))
        for i in range(10):
            self.app("svc/Svc_%d" % i)
        found = stacks.detect_services(self.dir)
        self.assertEqual(len(found["services"]), stacks.MAX_SERVICES)
        self.assertTrue(any("only the first" in n for n in found["notes"]))
        self.assertTrue(all(stacks.SERVICE_NAME_RE.match(s["name"]) for s in found["services"]))

    def test_suggested_config_is_valid_and_has_no_hint_keys(self):
        self.write("package.json", json.dumps({"workspaces": ["apps/*"]}))
        self.app("apps/web", dev={"vite": "5"})
        self.app("apps/api")
        config = stacks.suggested_config(stacks.detect_services(self.dir))
        self.assertTrue(all(not k.startswith("_") for s in config["services"] for k in s))
        order, primary = stacks.parse_services(config)
        self.assertEqual((len(order), primary), (2, "web"))


class LockfileHashTests(TmpCase):
    def test_line_endings_do_not_change_the_hash(self):
        lf = self.write("lf.lock", "a\nb\n")
        crlf = os.path.join(self.dir, "crlf.lock")
        with open(crlf, "wb") as fh:
            fh.write(b"a\r\nb\r\n")
        self.assertEqual(stacks.file_hash(lf), stacks.file_hash(crlf))
        self.assertEqual(stacks.file_hash(lf), stacks.digest_bytes(b"a\r\nb\r\n"))

    def test_different_content_still_differs_and_missing_files_are_none(self):
        a = self.write("a.lock", "x\n")
        b = self.write("b.lock", "y\n")
        self.assertNotEqual(stacks.file_hash(a), stacks.file_hash(b))
        self.assertIsNone(stacks.file_hash(os.path.join(self.dir, "nope.lock")))


class ConfigTests(TmpCase):
    def test_first_config_wins_and_bad_json_is_ignored(self):
        other = os.path.join(self.dir, "other")
        os.makedirs(other)
        self.write(".parallel-branches.json", '{"cmd": "a"}', other)
        self.assertEqual(stacks.load_config(self.dir, other), {"cmd": "a"})
        self.write(".parallel-branches.json", "{broken")
        self.assertEqual(stacks.load_config(self.dir), {})


if __name__ == "__main__":
    unittest.main()
