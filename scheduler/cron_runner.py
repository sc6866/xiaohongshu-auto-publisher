from __future__ import annotations

from typing import Callable

from agents.xiaohongshu_manager import XiaohongshuManager
from common.config import Settings
from common.logging_utils import configure_logging, get_logger


DEFAULT_JOBS = [
    {"name": "trend_scan", "schedule": "0 */2 * * *", "agent": "TrendScanner"},
    {"name": "content_production", "schedule": "0 8,12,18 * * *", "agent": "ContentGenerator"},
    {"name": "publish_queue", "schedule": "0 9,13,19 * * *", "agent": "PublishManager"},
    {"name": "data_feedback", "schedule": "0 10 * * *", "agent": "KnowledgeCurator"},
]


def _load_jobs(settings: Settings) -> list[dict[str, str]]:
    try:
        import yaml  # type: ignore
    except ImportError:
        return DEFAULT_JOBS

    path = settings.root_dir / "config" / "cron_schedule.yaml"
    if not path.exists():
        return DEFAULT_JOBS
    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    jobs = data.get("jobs")
    return jobs if isinstance(jobs, list) and jobs else DEFAULT_JOBS


def run_scheduler(settings: Settings) -> None:
    configure_logging(settings.logs_dir, level=settings.get("runtime", "log_level", "INFO"))
    logger = get_logger("CronRunner")
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError as exc:
        raise RuntimeError("APScheduler is not installed. Install dependencies before running the scheduler.") from exc

    manager = XiaohongshuManager(settings)
    scheduler = BlockingScheduler(timezone=settings.timezone)
    jobs = _load_jobs(settings)

    job_mapping: dict[str, Callable[[], object]] = {
        "trend_scan": manager.scan_and_ingest,
        "content_production": manager.produce_content,
        "publish_queue": manager.publish_queue,
        "data_feedback": manager.run_feedback_loop,
    }

    for job in jobs:
        name = job["name"]
        cron_expression = job["schedule"]
        scheduler.add_job(
            job_mapping[name],
            CronTrigger.from_crontab(cron_expression),
            id=name,
            name=name,
            replace_existing=True,
        )
        logger.info("Registered cron job %s -> %s", name, cron_expression)

    logger.info("Scheduler started")
    scheduler.start()


def run_scheduler_once(settings: Settings) -> dict[str, object]:
    configure_logging(settings.logs_dir, level=settings.get("runtime", "log_level", "INFO"))
    logger = get_logger("CronRunner")
    manager = XiaohongshuManager(settings)
    jobs = _load_jobs(settings)

    job_mapping: dict[str, Callable[[], object]] = {
        "trend_scan": manager.scan_and_ingest,
        "content_production": manager.produce_content,
        "publish_queue": manager.publish_queue,
        "data_feedback": manager.run_feedback_loop,
    }

    results: list[dict[str, object]] = []
    for job in jobs:
        name = job["name"]
        logger.info("Running scheduled job once: %s", name)
        try:
            result = job_mapping[name]()
            results.append({"name": name, "status": "ok", "result": result})
        except Exception as exc:  # noqa: BLE001
            results.append({"name": name, "status": "error", "error": str(exc)})
    return {"jobs": results, "job_count": len(results)}
