"""Launch a separate, throwaway Chrome profile with web security off (CORS bypass).

Used by pb.py. Python 3.9+, standard library only.

Safety model: the browser always uses its own empty --user-data-dir, never the
user's real profile, and is opened on a single localhost URL.
"""

import http.client
import os
import shutil
import subprocess
import sys
import time

MAC_APPS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
]
LINUX_NAMES = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome",
               "microsoft-edge"]
WINDOWS_PATHS = [
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
]
CDP_BASE = 9222


def find_chrome(explicit=None):
    """Path to a Chromium-based browser, or None. Order: explicit, PB_CHROME, known locations."""
    for candidate in (explicit, os.environ.get("PB_CHROME")):
        if candidate:
            return candidate if os.path.exists(candidate) else None
    if sys.platform == "darwin":
        return next((p for p in MAC_APPS if os.path.exists(p)), None)
    if os.name == "nt":
        expanded = (os.path.expandvars(p) for p in WINDOWS_PATHS)
        return next((p for p in expanded if os.path.exists(p)), None)
    return next((shutil.which(n) for n in LINUX_NAMES if shutil.which(n)), None)


def launch(chrome, profile_dir, cdp_port, url, headless=False):
    """Start the browser detached. Returns the Popen object."""
    os.makedirs(profile_dir, exist_ok=True)
    args = [
        chrome, "--user-data-dir=" + profile_dir, "--remote-debugging-port=%d" % cdp_port,
        "--disable-web-security", "--disable-site-isolation-trials",
        "--no-first-run", "--no-default-browser-check",
    ]
    if headless:
        args.append("--headless=new")
    args.append(url)
    return subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, start_new_session=True)


def wait_for_cdp(cdp_port, timeout=15.0):
    """True once the DevTools endpoint answers."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            conn = http.client.HTTPConnection("127.0.0.1", cdp_port, timeout=1)
            conn.request("GET", "/json/version")
            ok = conn.getresponse().status == 200
            conn.close()
            if ok:
                return True
        except (OSError, http.client.HTTPException):
            pass
        time.sleep(0.25)
    return False
