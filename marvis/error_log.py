import logging
from logging.handlers import RotatingFileHandler

_MAX_BYTES = 2 * 1024 * 1024  # rotate once the file hits ~2 MB
_BACKUP_COUNT = 5  # keep this many rotated files around (marvis_error.log.1 .. .5)

logger = logging.getLogger("marvis")
logger.setLevel(logging.ERROR)
logger.propagate = False

_handler = RotatingFileHandler(
    "marvis_error.log", maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
)
_handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"))
logger.addHandler(_handler)

# Temporary: pins down exactly where the audible mid-reply gaps (see CHANGELOG "(9)"/"(10)")
# are actually coming from -- separate from `logger` above so it isn't gated behind
# ERROR level and doesn't mix diagnostic noise into the real error log. Remove once diagnosed.
debug_logger = logging.getLogger("marvis.speech_debug")
debug_logger.setLevel(logging.DEBUG)
debug_logger.propagate = False
_debug_handler = RotatingFileHandler(
    "marvis_speech_debug.log", maxBytes=_MAX_BYTES, backupCount=1, encoding="utf-8"
)
_debug_handler.setFormatter(logging.Formatter("[%(asctime)s.%(msecs)03d] %(message)s", datefmt="%H:%M:%S"))
debug_logger.addHandler(_debug_handler)
