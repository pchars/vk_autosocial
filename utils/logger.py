import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict
from enum import Enum


class LogLevel(str, Enum):
    """Supported log levels"""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


# Mapping of the string values into logging const
LOG_LEVEL_MAPPING: Dict[str, int] = {
    LogLevel.DEBUG: logging.DEBUG,
    LogLevel.INFO: logging.INFO,
    LogLevel.WARNING: logging.WARNING,
    LogLevel.ERROR: logging.ERROR,
    LogLevel.CRITICAL: logging.CRITICAL
}

# Fallback for the cases when general logging was not set up yet
_fallback_logger = None

def _get_fallback_logger() -> logging.Logger:
    """Creating temporary logger before init of the main one"""
    global _fallback_logger
    if _fallback_logger is None:
        _fallback_logger = logging.getLogger('fallback')
        if not _fallback_logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            formatter = logging.Formatter('%(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            _fallback_logger.addHandler(handler)
            _fallback_logger.setLevel(logging.INFO)
    return _fallback_logger

def get_log_level_from_string(level_str: str) -> int:
    """
    Convert string log_level into logging const

    Args:
        level_str: string level of the logging from .cfg (debug, info, warning, error, critical)

    Returns:
        Const of the logging

    Raises:
        ValueError: If you used wrong log_level in the .cfg
    """
    level_str = level_str.lower().strip()

    # Switch with mapping
    if level_str in LOG_LEVEL_MAPPING:
        return LOG_LEVEL_MAPPING[level_str]

    # Different cases
    switch_case = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warn": logging.WARNING,
        "warning": logging.WARNING,
        "error": logging.ERROR,
        "critical": logging.CRITICAL,
        "fatal": logging.CRITICAL,
    }

    if level_str in switch_case:
        return switch_case[level_str]

    # If log_level is wrong then using INFO by default
    raise ValueError(f"Unknown log level: {level_str}. Supported levels: {list(LOG_LEVEL_MAPPING.keys())}")


def setup_logging(log_level: str = "info", log_file: Optional[Path] = None, console_output: bool = True) -> logging.Logger:
    """
    Logging configuration

    Args:
        log_level: Level of logging (debug, info, warning, error, critical)
        log_file: Path to the log file (optional)
        console_output: Show output into the consol (bool)

    Returns:
        Configured logger
    """
    fallback_logger = _get_fallback_logger()

    try:
        level = get_log_level_from_string(log_level)
    except ValueError as e:
        fallback_logger.warning(f"{e}. Using INFO level as default.")
        level = logging.INFO

    # Formatter creation
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    logger = logging.getLogger()
    logger.setLevel(level)

    logger.handlers.clear()

    if console_output:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        console_handler.setLevel(level)
        logger.addHandler(console_handler)

    if log_file:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = log_file.parent / f"{log_file.stem}_{timestamp}{log_file.suffix}"
        log_file.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)
        logger.addHandler(file_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    """
    Getter of the named logger

    Args:
        name: Logger name ( __name__)

    Returns:
        Ready to use logger
    """
    return logging.getLogger(name)