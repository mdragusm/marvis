import os


def test_devnull_redirect_survives_characters_outside_cp1252():
    # Regression: main.py redirects sys.stdout/stderr to os.devnull for pythonw.exe,
    # which has no console. Opened without errors="replace", that stream defaults to
    # cp1252 on Windows and crashes the instant a print() hits a character cp1252
    # can't represent (e.g. an arrow in a Claude reply) -- silently killing whatever
    # turn was mid-reply. This pins the fix directly, independent of main.py's
    # module-level `if sys.stdout is None` guard (which only fires under pythonw,
    # not under pytest).
    with open(os.devnull, "w", encoding="cp1252", errors="replace") as devnull:
        print("next steps → done", file=devnull)
