import sys, re
from pathlib import Path
from playwright.sync_api import sync_playwright

STORAGE = Path("storage_state.json")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(
        storage_state=str(STORAGE),
        viewport={"width": 1400, "height": 900}
    )
    page = context.new_page()
    page.goto("https://crosshero.com/dashboard", wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle", timeout=15000)
    print(f"URL: {page.url}")
    print(f"TITLE: {page.title()}")
    if "sign_in" in page.url:
        print("SESSION_EXPIRED"); sys.exit(1)
    page.screenshot(path="/tmp/ch1_dashboard.png", full_page=True)
    
    # llista tots els links de navegació
    print("\nLINKS (top 50):")
    links = page.locator("a").all()[:50]
    for i, l in enumerate(links):
        try:
            t = (l.inner_text() or "").strip()
            h = l.get_attribute("href") or ""
            if t or h:
                print(f"[{i}] href={h!r} text={t[:80]!r}")
        except: pass
    browser.close()
