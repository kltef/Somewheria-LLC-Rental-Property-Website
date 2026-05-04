import logging
import sys


LOGGER_NAME = "somewheria.console"


class ConsoleFormatter(logging.Formatter):
    def format(self, record):
        timestamp = self.formatTime(record, "%H:%M:%S")
        component = getattr(record, "component", "app")
        return f"[{timestamp}] {record.levelname:<7} {component}: {record.getMessage()}"


class FileFormatter(logging.Formatter):
    def format(self, record):
        component = getattr(record, "component", "app")
        timestamp = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        return f"{timestamp}|{record.levelname}|{component}|{record.getMessage()}"


def setup_console_logger(level: str = "INFO", log_file=None) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False

    if not any(isinstance(handler, logging.StreamHandler) for handler in logger.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(ConsoleFormatter())
        logger.addHandler(handler)

    if log_file is not None:
        resolved = str(log_file)
        has_file_handler = any(
            isinstance(handler, logging.FileHandler) and getattr(handler, "baseFilename", "") == resolved
            for handler in logger.handlers
        )
        if not has_file_handler:
            file_handler = logging.FileHandler(resolved, encoding="utf-8")
            file_handler.setFormatter(FileFormatter())
            logger.addHandler(file_handler)

    return logger


def set_console_log_level(level: str) -> None:
    logging.getLogger(LOGGER_NAME).setLevel(getattr(logging, level.upper(), logging.INFO))


def get_console_logger(component: str = "app") -> logging.LoggerAdapter:
    return logging.LoggerAdapter(logging.getLogger(LOGGER_NAME), {"component": component})
