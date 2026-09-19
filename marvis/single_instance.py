import os
import sys

import psutil

_MAIN_PY = os.path.abspath(sys.argv[0])


def kill_other_instances() -> None:
    """Kill any other running process whose command line targets this same main.py."""
    current_pid = os.getpid()

    # Exclude ancestors too: on Windows, venv's python.exe is a launcher stub
    # that spawns the real interpreter as a child with the same cmdline, so
    # our own parent process would otherwise look like a "duplicate" of us.
    excluded_pids = {current_pid}
    try:
        proc_self = psutil.Process(current_pid)
        excluded_pids.update(p.pid for p in proc_self.parents())
    except psutil.NoSuchProcess:
        pass

    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        if proc.info["pid"] in excluded_pids:
            continue
        if "python" not in (proc.info["name"] or "").lower():
            continue
        cmdline = proc.info["cmdline"] or []
        if any(os.path.abspath(arg) == _MAIN_PY for arg in cmdline if arg):
            try:
                proc.kill()
                proc.wait(timeout=5)
                print(f"[marvis] killed duplicate instance (pid {proc.pid})")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            except psutil.TimeoutExpired:
                print(f"[marvis] duplicate instance (pid {proc.pid}) did not exit in time")
