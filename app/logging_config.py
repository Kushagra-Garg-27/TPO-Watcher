import logging
import sys
from app.config import settings

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
    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S %Z"
    )
    handler.setFormatter(formatter)

    if logger.hasHandlers():
        logger.handlers.clear()
        
    logger.addHandler(handler)

    # Mute chatty loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("playwright").setLevel(logging.WARNING)
