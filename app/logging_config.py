import copy
import logging
import re
import sys
from app.config import settings


_SENSITIVE_SETTINGS = (
    "TPO_USERNAME", "TPO_PASSWORD", "SMTP_USERNAME", "SMTP_PASSWORD",
    "TELEGRAM_BOT_TOKEN", "SECRET_KEY",
)
_URL = re.compile(r"(?:https?|wss?)://[^\s<>\"']+", re.IGNORECASE)
_EMAIL = re.compile(r"[^\s<>\"'@=]+@[^\s<>\"'@,;]+")
_PRIVATE_HEADER = re.compile(
    r"\b(?:authorization|cookie|set-cookie)\s*[:=]\s*[^\r\n]*", re.IGNORECASE)
_PRIVATE_FIELD = re.compile(
    r"[\"']?\b(?:password|secret(?:_key)?|(?:access[_-]?|refresh[_-]?)?token)"
    r"[\"']?\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;}\]]+)", re.IGNORECASE)


class PrivacyFormatter(logging.Formatter):
    """Defense at the shared console boundary, in addition to safe call sites.

    Pattern redaction cannot identify arbitrary upstream prose. Call sites must
    still use fixed categories instead of bodies, identities or exception text.
    Exception/stack details are deliberately withheld at this boundary.
    """

    def format(self, record):
        safe = copy.copy(record)
        if isinstance(safe.msg, BaseException):
            safe.msg, safe.args = "exception_details_suppressed", ()
        # Do not invoke an exception's potentially sensitive __str__ through %s.
        if isinstance(safe.args, dict):
            safe.args = {k: "exception_details_suppressed" if isinstance(v, BaseException) else v
                         for k, v in safe.args.items()}
        elif isinstance(safe.args, tuple):
            safe.args = tuple("exception_details_suppressed" if isinstance(v, BaseException) else v
                              for v in safe.args)
        try:
            message = safe.getMessage()
        except Exception:
            message = "log_message_unavailable"
        for name in _SENSITIVE_SETTINGS:
            value = getattr(settings, name, None)
            if isinstance(value, str) and value:
                message = message.replace(value, "[redacted]")
        for pattern in (_URL, _EMAIL, _PRIVATE_HEADER, _PRIVATE_FIELD):
            message = pattern.sub("[redacted]", message)
        safe.msg, safe.args = message, ()
        # A previous handler may already have cached unredacted exception text.
        safe.exc_text = None
        safe.stack_info = None
        return super().format(safe)

    def formatException(self, exc_info):
        return "exception_details_suppressed"


def setup_logging():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    logger = logging.getLogger()
    logger.setLevel(settings.LOG_LEVEL)

    # Console handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(settings.LOG_LEVEL)

    # Formatter: [2026-09-09 18:35:00 IST] INFO: Message
    formatter = PrivacyFormatter(
        "[%(asctime)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S %Z"
    )
    handler.setFormatter(formatter)

    if logger.hasHandlers():
        logger.handlers.clear()
        
    logger.addHandler(handler)

    # Uvicorn must share this sink, including errors raised by ASGI handlers.
    # get_uvicorn_config() keeps Uvicorn from replacing it afterward.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        service_logger = logging.getLogger(name)
        service_logger.handlers.clear()
        service_logger.propagate = True

    # Mute chatty loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("playwright").setLevel(logging.WARNING)
