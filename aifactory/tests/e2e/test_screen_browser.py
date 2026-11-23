"""Secret screen in the shipped dashboard, without real capture or native input."""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import Request, expect, sync_playwright
from test_f3_browser import _launch

from advice_fixture import make_repo
from advice_server import dashboard

pytestmark = [pytest.mark.browser, pytest.mark.xdist_group("e2e")]


def _has_event(request: Request, kind: str, **fields: object) -> bool:
    if request.method != "POST" or not request.url.endswith("/api/screen/input"):
        return False
    body = request.post_data_json
    if body is None:
        return False
    return any(
        event.get("type") == kind and all(event.get(key) == value for key, value in fields.items())
        for event in body["events"]
    )


def test_screen_browser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIRO_SCREEN_BACKEND", "fake")
    repo = make_repo(tmp_path / "repo")
    with dashboard(repo, tmp_path / "home") as (url, identifier), sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page()
            page.set_default_timeout(15000)
            page.goto(f"{url}/#/r/{identifier}/runs")
            expect(page.locator('[data-test="assistant-toggle"]')).to_be_visible()
            overlay = page.locator('[data-test="screen-overlay"]')
            terminal = page.locator('[data-test="terminal-overlay"]')
            expect(overlay).to_have_count(0)
            for key in ("Shift", "Control", "Shift", "Control"):
                page.keyboard.press(key)
            expect(overlay).to_be_visible()
            canvas = overlay.locator("canvas")
            expect(canvas).to_be_focused()
            # Expose a live pixel getter so Playwright's expect retries until paint.
            # The fake frame has a nonzero grey centre, including with letterboxing.
            canvas.evaluate("""canvas => Object.defineProperty(canvas, 'hasFrame', {
                get() {
                    const pixel = canvas.getContext('2d').getImageData(
                        Math.floor(canvas.width / 2), Math.floor(canvas.height / 2), 1, 1
                    ).data;
                    return pixel[3] === 255 && pixel[0] > 0
                        && pixel[1] > 0 && pixel[2] > 0;
                }
            })""")
            expect(canvas).to_have_js_property("hasFrame", True)

            with page.expect_request(
                lambda request: _has_event(request, "button", button="left", down=True)
            ) as clicked:
                canvas.click()
            body = clicked.value.post_data_json
            assert body is not None
            button = next(
                event for event in body["events"] if event["type"] == "button" and event["down"]
            )
            assert button == {
                "type": "button",
                "button": "left",
                "down": True,
                "x": pytest.approx(0.5, abs=0.01),
                "y": pytest.approx(0.5, abs=0.01),
            }
            with page.expect_request(
                lambda request: _has_event(request, "key", code="KeyA", down=True)
            ):
                page.keyboard.press("KeyA")

            # The terminal shortcut must leave the screen open and usable.
            for key in ("Control", "Shift", "Control", "Shift"):
                page.keyboard.press(key)
            expect(overlay).to_be_visible()
            expect(terminal).to_have_count(0)
            with page.expect_request(lambda request: _has_event(request, "release_all")):
                for key in ("Shift", "Control", "Shift", "Control"):
                    page.keyboard.press(key)
            expect(overlay).to_have_count(0)
            expect(terminal).to_have_count(0)
        finally:
            browser.close()
