import logging
import logging.config
import logging.handlers
from pathlib import Path


def configure_logging(log_dir: Path, level: str = "INFO"):
    log_dir.mkdir(parents=True, exist_ok=True)
    normalized_level = (level or "INFO").upper()

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "standard": {
                    "format": "%(asctime)s %(levelname)s [%(name)s] %(message)s",
                },
                "access": {
                    "format": "%(asctime)s %(levelname)s [%(name)s] %(client_addr)s %(request_line)s %(status_code)s",
                },
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "level": normalized_level,
                    "formatter": "standard",
                },
                "server_file": {
                    "class": "logging.handlers.RotatingFileHandler",
                    "level": normalized_level,
                    "formatter": "standard",
                    "filename": str(log_dir / "server.log"),
                    "maxBytes": 5 * 1024 * 1024,
                    "backupCount": 5,
                    "encoding": "utf-8",
                },
                "error_file": {
                    "class": "logging.handlers.RotatingFileHandler",
                    "level": "ERROR",
                    "formatter": "standard",
                    "filename": str(log_dir / "error.log"),
                    "maxBytes": 5 * 1024 * 1024,
                    "backupCount": 10,
                    "encoding": "utf-8",
                },
                "import_file": {
                    "class": "logging.handlers.RotatingFileHandler",
                    "level": normalized_level,
                    "formatter": "standard",
                    "filename": str(log_dir / "import.log"),
                    "maxBytes": 5 * 1024 * 1024,
                    "backupCount": 5,
                    "encoding": "utf-8",
                },
                "api_file": {
                    "class": "logging.handlers.RotatingFileHandler",
                    "level": normalized_level,
                    "formatter": "standard",
                    "filename": str(log_dir / "api.log"),
                    "maxBytes": 5 * 1024 * 1024,
                    "backupCount": 5,
                    "encoding": "utf-8",
                },
            },
            "loggers": {
                "uvicorn": {
                    "handlers": ["console", "server_file", "error_file"],
                    "level": normalized_level,
                    "propagate": False,
                },
                "uvicorn.error": {
                    "handlers": ["console", "server_file", "error_file"],
                    "level": normalized_level,
                    "propagate": False,
                },
                "app.import": {
                    "handlers": ["console", "import_file", "error_file"],
                    "level": normalized_level,
                    "propagate": False,
                },
                "app.api": {
                    "handlers": ["console", "api_file", "error_file"],
                    "level": normalized_level,
                    "propagate": False,
                },
                "sqlalchemy.engine": {
                    "handlers": ["console", "server_file", "error_file"],
                    "level": "WARNING",
                    "propagate": False,
                },
            },
            "root": {
                "handlers": ["console", "server_file", "error_file"],
                "level": normalized_level,
            },
        }
    )
