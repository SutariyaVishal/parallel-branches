"""Tests for stacks.py. Run: python3 -m unittest discover tests/scripts"""

import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest

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
        self.assertEqual(stacks.render("{PY} a {PORT} {HMR_PORT}", 4001, 4002, self.dir), "python3 a 4001 4002")
        self.write(".venv/bin/python")
        self.assertIn(".venv/bin/python", stacks.render("{PY}", 1, 2, self.dir))


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
        self.assertTrue(os.path.exists(os.path.join(self.wt, "node_modules", "x", "index.js")))
        bad = {"cmd": "exit 4", "clone_from": None, "note": "x"}
        ok, note = stacks.run_install(bad, self.wt, os.path.join(self.dir, "i.log"), 30)
        self.assertFalse(ok)
        self.assertIn("exit 4", note)


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

    def test_clean_project_has_no_risk(self):
        self.write(".env", "NAME=app\nPORT=3000\n")
        self.assertEqual(stacks.scan_risks(self.dir, {4500}), {"cors_risk": False, "findings": []})

    def test_node_modules_and_source_files_are_not_scanned(self):
        self.write("node_modules/pkg/vite.config.js", "http://localhost:1234")
        self.write("src/app.js", "fetch('http://localhost:9999')")
        self.assertFalse(stacks.scan_risks(self.dir, set())["cors_risk"])


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
