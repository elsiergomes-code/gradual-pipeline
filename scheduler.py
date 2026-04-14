"""
scheduler.py — Master scheduler for Gradual Holdings Inc.
Runs all agents under one APScheduler instance on Railway.

Jobs:
  - LinkedIn post: every 2 days at 8:00 AM Toronto
  - Newsletter:    every Tuesday at 8:00 AM Toronto
"""

import sys
import logging
from datetime import datetime, timedelta

import pytz
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("gradual_scheduler")

TORONTO_TZ = pytz.timezone("America/Toronto")


# ── Import agents ─────────────────────────────────────────────────────────────
def run_linkedin():
    try:
        from main import run_pipeline
        logger.info("Running LinkedIn pipeline...")
        run_pipeline()
    except Exception as e:
        logger.error(f"LinkedIn pipeline error: {e}")


def run_newsletter():
    try:
        from newsletter_agent import run
        logger.info("Running newsletter agent...")
        run()
    except Exception as e:
        logger.error(f"Newsletter agent error: {e}")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":

    # Allow manual test runs
    if "--linkedin-now" in sys.argv:
        run_linkedin()
        sys.exit(0)

    if "--newsletter-now" in sys.argv:
        run_newsletter()
        sys.exit(0)

    # Schedule LinkedIn: every 2 days at 8am Toronto
    now = datetime.now(TORONTO_TZ)
    next_8am = now.replace(hour=8, minute=0, second=0, microsecond=0)
    if next_8am <= now:
        next_8am += timedelta(days=1)

    # Schedule newsletter: every Tuesday at 8am Toronto
    scheduler = BlockingScheduler(timezone=TORONTO_TZ)

    scheduler.add_job(
        run_linkedin,
        trigger=IntervalTrigger(days=2, start_date=next_8am, timezone=TORONTO_TZ),
        id="linkedin_post",
        name="LinkedIn post every 2 days at 08:00 Toronto",
        misfire_grace_time=3600,
    )

    scheduler.add_job(
        run_newsletter,
        trigger=CronTrigger(day_of_week="tue", hour=8, minute=0, timezone=TORONTO_TZ),
        id="newsletter",
        name="The Raw State — every Tuesday 08:00 Toronto",
        misfire_grace_time=3600,
    )

    logger.info("=" * 50)
    logger.info("Gradual Holdings Inc. — Master Scheduler")
    logger.info("=" * 50)
    logger.info(f"LinkedIn  : every 2 days at 08:00 Toronto")
    logger.info(f"Newsletter: every Tuesday at 08:00 Toronto")
    logger.info(f"Next LinkedIn run: {next_8am.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    logger.info("Press Ctrl+C to stop.")
    logger.info("=" * 50)

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")
