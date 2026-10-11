"""Main service entry point for Kas WRI WhatsApp Dues Bot.

Launches the FastAPI application via uvicorn along with background
workers for Outbox synchronization and monthly billing scheduling.
"""

import asyncio
import logging
import sys

import uvicorn

from wri_kas_bot.app import app
from wri_kas_bot.config import get_settings
from wri_kas_bot.database import Database
from wri_kas_bot.outbox import OutboxWorker
from wri_kas_bot.scheduler import setup_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("wri_kas_bot")


async def run_server() -> None:
    """Launch the application alongside OutboxWorker and Scheduler."""
    settings = get_settings()
    db_path = settings.database_url
    if "sqlite" in db_path:
        db_path = db_path.split(":///")[-1]

    db = Database(db_path)
    db.init_schema()

    # 1. Initialize background Outbox worker
    stop_event = asyncio.Event()
    worker = OutboxWorker(db=db)
    worker_task = asyncio.create_task(worker.run_loop(poll_interval=5.0, stop_event=stop_event))

    # 2. Initialize APScheduler
    scheduler = setup_scheduler(db=db)
    scheduler.start()
    logger.info("APScheduler initialized for monthly billing blasts")

    # 3. Start Uvicorn Web Server
    config = uvicorn.Config(app=app, host=settings.host, port=settings.port, log_level="info")
    server = uvicorn.Server(config)

    try:
        await server.serve()
    finally:
        stop_event.set()
        await worker_task
        scheduler.shutdown(wait=False)
        logger.info("Clean shutdown completed")


def main() -> None:
    """Entry point for CLI or docker container."""
    try:
        asyncio.run(run_server())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Process terminated by user")
        sys.exit(0)


if __name__ == "__main__":
    main()
