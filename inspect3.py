import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

STORAGE = Path("storage_state.json")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(storage_state=str(STORAGE), viewport={"width": 1400, "height": 900})
    page = context.new_page()
    page.goto("https://crosshero.com/dashboard", wait_until="load")
    page.wait_for_timeout(5000)  # esperem 5s
    print(f"URL: {page.url}")
    print(f"TITLE: {page.title()}")
    html = page.content()
    print(f"HTML len: {len(html)}")
    print(f"\nPRIMERS 2000 CHARS HTML:\n{html[:2000]}")
    page.screenshot(path="/tmp/ch3.png", full_page=True)
    browser.close()
