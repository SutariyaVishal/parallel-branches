"""Stack detection, dependency install planning, env-file copying and risk scanning.

Used by pb.py. Python 3.9+, standard library only. Nothing here starts a server.
"""

import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

CONFIG_NAME = ".parallel-branches.json"

# (package name, label, flags appended to the dev script). First match wins, so
# meta-frameworks that sit on top of Vite come before Vite itself.
NODE_FRAMEWORKS = [
    ("next", "next", "-p {PORT}"),
    ("nuxt", "nuxt", "--port {PORT}"),
    ("@angular/cli", "angular", "--port {PORT}"),
    ("astro", "astro", "--port {PORT}"),
    ("@sveltejs/kit", "sveltekit", "--port {PORT} --strictPort"),
    ("react-scripts", "create-react-app", None),  # reads PORT only
    ("vite", "vite", "--port {PORT} --strictPort"),
    ("webpack-dev-server", "webpack-dev-server", "--port {PORT}"),
]
NODE_LOCKFILES = [
    ("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"), ("bun.lockb", "bun"),
    ("bun.lock", "bun"), ("package-lock.json", "npm"),
]
SKIP_DIRS = {"node_modules", ".git", "dist", "build", ".venv", "venv", "vendor", "target",
             "__pycache__", ".next", ".nuxt", ".angular", "coverage"}
# Hosts that show up in config files but are never an API the app calls.
IGNORED_HOSTS = ("w3.org", "json-schema.org", "schema.org", "apache.org", "npmjs.org",
                 "npmjs.com", "github.com", "example.com", "example.org", "mozilla.org")
AUTH_WORDS = re.compile(r"redirect|callback|oauth|oidc|issuer|login|logout|keycloak|auth0|saml", re.I)
URL_RE = re.compile(r"https?://([A-Za-z0-9._-]+)(?::(\d{2,5}))?[^\s'\"`)>,;]*")


