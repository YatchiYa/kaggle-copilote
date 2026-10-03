"""End-to-end UI test against a running Podium (default http://127.0.0.1:8000). No AI calls are made.
Run: make e2e   (or: uv run --with playwright python tests/e2e_ui.py [base_url])"""
import sys

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
PAGES = ["overview", "ongoing", "competitions/all", "submissions", "activity", "decisions", "alerts", "spend", "platforms", "settings"]

with sync_playwright() as p:
    browser = p.chromium.launch()
    errors = []
    for width in (1440, 390):  # desktop and phone
        page = browser.new_page(viewport={"width": width, "height": 900})
        page.on("pageerror", lambda e: errors.append(f"{width}px: {e}"))
        for route in PAGES:
            page.goto(f"{BASE}/#/{route}")
            page.wait_for_selector("#view h1", timeout=15000)
            assert "Could not load" not in page.inner_text("#view"), f"{route} failed to load"
        page.close()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{BASE}/#/competitions/all")
    page.wait_for_selector("#ctBody")
    n = page.locator("#ctBody tr").count()
    page.click("th[data-sort='deadline']")
    page.wait_for_selector("#ctBody")
    page.fill("#ctQ", "zzzz-no-such-competition")
    assert "No competitions match" in page.inner_text("#ctBody"), "search did not filter"
    page.fill("#ctQ", "")
    page.click("#ctClear")
    page.wait_for_selector("#ctBody")
    assert page.locator("#ctBody tr").count() == n, "clear did not restore rows"
    page.click("#copilotBtn")
    assert page.is_visible("#copilot") and page.is_visible("#cpText"), "copilot did not open"
    page.keyboard.press("Escape")
    browser.close()
    assert not errors, errors
print(f"e2e ok: {len(PAGES)} pages x 2 viewports, table search/sort/clear, copilot panel")
