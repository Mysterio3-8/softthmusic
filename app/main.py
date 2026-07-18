from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Позволяет запускать как `python app/main.py` — корень проекта в sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import ConfigError, load_config  # noqa: E402
from app.database import Database  # noqa: E402
from app.logger import setup_logging  # noqa: E402
from app.publisher import run_once  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="YouTube -> VK Auto Publisher")
    parser.add_argument("--config", default="config.yaml", help="путь к config.yaml")
    parser.add_argument("--env", default=".env", help="путь к .env")
    parser.add_argument(
        "--schedule",
        action="store_true",
        help="постоянный режим: ежедневный запуск в 00:00 через APScheduler",
    )
    args = parser.parse_args()

    try:
        config = load_config(args.config, args.env)
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2

    log = setup_logging(config.log_path)

    if args.schedule:
        _run_scheduled(config)
        return 0

    db = Database(config.database_path)
    try:
        run_once(config, db)
    except Exception:  # noqa: BLE001 — верхний уровень, логируем и падаем с кодом
        log.exception("Непредвиденная ошибка при запуске")
        return 1
    finally:
        db.close()
    return 0


def _run_scheduled(config) -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler

    from app.logger import get_logger

    log = get_logger()
    scheduler = BlockingScheduler()

    def daily_job() -> None:
        db = Database(config.database_path)
        try:
            run_once(config, db)
        except Exception:  # noqa: BLE001
            log.exception("Ошибка в ежедневной задаче")
        finally:
            db.close()

    scheduler.add_job(daily_job, "cron", hour=0, minute=0, id="daily")
    log.info("Планировщик запущен: ежедневно в 00:00. Первый прогон сейчас.")
    daily_job()
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Планировщик остановлен")


if __name__ == "__main__":
    raise SystemExit(main())
