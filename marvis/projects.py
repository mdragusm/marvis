from pathlib import Path

from .config import config

_cache: list[tuple[str, Path]] | None = None


def get_projects() -> list[tuple[str, Path]]:
    global _cache
    if _cache is None:
        base = Path(config.projects_base_dir)
        results: list[tuple[str, Path]] = []
        try:
            for d1 in base.iterdir():
                if not d1.is_dir() or d1.name.startswith('.'):
                    continue
                results.append((d1.name, d1))
                try:
                    for d2 in d1.iterdir():
                        if d2.is_dir() and not d2.name.startswith('.'):
                            results.append((d2.name, d2))
                except PermissionError:
                    pass
        except (PermissionError, OSError):
            pass
        _cache = results
    return _cache


def match_project(spoken: str) -> list[Path]:
    """Returns candidate project paths for the spoken name.

    One result = unambiguous match. Multiple = ambiguous (caller should ask).
    Zero = nothing found.
    """
    spoken_lower = spoken.lower().strip()
    projects = get_projects()

    # Exact name match wins outright.
    exact = [p for name, p in projects if name.lower() == spoken_lower]
    if exact:
        return exact

    # Substring: spoken contained in name, or name contained in spoken.
    spoken_words = set(spoken_lower.split())
    contains = [
        p for name, p in projects
        if spoken_lower in name.lower()
        or name.lower() in spoken_lower
        or spoken_words.issubset(set(name.lower().replace('-', ' ').replace('_', ' ').split()))
    ]
    return contains
