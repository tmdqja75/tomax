"""Capture the interactive React dashboard as a single PNG via headless Chromium.

Reuses the same payload, UI build, and loopback server the interactive
``dashboard`` command uses, then drives Playwright/Chromium to screenshot the
fully rendered page. Everything is local: an ephemeral loopback server, a
route allow-list that blocks any non-loopback request, and a server that is
always shut down in a ``finally`` block.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from datetime import date
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from tomax.dashboard.payload import build_payload
from tomax.dashboard.server import make_server
from tomax.dashboard.ui_build import resolve_dist_dir
from tomax.privacy import PrivacyPolicy

_SETTLE_MS = 2000
_MISSING_BROWSER_HINTS = ("Executable doesn't exist", "playwright install")


def _url_allowed(url: str, prefix: str) -> bool:
    """True only for URLs served by our own loopback server."""
    return url.startswith(prefix)


def _launch_chromium(playwright, *, installer=subprocess.run):
    """Launch headless Chromium, auto-installing it once if the binary is missing."""
    try:
        return playwright.chromium.launch(headless=True)
    except (PlaywrightError, RuntimeError) as error:
        message = str(error)
        if not any(hint in message for hint in _MISSING_BROWSER_HINTS):
            raise
        result = installer(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True,
            text=True,
        )
        if getattr(result, "returncode", 0) != 0:
            raise RuntimeError(
                "failed to install Chromium for dashboard export — run "
                "`playwright install chromium` manually"
            ) from error
        return playwright.chromium.launch(headless=True)


def screenshot_payload(
    payload: dict,
    output_path: Path,
    *,
    dist_dir: Path,
    lang: str = "en",
    width: int = 1100,
    scale: int = 2,
) -> None:
    """Screenshot an already-assembled payload against an already-built dist."""
    server = make_server(payload, dist_dir=dist_dir, host="127.0.0.1", port=0, lang=lang)
    port = server.server_address[1]
    prefix = f"http://127.0.0.1:{port}/"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = _launch_chromium(playwright)
            context = browser.new_context(
                viewport={"width": width, "height": 900},
                device_scale_factor=scale,
                color_scheme="dark",
                reduced_motion="reduce",
            )
            context.route(
                "**/*",
                lambda route: (
                    route.continue_()
                    if _url_allowed(route.request.url, prefix)
                    else route.abort()
                ),
            )
            page = context.new_page()
            page.goto(prefix, wait_until="networkidle")
            page.wait_for_function(
                "document.querySelector('.dashboard')"
                " && !document.body.innerText.includes('Loading…')"
            )
            page.evaluate("document.fonts.ready")
            page.wait_for_timeout(_SETTLE_MS)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(output_path), full_page=True, type="png")
            context.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


def export_dashboard_png(
    output_path: Path,
    *,
    ledger_path: Path,
    all_devices: bool,
    repo_target: str | None,
    privacy_policy: PrivacyPolicy,
    today: date,
    ui_dir: Path,
    tmp_stage_dir: Path,
    lang: str = "en",
    pie_top_n: int = 6,
    bar_chart_threshold_days: int = 15,
    width: int = 1100,
    scale: int = 2,
    force_build: bool = False,
) -> None:
    """Assemble the local payload, build the UI, and screenshot it to ``output_path``."""
    payload = build_payload(
        ledger_path=ledger_path,
        all_devices=all_devices,
        repo_target=repo_target,
        privacy_policy=privacy_policy,
        today=today,
        pie_top_n=pie_top_n,
        bar_chart_threshold_days=bar_chart_threshold_days,
        tmp_stage_dir=tmp_stage_dir,
    )
    dist_dir = resolve_dist_dir(ui_dir, force=force_build)
    screenshot_payload(
        payload, output_path, dist_dir=dist_dir, lang=lang, width=width, scale=scale
    )
