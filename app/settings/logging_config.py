import logging.config


def setup_logging(level: str = "INFO") -> None:
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
                    "datefmt": "%Y-%m-%d %H:%M:%S",
                },
            },
            "handlers": {
                "console": {"class": "logging.StreamHandler", "formatter": "default"},
            },
            "root": {"handlers": ["console"], "level": level},
            "loggers": {
                "sqlalchemy.engine": {"level": "WARNING", "propagate": True},
                "uvicorn.access": {"level": "INFO", "propagate": True},
                "botocore": {"level": "WARNING", "propagate": True},
            },
        }
    )
