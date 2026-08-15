import asyncio

from app.settings.config import settings
from app.settings.logging_config import setup_logging
from app.worker.consumer import run_forever

setup_logging(settings.log_level)
asyncio.run(run_forever())
