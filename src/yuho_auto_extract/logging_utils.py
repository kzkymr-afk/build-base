from __future__ import annotations

import logging
import os


def configure_logging(level: str = None) -> None:
    raw_level = level or os.getenv("YUHO_LOG_LEVEL", "INFO")
    logging.basicConfig(
        level=getattr(logging, raw_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
