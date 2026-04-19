import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

SERVICE_NAME = "devlocare-backend"
LOGS_DIR = Path("logs")
LOGS_DIR.mkdir(exist_ok=True)

LOG_FORMAT = (
    "%(asctime)s | %(levelname)-8s | service=%(service)s | "
    "module=%(name)s | %(message)s"
)
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class _ServiceFilter(logging.Filter):
    """Injects service name into every log record."""
    def filter(self, record: logging.LogRecord) -> bool:
        record.service = SERVICE_NAME
        return True


def _build_formatter() -> logging.Formatter:
    return logging.Formatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT)


def _build_console_handler() -> logging.StreamHandler:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_build_formatter())
    handler.setLevel(logging.DEBUG)
    return handler


def _build_file_handler(filename: str, level: int) -> RotatingFileHandler:
    """
    Rotating file handler — each file caps at 5 MB, keeps last 3 files.
    """
    handler = RotatingFileHandler(
        LOGS_DIR / filename,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(_build_formatter())
    handler.setLevel(level)
    return handler


def get_logger(name: str) -> logging.Logger:
    """
    Returns a configured logger for the given module.
    Usage in every module:
        from app.core.logging import get_logger
        logger = get_logger(__name__)

    Log files produced:
        logs/app.log      — INFO and above (normal flow)
        logs/error.log    — ERROR and above (exceptions, crashes)
    """
    logger = logging.getLogger(name)

    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        logger.propagate = False

        service_filter = _ServiceFilter()

        console = _build_console_handler()
        console.addFilter(service_filter)

        app_file = _build_file_handler("app.log", logging.INFO)
        app_file.addFilter(service_filter)

        error_file = _build_file_handler("error.log", logging.ERROR)
        error_file.addFilter(service_filter)

        logger.addHandler(console)
        logger.addHandler(app_file)
        logger.addHandler(error_file)

    return logger
