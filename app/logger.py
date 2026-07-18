from __future__ import annotations

import logging
from pathlib import Path

_CONFIGURED = False


def setup_logging(log_path: Path) -> logging.Logger:
    """Настраивает логирование в файл и консоль. Идемпотентна."""
    global _CONFIGURED
    logger = logging.getLogger("publisher")
    if _CONFIGURED:
        return logger

    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    _CONFIGURED = True
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger("publisher")
