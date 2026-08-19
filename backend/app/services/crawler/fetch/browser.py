"""Chrome driver lifecycle and robust page loading.

selenium / undetected_chromedriver are imported inside the functions so the rest
of the package (and the FastAPI app importing it) needs no browser installed.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from app.services.crawler.common.constants import (
    PAGE_LOAD_BACKOFF,
    PAGE_LOAD_RETRIES,
)

if TYPE_CHECKING:
    from selenium.webdriver.remote.webdriver import WebDriver

logger = logging.getLogger(__name__)

# Pin to Google Chrome rather than the snap chromium, whose version may not match
# the available chromedriver.
_CHROME_BINARIES = ("/usr/bin/google-chrome", "/usr/bin/google-chrome-stable")

_CHROME_ARGS = (
    "--window-size=1920x1080",
    "--accept-language=en-US,en;q=0.9,zh-CN;q=0.8",
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-gpu",
    "--disable-extensions",
    "--disable-background-networking",
)

_DRIVER_ATTEMPTS = 3
_PAGE_LOAD_TIMEOUT = 120


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip() in ("1", "true", "yes")


def _chrome_binary() -> str | None:
    return next((path for path in _CHROME_BINARIES if os.path.exists(path)), None)


def _chrome_major_version(binary: str | None) -> int | None:
    """Read Chrome's major version from the same binary we hand to uc, so the
    driver and the browser always agree."""
    try:
        result = subprocess.run(
            [binary or "google-chrome", "--version"], capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            version = int(result.stdout.strip().split()[2].split(".")[0])
            logger.info("Detected Chrome major version: %s (binary: %s)", version, binary)
            return version
    except Exception as exc:
        logger.warning("Could not detect Chrome version: %s", exc)
    return None


def _start_display() -> tuple[bool, Any | None]:
    """Pick a display strategy and return (headless, virtual_display).

    MONITOR_USE_WSLG=1  headed Chrome on WSLg's existing X display — the same
                        strong anti-bot posture as production, without Xvfb
                        (which hangs undetected_chromedriver on WSL2).
    MONITOR_HEADLESS=1  --headless=new, no X server. Fastest, but some sites
                        block or challenge headless.
    unset (production)  headed Chrome inside a fresh Xvfb display — what every
                        site was validated against.
    """
    if _env_flag("MONITOR_USE_WSLG"):
        os.environ.setdefault("DISPLAY", ":0")
        logger.info("Using existing X display (WSLg) at DISPLAY=%s — headed, no Xvfb", os.environ["DISPLAY"])
        return False, None

    if _env_flag("MONITOR_HEADLESS"):
        return True, None

    if sys.platform.startswith("linux"):
        try:
            from pyvirtualdisplay import Display

            display = Display(visible=0, size=(1920, 1080))
            display.start()
            logger.info("Started virtual display (Xvfb)")
            time.sleep(2)
            return False, display
        except Exception as exc:
            logger.warning("Could not start virtual display: %s", exc)

    return False, None


def _build_driver() -> tuple[WebDriver, Any | None]:
    import undetected_chromedriver as uc

    binary = _chrome_binary()
    version_main = _chrome_major_version(binary)
    headless, display = _start_display()

    for attempt in range(1, _DRIVER_ATTEMPTS + 1):
        try:
            options = uc.ChromeOptions()
            for arg in _CHROME_ARGS:
                options.add_argument(arg)
            if binary:
                options.binary_location = binary
            if headless:
                options.add_argument("--headless=new")
            driver = uc.Chrome(options=options, headless=headless, version_main=version_main)
            driver.set_page_load_timeout(_PAGE_LOAD_TIMEOUT)
            logger.info("undetected_chromedriver ready")
            return driver, display
        except Exception as exc:
            logger.error("Driver attempt %d/%d failed: %s", attempt, _DRIVER_ATTEMPTS, exc)
            if attempt == _DRIVER_ATTEMPTS:
                raise
            time.sleep(3)

    raise RuntimeError("unreachable")  # pragma: no cover


@contextmanager
def browser_session() -> Iterator[WebDriver]:
    """Yield a ready driver and always tear it (and any Xvfb display) down."""
    driver, display = _build_driver()
    try:
        yield driver
    finally:
        for close in (getattr(driver, "quit", None), getattr(display, "stop", None)):
            if close is None:
                continue
            try:
                close()
            except Exception:
                pass


def load_page(driver: WebDriver, url: str, min_anchors: int = 0, wait_for: str | None = None) -> bool:
    """Load a URL, waiting until its content is actually in the DOM.

    Retries with linear backoff, waits for readyState=complete, then — when
    min_anchors > 0 — until that many <a> elements exist, so a JS-rendered list is
    present before we scrape. Scrolls to the bottom to trigger lazy loading.

    `wait_for` (a per-site CSS selector) is for lists that render AFTER
    readyState=complete via an async fetch: the generic min_anchors gate is
    satisfied by header/footer links and would fire on an empty container.
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait

    last_error: Exception | None = None
    for attempt in range(1, PAGE_LOAD_RETRIES + 1):
        try:
            driver.get(url)
            WebDriverWait(driver, 20).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )

            if min_anchors > 0:
                try:
                    WebDriverWait(driver, 15).until(
                        lambda d: len(d.find_elements(By.TAG_NAME, "a")) >= min_anchors
                    )
                except Exception:
                    time.sleep(2)  # threshold never reached; the page may be sparse
            else:
                time.sleep(2)

            # Scroll BEFORE the wait_for gate: some lists render the container
            # early but only populate their cards after a scroll, so waiting first
            # would time out on an empty container.
            _scroll_to_bottom(driver)

            if wait_for:
                _wait_for_selector(driver, wait_for)
            return True
        except Exception as exc:
            last_error = exc
            if attempt < PAGE_LOAD_RETRIES:
                delay = PAGE_LOAD_BACKOFF * attempt
                logger.warning(
                    "    Load attempt %d/%d failed: %s; retrying in %ds",
                    attempt, PAGE_LOAD_RETRIES, exc, delay,
                )
                time.sleep(delay)

    logger.error("    Failed to load after %d attempts: %s", PAGE_LOAD_RETRIES, last_error)
    return False


def _scroll_to_bottom(driver: WebDriver, pause: float = 1.0) -> None:
    try:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(pause)
    except Exception:
        pass


def _wait_for_selector(driver: WebDriver, selector: str, timeout: int = 25) -> None:
    """Wait for an async-rendered container, re-scrolling on every poll so lazy
    loaders that key off scroll EVENTS (not position) keep getting nudged.

    A timeout is not fatal: extraction then reports zero items and the run summary
    flags the site.
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait

    def seen(d: WebDriver) -> bool:
        _scroll_to_bottom(d, pause=0)
        return len(d.find_elements(By.CSS_SELECTOR, selector)) > 0

    try:
        WebDriverWait(driver, timeout, poll_frequency=1.5).until(seen)
    except Exception:
        logger.info("    wait_for %r not seen within %ds", selector, timeout)
