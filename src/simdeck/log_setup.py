"""Colored terminal logging for SimDeck."""

import logging

SUCCESS = 25
logging.addLevelName(SUCCESS, "SUCCESS")


def _success(self, message, *args, **kwargs):
    if self.isEnabledFor(SUCCESS):
        self._log(SUCCESS, message, args, **kwargs)


logging.Logger.success = _success

_RESET = "\033[0m"
_COLORS = {
    logging.DEBUG:    "\033[90m",    # dark grey
    logging.INFO:     "\033[97m",    # bright white
    SUCCESS:          "\033[92m",    # bright green
    logging.WARNING:  "\033[93m",    # bright yellow
    logging.ERROR:    "\033[91m",    # bright red
    logging.CRITICAL: "\033[91;1m",  # bold bright red
}


class _ColoredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        color = _COLORS.get(record.levelno, "")
        msg = super().format(record)
        return f"{color}{msg}{_RESET}" if color else msg


def setup(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(_ColoredFormatter(
        fmt="%(asctime)s [%(levelname)-7s] %(name)s - %(message)s",
        datefmt="%H:%M:%S",
    ))
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)
