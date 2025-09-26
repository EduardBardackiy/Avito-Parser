from __future__ import annotations

import schedule
import time

from utils.logger import get_logger


def run_scheduler(job_callable, every_minutes: int = 5) -> None:
    logger = get_logger("scheduler")
    schedule.every(every_minutes).minutes.do(job_callable)
    logger.info(f"Scheduler started. Running job every {every_minutes} minute(s)")
    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Scheduler stopped by user")


