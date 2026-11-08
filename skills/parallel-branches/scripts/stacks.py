"""Stack detection, dependency install planning, env-file copying and risk scanning.

Used by pb.py. Python 3.9+, standard library only. Nothing here starts a server.
"""

import glob
import hashlib
import json
import os
import re
import shlex
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


# ------------------------------------------------------- monorepo detection

BACKEND_WORDS = ("api", "backend", "server", "service", "worker", "gateway", "db")
FRONTEND_WORDS = ("web", "frontend", "client", "ui", "app", "site", "portal", "dashboard")
LAYOUT_DIRS = ("apps", "services", "packages")
MAX_SERVICES = 8


def workspace_patterns(path):
    """Workspace globs declared at the repo root: npm/yarn `workspaces`, pnpm-workspace.yaml, lerna.json."""
    patterns = []
    workspaces = read_json(os.path.join(path, "package.json")).get("workspaces")
    if isinstance(workspaces, dict):
        workspaces = workspaces.get("packages")
    if isinstance(workspaces, list):
        patterns += [p for p in workspaces if isinstance(p, str)]
    lerna = read_json(os.path.join(path, "lerna.json")).get("packages")
    if isinstance(lerna, list):
        patterns += [p for p in lerna if isinstance(p, str)]
    inside = False  # pnpm-workspace.yaml: a `packages:` key followed by `- glob` lines
    for line in read_text(os.path.join(path, "pnpm-workspace.yaml")).splitlines():
        if re.match(r"^packages\s*:", line):
            inside = True
        elif inside and re.match(r"^\S", line):
            inside = False
        elif inside:
            match = re.match(r"^\s*-\s*['\"]?([^'\"#]+?)['\"]?\s*(#.*)?$", line)
            if match:
                patterns.append(match.group(1))
    return patterns


def workspace_dirs(path):
    included, excluded = [], set()
    for pattern in workspace_patterns(path):
        negate = pattern.startswith("!")
        for found in glob.glob(os.path.join(path, pattern.lstrip("!")), recursive=True):
            if os.path.isdir(found) and "node_modules" not in found.split(os.sep):
                (excluded.add if negate else included.append)(os.path.normpath(found))
    return sorted(set(d for d in included if d not in excluded))


def layout_dirs(path):
    """Sub-directories that may hold an app: top-level folders, plus the children of apps/ and services/."""
    dirs = []
    for name in sorted(os.listdir(path)):
        full = os.path.join(path, name)
        if not os.path.isdir(full) or name.startswith(".") or name in SKIP_DIRS:
            continue
        if name in LAYOUT_DIRS:
            dirs += [os.path.join(full, sub) for sub in sorted(os.listdir(full))
                     if os.path.isdir(os.path.join(full, sub)) and sub not in SKIP_DIRS and not sub.startswith(".")]
        else:
            dirs.append(full)
    return dirs


def service_name(directory, taken):
    base = re.sub(r"[^a-z0-9-]+", "-", os.path.basename(directory).lower()).strip("-") or "app"
    base = base[:28]
    name, number = base, 2
    while name in taken:
        name, number = "%s-%d" % (base, number), number + 1
    return name


def role(name):
    if any(word in name for word in BACKEND_WORDS):
        return 0
    if any(word in name for word in FRONTEND_WORDS):
        return 2
    return 1


def detect_services(path):
    """Suggest a `services` config for a monorepo. Returns None, or {"services": [...], "notes": [...]}.

    This only reads the repository. Nothing is started or written.
    """
    workspaces = workspace_dirs(path)
    root_stack = detect_stack(path)
    candidates = []
    for directory in (workspaces or layout_dirs(path)):
        if os.path.realpath(directory) == os.path.realpath(path):
            continue
        stack = detect_stack(directory)
        if stack:
            candidates.append((directory, stack))
    if not candidates or (root_stack and not workspaces and len(candidates) < 2):
        return None
    notes = []
    if root_stack and workspaces:
        notes.append("the root %s script (%s) may already start every app; check it before using services"
                     % (root_stack["name"], root_stack["cmd"]))
    if len(candidates) > MAX_SERVICES:
        notes.append("%d apps found, only the first %d are listed" % (len(candidates), MAX_SERVICES))
        candidates = candidates[:MAX_SERVICES]
    taken, services = set(), []
    for directory, stack in candidates:
        name = service_name(directory, taken)
        taken.add(name)
        services.append({"name": name, "cwd": os.path.relpath(directory, path).replace(os.sep, "/"),
                         "cmd": stack["cmd"], "_stack": stack["name"], "_confidence": stack["confidence"]})
        if stack["confidence"] == "low":
            notes.append("%s: %s" % (name, stack["note"]))
    services.sort(key=lambda svc: role(svc["name"]))  # backends first, front ends last (primary)
    notes.append("depends_on and {url.NAME} wiring between the apps are not guessed; "
                 "add them where one app calls another")
    return {"services": services, "notes": notes}