def read_text(path, limit=200000):
    try:
        with open(path, errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return ""


def read_json(path):
    try:
        with open(path) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def exists(path, *parts):
    return os.path.exists(os.path.join(path, *parts))


def load_config(*dirs):
    """First .parallel-branches.json found in `dirs`, as a dict ({} if none)."""
    for directory in dirs:
        cfg = read_json(os.path.join(directory, CONFIG_NAME))
        if cfg:
            return cfg
    return {}


# ------------------------------------------------------------ stack detection

def node_manager(path):
    for lockfile, manager in NODE_LOCKFILES:
        if exists(path, lockfile):
            return manager, lockfile
    return "npm", None


def detect_node(path):
    pkg = read_json(os.path.join(path, "package.json"))
    if not pkg:
        return None
    scripts = pkg.get("scripts") or {}
    script = next((s for s in ("dev", "start", "serve") if s in scripts), None)
    if not script:
        return None
    deps = dict(pkg.get("dependencies") or {})
    deps.update(pkg.get("devDependencies") or {})
    label, flags = "node", None
    if exists(path, "angular.json"):
        deps.setdefault("@angular/cli", "*")
    for package, name, framework_flags in NODE_FRAMEWORKS:
        if package in deps:
            label, flags = name, framework_flags
            break
    manager, _ = node_manager(path)
    run = {"npm": "npm run %s", "yarn": "yarn %s", "pnpm": "pnpm run %s", "bun": "bun run %s"}[manager] % script
    if flags:
        cmd = "%s -- %s" % (run, flags) if manager == "npm" else "%s %s" % (run, flags)
        confidence, note = "high", "port passed as a flag"
    else:
        cmd, confidence = run, "low"
        note = "no port flag known for this project; only the PORT env var is set"
    return {"name": label, "family": "node", "manager": manager, "cmd": cmd, "env": {},
            "confidence": confidence, "note": note}


def python_text(path):
    return " ".join(read_text(os.path.join(path, name)).lower()
                    for name in ("requirements.txt", "pyproject.toml", "Pipfile"))


def detect_python(path):
    text = python_text(path)
    base = {"family": "python", "env": {}, "confidence": "high", "note": "port passed as a flag"}
    if exists(path, "manage.py"):
        return dict(base, name="django", cmd="{PY} manage.py runserver 127.0.0.1:{PORT}")
    if "flask" in text and any(exists(path, f) for f in ("app.py", "wsgi.py", "application.py")):
        return dict(base, name="flask", cmd="{PY} -m flask run --port {PORT}")
    if "uvicorn" in text or "fastapi" in text:
        for module_file, module in (("main.py", "main:app"), (os.path.join("app", "main.py"), "app.main:app")):
            if "app" in read_text(os.path.join(path, module_file)) and exists(path, module_file):
                return dict(base, name="fastapi", cmd="{PY} -m uvicorn %s --port {PORT}" % module)
    return None


def detect_rails(path):
    if exists(path, "Gemfile") and exists(path, "bin", "rails"):
        return {"name": "rails", "family": "ruby", "cmd": "bin/rails server -p {PORT}", "env": {},
                "confidence": "high", "note": "port passed as a flag"}
    return None


def detect_spring(path):
    note = "port passed through SERVER_PORT"
    if "spring-boot" in read_text(os.path.join(path, "pom.xml")):
        cmd = "./mvnw spring-boot:run" if exists(path, "mvnw") else "mvn spring-boot:run"
        return {"name": "spring-boot-maven", "family": "java", "cmd": cmd,
                "env": {"SERVER_PORT": "{PORT}"}, "confidence": "high", "note": note}
    for name in ("build.gradle", "build.gradle.kts"):
        if "org.springframework.boot" in read_text(os.path.join(path, name)):
            cmd = "./gradlew bootRun" if exists(path, "gradlew") else "gradle bootRun"
            return {"name": "spring-boot-gradle", "family": "java", "cmd": cmd,
                    "env": {"SERVER_PORT": "{PORT}"}, "confidence": "high", "note": note}
    return None


def detect_go(path):
    if exists(path, "go.mod") and exists(path, "main.go"):
        return {"name": "go", "family": "go", "cmd": "go run .", "env": {}, "confidence": "low",
                "note": "Go programs choose their own port; only the PORT env var is set"}
    return None


def detect_all(path):
    found = [fn(path) for fn in (detect_node, detect_python, detect_rails, detect_spring, detect_go)]
    return [item for item in found if item]


def detect_stack(path):
    """Best guess for how to run the project at `path`, or None. Lists other candidates too."""
    found = detect_all(path)
    if not found:
        return None
    best = dict(found[0])
    best["alternatives"] = [item["name"] for item in found[1:]]
    return best


def python_for(path):
    venv = os.path.join(path, ".venv", "bin", "python")
    if os.name == "nt":
        venv = os.path.join(path, ".venv", "Scripts", "python.exe")
    return venv if os.path.exists(venv) else ("python3" if os.name != "nt" else "python")


def render(text, port, hmr_port, path):
    return (text.replace("{PORT}", str(port)).replace("{HMR_PORT}", str(hmr_port))
            .replace("{PY}", python_for(path)))


# ----------------------------------------------------------- dependency setup

def file_hash(path):
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def plan_install(path, main, stack, fresh=False):
    """What to do about dependencies in `path`. Returns a dict, or None if nothing is needed.

    `fresh` pretends `path` is a brand-new worktree (used for dry runs and `detect`).

    Keys: cmd (shell command or None), clone_from (directory to copy, or None), note.
    """
    if not stack:
        return None
    family = stack.get("family")
    if family == "node":
        if exists(path, "node_modules") and not fresh:
            return None
        manager, lockfile = node_manager(path)
        if manager == "pnpm":
            cmd = "pnpm install --frozen-lockfile"
        elif manager == "yarn":
            cmd = "yarn install --immutable" if exists(path, ".yarnrc.yml") else "yarn install --frozen-lockfile"
        elif manager == "bun":
            cmd = "bun install --frozen-lockfile"
        else:
            cmd = "npm ci" if lockfile else "npm install"
        plan = {"cmd": cmd, "clone_from": None, "note": "install: " + cmd}
        source = os.path.join(main, "node_modules")
        if (manager != "pnpm" and lockfile and os.path.isdir(source) and os.path.abspath(main) != os.path.abspath(path)
                and file_hash(os.path.join(main, lockfile)) == file_hash(os.path.join(path, lockfile))):
            plan.update(clone_from=source, note="copy node_modules from main checkout (same %s)" % lockfile)
        return plan
    if family == "python":
        if exists(path, ".venv") and not fresh:
            return None
        uv = shutil.which("uv")
        if exists(path, "uv.lock") and uv:
            return {"cmd": "uv sync", "clone_from": None, "note": "install: uv sync"}
        if exists(path, "requirements.txt"):
            cmd = ("uv venv .venv && uv pip install -r requirements.txt" if uv else
                   "python3 -m venv .venv && .venv/bin/pip install -r requirements.txt")
            return {"cmd": cmd, "clone_from": None, "note": "install: " + cmd}
        return None
    if family == "ruby" and exists(path, "Gemfile.lock"):
        return {"cmd": "bundle install", "clone_from": None, "note": "install: bundle install"}
    return None


def clone_directory(source, dest):
    """Copy-on-write clone where the filesystem allows it. Returns True on success."""
    if sys.platform == "darwin":
        cmd = ["cp", "-cR", source, dest]
    elif sys.platform.startswith("linux"):
        cmd = ["cp", "-R", "--reflink=auto", source, dest]
    else:
        return False
    return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def run_install(plan, path, log_path, timeout):
    """Run a plan from plan_install. Returns (ok, note)."""
    if plan.get("clone_from"):
        dest = os.path.join(path, "node_modules")
        if clone_directory(plan["clone_from"], dest):
            return True, plan["note"]
        shutil.rmtree(dest, ignore_errors=True)  # partial copy; fall back to a real install
    if not plan.get("cmd"):
        return False, "no install command available"
    with open(log_path, "w") as log:
        try:
            proc = subprocess.run(plan["cmd"], shell=True, cwd=path, stdout=log,
                                  stderr=subprocess.STDOUT, timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, "install timed out after %ds: %s" % (timeout, plan["cmd"])
    if proc.returncode != 0:
        return False, "install failed (exit %d): %s" % (proc.returncode, plan["cmd"])
    return True, plan["note"]


# ----------------------------------------------------------------- env files

def git_ignored(repo, relpath):
    return subprocess.run(["git", "-C", repo, "check-ignore", "-q", relpath],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def copy_env_files(main, path, extra_patterns=()):
    """Copy git-ignored .env files (and .worktreeinclude / config patterns) into a new worktree.

    Never overwrites, and never copies a file git would track. Returns the relative names copied.
    """
    patterns = [name for name in sorted(os.listdir(main)) if name == ".env" or name.startswith(".env.")]
    for line in read_text(os.path.join(main, ".worktreeinclude")).splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    patterns.extend(extra_patterns)
    copied = []
    for pattern in patterns:
        for source in sorted(glob.glob(os.path.join(main, pattern), recursive=True)):
            if not os.path.isfile(source):
                continue
            rel = os.path.relpath(source, main)
            dest = os.path.join(path, rel)
            if rel.startswith("..") or os.path.exists(dest) or rel in copied:
                continue
            if not git_ignored(main, rel):
                continue
            os.makedirs(os.path.dirname(dest) or path, exist_ok=True)
            shutil.copy2(source, dest)
            copied.append(rel)
    return copied


# --------------------------------------------------------------- risk scanning

def candidate_files(path, limit=60):
    """Env and config style files near the repo root, where API URLs usually live."""
    found = []
    for root, dirs, files in os.walk(path):
        depth = os.path.relpath(root, path).count(os.sep) + (0 if root == path else 1)
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".") and depth < 3]
        in_config_dir = os.path.basename(root) in ("environments", "config", "configs", "env")
        for name in sorted(files):
            is_env = name == ".env" or name.startswith(".env.") or name.endswith(".env")
            is_config = bool(re.search(r"(\.config\.(js|ts|mjs|cjs|json)|^proxy\.conf.*)$", name))
            if is_env or is_config or in_config_dir:
                found.append(os.path.join(root, name))
        if len(found) >= limit:
            break
    return found[:limit]


def scan_risks(path, own_ports):
    """Look for URLs that will break or cross origins when the app runs on a new port.

    Returns {"cors_risk": bool, "findings": [...]}. A finding has file, line, url, kind and auth.
    Kinds: "other-local-port" (localhost on a port that is not ours) and "remote-origin".
    """
    findings = []
    for filename in candidate_files(path):
        for number, line in enumerate(read_text(filename).splitlines(), 1):
            for match in URL_RE.finditer(line):
                host, port = match.group(1).lower(), match.group(2)
                if host in ("localhost", "127.0.0.1", "0.0.0.0"):
                    if port is None or int(port) in own_ports:
                        continue
                    kind = "other-local-port"
                elif "." in host and not host.endswith(IGNORED_HOSTS):
                    kind = "remote-origin"
                else:
                    continue
                findings.append({"file": os.path.relpath(filename, path), "line": number,
                                 "url": match.group(0)[:100], "kind": kind,
                                 "auth": bool(AUTH_WORDS.search(line))})
    return {"cors_risk": bool(findings), "findings": findings[:12]}
