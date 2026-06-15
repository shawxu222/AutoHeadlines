from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")
APP_HOME = Path(os.getenv("AUTOHEADLINES_HOME", PROJECT_ROOT)).expanduser()
LOG_DIR = APP_HOME / "logs"
LOG_FILE = LOG_DIR / "app.log"


def setup_logger(name: str = "AutoHeadlines") -> logging.Logger:
    """Configure a shared file and console logger."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(level)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


def get_logger(name: str) -> logging.Logger:
    return setup_logger(name)