def suggested_config(suggestion):
    """The `.parallel-branches.json` content for a detect_services result (without the _hint keys)."""
    return {"services": [{k: v for k, v in svc.items() if not k.startswith("_")}
                         for svc in suggestion["services"]]}


def shell_quote(text):
    """Quote `text` for the shell that subprocess uses: cmd.exe on Windows, sh elsewhere."""
    if os.name == "nt":
        return '"%s"' % text if (" " in text or "&" in text) else text
    return shlex.quote(text)


def python_for(path):
    """The interpreter command for a project: its .venv if it has one, otherwise the system Python."""
    venv = os.path.join(path, ".venv", "Scripts", "python.exe") if os.name == "nt" \
        else os.path.join(path, ".venv", "bin", "python")
    return shell_quote(venv) if os.path.exists(venv) else ("python3" if os.name != "nt" else "python")


def render(text, port, hmr_port, path, ports=None):
    """Fill in {PORT}, {HMR_PORT}, {PY} and, for multi-service setups, {ports.NAME} and {url.NAME}."""
    text = (text.replace("{PORT}", str(port)).replace("{HMR_PORT}", str(hmr_port))
            .replace("{PY}", python_for(path)))
    if ports:
        def other(match):
            number = ports[match.group(2)][0]
            return str(number) if match.group(1) == "ports" else "http://localhost:%d" % number
        text = REF_RE.sub(other, text)
    return text


# ------------------------------------------------------------------ services

SERVICE_KEYS = {"name", "cwd", "cmd", "env", "install", "health_path", "health", "depends_on", "primary"}
SERVICE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
REF_RE = re.compile(r"\{(ports|url)\.([a-z0-9-]+)\}")


def parse_services(cfg):
    """Validate `services` in a config. Returns None, or (services in start order, primary name).

    Raises ValueError with a message the user can act on.
    """
    raw = cfg.get("services")
    if raw is None:
        return None
    if not isinstance(raw, list) or not raw:
        raise ValueError("services must be a non-empty list")
    names = set()
    for svc in raw:
        if not isinstance(svc, dict):
            raise ValueError("every service must be an object")
        unknown = sorted(set(svc) - SERVICE_KEYS)
        if unknown:
            raise ValueError("unknown key(s) %s in service %r" % (unknown, svc.get("name")))
        name = svc.get("name")
        if not isinstance(name, str) or not SERVICE_NAME_RE.match(name):
            raise ValueError("service name %r must be lowercase letters, digits and hyphens" % (name,))
        if name in names:
            raise ValueError("duplicate service name %r" % name)
        names.add(name)
        cwd = svc.get("cwd", ".")
        if not isinstance(cwd, str) or os.path.isabs(cwd) or os.path.normpath(cwd).split(os.sep)[0] == "..":
            raise ValueError("cwd of service %r must be a relative path inside the repository" % name)
        if svc.get("health") not in (None, "http", "tcp"):
            raise ValueError("health of service %r must be \"http\" or \"tcp\"" % name)
        deps = svc.get("depends_on", [])
        if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
            raise ValueError("depends_on of service %r must be a list of service names" % name)
    by_name = {svc["name"]: svc for svc in raw}
    for svc in raw:
        for dep in svc.get("depends_on", []):
            if dep not in by_name:
                raise ValueError("service %r depends on unknown service %r" % (svc["name"], dep))
        texts = [str(svc.get("cmd", ""))] + [str(v) for v in (svc.get("env") or {}).values()]
        for text in texts:
            for match in REF_RE.finditer(text):
                if match.group(2) not in by_name:
                    raise ValueError("service %r refers to unknown service %r" % (svc["name"], match.group(2)))
    order, done, visiting = [], set(), set()

    def visit(name):
        if name in done:
            return
        if name in visiting:
            raise ValueError("depends_on forms a cycle through %r" % name)
        visiting.add(name)
        for dep in by_name[name].get("depends_on", []):
            visit(dep)
        visiting.discard(name)
        done.add(name)
        order.append(by_name[name])

    for svc in raw:
        visit(svc["name"])
    primaries = [svc["name"] for svc in raw if svc.get("primary")]
    if len(primaries) > 1:
        raise ValueError("only one service can be primary, found %s" % primaries)
    return order, (primaries[0] if primaries else raw[-1]["name"])


def cross_service_refs(services):
    """(service, referenced service) pairs: a service that talks to another one on a different port."""
    pairs = []
    for svc in services:
        texts = [str(svc.get("cmd", ""))] + [str(v) for v in (svc.get("env") or {}).values()]
        for text in texts:
            for match in REF_RE.finditer(text):
                if match.group(2) != svc["name"] and (svc["name"], match.group(2)) not in pairs:
                    pairs.append((svc["name"], match.group(2)))
    return pairs


