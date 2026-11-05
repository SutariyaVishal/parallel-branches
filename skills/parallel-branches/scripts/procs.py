"""Process, signal and lock helpers that differ between POSIX and Windows.

Used by pb.py and devbrowser.py. Python 3.9+, standard library only (ctypes on Windows).

A "group id" is the leader's PID on both systems. On POSIX the leader starts a new session, so
the whole tree shares that process group. Windows has no process groups in that sense: the tree
is found from the leader with `taskkill /T`, which works while the leader is alive. Servers are
started through a shell, which stays alive while its child runs.
"""

import contextlib
import os
import shutil
import signal
import subprocess
import time

IS_WINDOWS = os.name == "nt"

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    _QUERY_LIMITED = 0x1000
    _STILL_ACTIVE = 259
    _ERROR_ACCESS_DENIED = 5
else:
    import fcntl


# -------------------------------------------------------------------- starting

def spawn(cmd, cwd, env, log):
    """Start a shell command detached, with output going to the open file `log`."""
    kwargs = dict(shell=True, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    if IS_WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)


def spawn_program(args):
    """Start a program (no shell) detached, with output discarded."""
    kwargs = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if IS_WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(args, **kwargs)


# ------------------------------------------------------------------- inspecting

def _win_open(pid):
    return _k32.OpenProcess(_QUERY_LIMITED, False, pid)


def process_token(pid):
    """A start-time string that changes if the PID is reused by another process."""
    if IS_WINDOWS:
        handle = _win_open(pid)
        if not handle:
            return ""
        try:
            created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
            if not _k32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                        ctypes.byref(kernel), ctypes.byref(user)):
                return ""
            return str((created.dwHighDateTime << 32) | created.dwLowDateTime)
        finally:
            _k32.CloseHandle(handle)
    proc = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, universal_newlines=True)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def group_alive(pgid):
    """True while the leader (Windows) or any member of the group (POSIX) is running."""
    if IS_WINDOWS:
        handle = _win_open(pgid)
        if not handle:
            return ctypes.get_last_error() == _ERROR_ACCESS_DENIED
        try:
            code = wintypes.DWORD()
            return bool(_k32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == _STILL_ACTIVE
        finally:
            _k32.CloseHandle(handle)
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def listener_pgids(port):
    """Process-group ids listening on `port`, or None if that cannot be determined here.

    None on Windows (owner verification is skipped there) and where `lsof` is missing.
    """
    if IS_WINDOWS or not shutil.which("lsof"):
        return None
    proc = subprocess.run(["lsof", "-nP", "-t", "-iTCP:%d" % port, "-sTCP:LISTEN"],
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True)
    pgids = set()
    for pid in proc.stdout.split():
        ps = subprocess.run(["ps", "-o", "pgid=", "-p", pid], stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, universal_newlines=True)
        if ps.stdout.strip().isdigit():
            pgids.add(int(ps.stdout.strip()))
    return pgids


# --------------------------------------------------------------------- stopping

def reap(pid):
    """Collect a finished child process. A zombie leader makes its group look alive, and on macOS
    a group of only zombies refuses signals with EPERM. Nothing to do on Windows."""
    if IS_WINDOWS:
        return
    try:
        os.waitpid(pid, os.WNOHANG)
    except OSError:  # not our child (ChildProcessError is an OSError)
        pass


def stop_group(pgid, grace=8.0):
    """Stop the process group (POSIX) or process tree (Windows). True once it is gone."""
    if IS_WINDOWS:
        # There is no polite signal for console programs, so this is a forced kill of the whole tree.
        subprocess.run(["taskkill", "/PID", str(pgid), "/T", "/F"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        deadline = time.time() + grace
        while time.time() < deadline:
            if not group_alive(pgid):
                return True
            time.sleep(0.1)
        return not group_alive(pgid)
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 3.0)):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return True
        except PermissionError:
            reap(pgid)
            return not group_alive(pgid)
        deadline = time.time() + wait
        while time.time() < deadline:
            reap(pgid)
            if not group_alive(pgid):
                return True
            time.sleep(0.1)
    return not group_alive(pgid)


def remove_tree(path, tries=25, delay=0.2):
    """Delete a directory tree, retrying while another process still holds files open.

    Windows keeps a dead browser's files locked for a moment. Returns True once the path is gone.
    """
    for _ in range(tries):
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path):
            return True
        time.sleep(delay)
    return not os.path.exists(path)


# ---------------------------------------------------------------------- locking

@contextlib.contextmanager
def file_lock(path, timeout=60.0):
    """An exclusive lock held for the duration of the block, shared between processes."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handle = open(path, "a+")
    locked = False
    try:
        if IS_WINDOWS:
            import msvcrt
            deadline = time.time() + timeout
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    locked = True
                    break
                except OSError:
                    if time.time() > deadline:
                        raise
                    time.sleep(0.1)
        else:
            fcntl.flock(handle, fcntl.LOCK_EX)
            locked = True
        yield
    finally:
        if locked and IS_WINDOWS:
            import msvcrt
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        handle.close()
