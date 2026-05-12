import sys, re
from pathlib import Path
from playwright.sync_api import sync_playwright

STORAGE = Path("storage_state.json")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
HYROX_PROGRAM_ID = "66c5895463564f00309d611b"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
    context = browser.new_context(storage_state=str(STORAGE), viewport={"width": 1400, "height": 900}, user_agent=UA, locale="es-ES")
    page = context.new_page()
    page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

    # Anem directe a la pàgina de classes
    page.goto("https://crosshero.com/dashboard/classes", wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    print(f"URL: {page.url}")
    html = page.content()
    print(f"HTML len: {len(html)}")
    
    # Buscar enllaços a classes amb date/id
    print("\nCLASS_LINKS (amb date+id):")
    links = page.locator("a").all()
    for l in links:
        try:
            h = l.get_attribute("href") or ""
            if "date=" in h and "id=" in h:
                t = (l.inner_text() or "").strip()[:80]
                print(f"  {h}")
                print(f"     :: {t!r}")
        except: pass
    
    page.screenshot(path="/tmp/ch5_classes.png", full_page=True)
    Path("/tmp/ch5_classes.html").write_text(html)
    browser.close()