# ----------------------------------------------------------- dependency setup

def digest_bytes(data):
    """sha256 of text-file content with line endings normalized.

    Lockfiles are compared only to ask "same dependencies?". Git for Windows commits them with LF
    but checks them out with CRLF, so raw bytes of the same lockfile can differ between the commit
    and the working tree.
    """
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def file_hash(path):
    try:
        with open(path, "rb") as fh:
            return digest_bytes(fh.read())
    except OSError:
        return None


def plan_install(path, main, stack, fresh=False, branch_lock_hash=None):
    """What to do about dependencies in `path`. Returns a dict, or None if nothing is needed.

    `fresh` pretends `path` is a brand-new worktree (used for dry runs and `detect`).
    `branch_lock_hash` is the lockfile hash on the branch about to be checked out; with it a dry run
    can say whether `node_modules` would be cloned from the main checkout or installed.

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
        target_hash = branch_lock_hash if branch_lock_hash is not None else (
            file_hash(os.path.join(path, lockfile)) if lockfile else None)
        different_dir = branch_lock_hash is not None or os.path.abspath(main) != os.path.abspath(path)
        if (manager != "pnpm" and lockfile and os.path.isdir(source) and different_dir
                and target_hash is not None and file_hash(os.path.join(main, lockfile)) == target_hash):
            plan.update(clone_from=source, note="copy node_modules from main checkout (same %s)" % lockfile)
        return plan
    if family == "python":
        if exists(path, ".venv") and not fresh:
            return None
        uv = shutil.which("uv")
        if exists(path, "uv.lock") and uv:
            return {"cmd": "uv sync", "clone_from": None, "note": "install: uv sync"}
        if exists(path, "requirements.txt"):
            if uv:
                cmd = "uv venv .venv && uv pip install -r requirements.txt"
            elif os.name == "nt":
                cmd = "python -m venv .venv && .venv\\Scripts\\python.exe -m pip install -r requirements.txt"
            else:
                cmd = "python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt"
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
        plan = dict(plan, note="clone not possible here, installed instead: " + str(plan.get("cmd")))
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


def copy_plan(main, path, extra_patterns=()):
    """What `copy_env_files` would copy into `path`: relative names, directories with a trailing separator.

    Patterns are the `.env` files, `.worktreeinclude` lines and `extra_patterns`; a trailing slash is allowed.
    Only git-ignored files and git-ignored directories match, and anything already in `path` is left alone.
    """
    patterns = [name for name in sorted(os.listdir(main)) if name == ".env" or name.startswith(".env.")]
    for line in read_text(os.path.join(main, ".worktreeinclude")).splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    patterns.extend(extra_patterns)
    planned = []
    for pattern in patterns:
        for source in sorted(glob.glob(os.path.join(main, pattern.rstrip("/\\")), recursive=True)):
            rel = os.path.relpath(source, main)
            is_dir = os.path.isdir(source) and not os.path.islink(source)
            if rel.startswith("..") or not (is_dir or os.path.isfile(source)):
                continue
            name = rel + os.sep if is_dir else rel
            if os.path.exists(os.path.join(path, rel)) or name in planned:
                continue
            if any(name.startswith(done) for done in planned if done.endswith(os.sep)):
                continue  # inside a directory that is already being copied
            if git_ignored(main, rel):
                planned.append(name)
    return planned


def copy_env_files(main, path, extra_patterns=()):
    """Copy git-ignored files and directories (the `.env` files, `.worktreeinclude`, config patterns) into
    a new worktree. Directories are cloned copy-on-write where the filesystem allows it.

    Never overwrites, and never copies what git would track. Returns the names copied (see `copy_plan`).
    """
    copied = []
    for name in copy_plan(main, path, extra_patterns):
        rel = name.rstrip(os.sep)
        source, dest = os.path.join(main, rel), os.path.join(path, rel)
        os.makedirs(os.path.dirname(dest) or path, exist_ok=True)
        if name.endswith(os.sep):
            if not clone_directory(source, dest):
                shutil.rmtree(dest, ignore_errors=True)  # partial clone; copy the slow way
                shutil.copytree(source, dest, symlinks=True)
        else:
            shutil.copy2(source, dest)
        copied.append(name)
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
    findings, seen = [], set()
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
                key = (os.path.relpath(filename, path), number, match.group(0))
                if key in seen:
                    continue
                seen.add(key)
                findings.append({"file": key[0], "line": number, "url": match.group(0)[:100], "kind": kind,
                                 "auth": bool(AUTH_WORDS.search(line))})
    # What a dev-mode browser cannot fix comes first, then local ports, then remote hosts.
    findings.sort(key=lambda f: (not f["auth"], f["kind"] != "other-local-port"))
    return {"cors_risk": bool(findings), "findings": findings[:40]}
