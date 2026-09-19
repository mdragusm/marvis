import os
import sys
import traceback

# Under pythonw.exe there is no console, so sys.stdout/stderr are None and any bare
# print() would crash. Route them to the void; real diagnostics go to marvis_error.log
# via error_log.py instead. errors="replace" matters: the default cp1252 encoding here
# can't represent a lot of Unicode (arrows, em-dashes, emoji) in Claude's replies, and
# without it a single such character crashes the print() call.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", errors="replace")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", errors="replace")

from marvis import indicator
from marvis.error_log import logger as error_logger


def _log_crash(error_text: str) -> None:
    print(error_text, flush=True)
    error_logger.error("fatal crash:\n%s", error_text)


def _run_assistant() -> None:
    # Runs on a background thread; the main thread is busy driving the indicator
    # window's event loop, so a crash here has to be logged and torn down explicitly
    # rather than relying on it propagating out of __main__.
    try:
        from marvis.assistant import run
        from marvis.single_instance import kill_other_instances

        kill_other_instances()
        run()
    except BaseException:
        _log_crash(traceback.format_exc())
        os._exit(1)


if __name__ == "__main__":
    try:
        indicator.run(_run_assistant)
    except BaseException:
        _log_crash(traceback.format_exc())
        sys.exit(1)
