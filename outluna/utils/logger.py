"""日志配置。"""

import logging
import sys
from pathlib import Path

try:
    from loguru import logger as _loguru_logger
    LOGURU_AVAILABLE = True
except ImportError:
    LOGURU_AVAILABLE = False

from outluna.config import settings


def setup_logging(log_dir: Path | None = None):
    """配置结构化日志。"""
    log_dir = log_dir or settings.project_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    if LOGURU_AVAILABLE:
        _loguru_logger.remove()
        _loguru_logger.add(
            sys.stdout,
            level="INFO",
            format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level:<8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        )
        _loguru_logger.add(
            log_dir / "outluna_{time:YYYY-MM-DD}.log",
            rotation="00:00",
            retention="30 days",
            encoding="utf-8",
            level="DEBUG",
        )
        return _loguru_logger
    else:
        # 回退到标准库 logging
        logger = logging.getLogger("outluna")
        logger.setLevel(logging.DEBUG)

        if not logger.handlers:
            formatter = logging.Formatter(
                "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d - %(message)s"
            )

            console = logging.StreamHandler(sys.stdout)
            console.setLevel(logging.INFO)
            console.setFormatter(formatter)
            logger.addHandler(console)

            file_handler = logging.FileHandler(
                log_dir / "outluna_standard.log",
                encoding="utf-8",
            )
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)

        return logger
