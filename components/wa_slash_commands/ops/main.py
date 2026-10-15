"""
ops/main.py -- Entry point for WA Ops Platform
Starts both the FastAPI server and the background scheduler.
"""
import logging
import uvicorn
from threading import Thread

from ops.api import app
from ops.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def run_scheduler():
    """Run the scheduler in a separate thread."""
    try:
        start_scheduler()
    except Exception as e:
        logger.error(f"Scheduler error: {e}")


def main():
    logger.info("Starting WA Ops Platform...")

    # Start scheduler in background thread
    scheduler_thread = Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
    logger.info("Scheduler thread started.")

    # Trigger immediate nudge check for active cards
    from ops.scheduler import nudge_active_cards
    logger.info("Running initial nudge check...")
    nudge_active_cards()

    # Start FastAPI server
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
