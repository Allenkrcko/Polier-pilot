"""Worker entrypoint. Replaces the raw `rq worker` command so logging is configured."""
from __future__ import annotations

import logging

from rq import Worker

from app.logging_config import configure_logging
from app.workers.queue import default_queue, redis_conn

logger = logging.getLogger(__name__)


def main() -> None:
    configure_logging()
    queue = default_queue()
    logger.info("Starting RQ worker on queue=%s", queue.name)
    Worker([queue], connection=redis_conn()).work(with_scheduler=False)


if __name__ == "__main__":
    main()
