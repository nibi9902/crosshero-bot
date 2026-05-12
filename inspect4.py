import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

STORAGE = Path("storage_state.json")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
    context = browser.new_context(
        storage_state=str(STORAGE),
        viewport={"width": 1400, "height": 900},
        user_agent=UA,
        locale="es-ES",
    )
    page = context.new_page()
    # eliminem el flag navigator.webdriver
    page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    
    page.goto("https://crosshero.com/dashboard", wait_until="domcontentloaded")
    page.wait_for_timeout(4000)
    print(f"URL: {page.url}")
    print(f"TITLE: {page.title()}")
    html = page.content()
    print(f"HTML len: {len(html)}")
    if "Service unavailable" in html:
        print("⚠️ SERVICE UNAVAILABLE")
        print(html[:500])
    else:
        print("\nLINKS:")
        for l in page.locator("a").all()[:40]:
            try:
                t=(l.inner_text() or "").strip()[:60]; h=l.get_attribute("href") or ""
                if t or h: print(f"  {h!r} :: {t!r}")
            except: pass
    page.screenshot(path="/tmp/ch4.png", full_page=True)
    browser.close()
