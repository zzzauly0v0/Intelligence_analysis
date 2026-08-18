"""Runner for the crawler package."""

from __future__ import annotations

import sys

from app.services.crawler.delivery.cli import main

if __name__ == "__main__":
    sys.exit(main())
