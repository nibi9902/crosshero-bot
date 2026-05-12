import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

STORAGE = Path("storage_state.json")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
    context = browser.new_context(storage_state=str(STORAGE), viewport={"width": 1400, "height": 900}, user_agent=UA, locale="es-ES")
    page = context.new_page()
    page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    
    # Captura XHR/fetch
    requests_log = []
    page.on("request", lambda r: requests_log.append((r.method, r.url, r.resource_type)))
    
    page.goto("https://crosshero.com/dashboard/classes", wait_until="domcontentloaded")
    page.wait_for_timeout(5000)
    
    print("XHR/FETCH requests:")
    for m, u, rt in requests_log:
        if rt in ("xhr", "fetch") or "/api/" in u or "json" in u.lower():
            print(f"  {m} [{rt}] {u}")
    
    print("\n--- Buscant elements de calendari ---")
    # Mira els elements que poden ser classes
    for sel in [".class-item", ".calendar-class", "[data-class-id]", ".schedule-item", "tr.class", ".event"]:
        cnt = page.locator(sel).count()
        if cnt: print(f"  {sel}: {cnt}")
    
    # Mira si hi ha select de programa
    print("\n--- Selects ---")
    selects = page.locator("select").all()
    for s in selects:
        try:
            name = s.get_attribute("name") or s.get_attribute("id") or "?"
            opts = s.locator("option").all()
            print(f"  <select {name!r}>: {len(opts)} opcions")
            for o in opts[:6]:
                print(f"    {o.get_attribute('value')!r} :: {o.inner_text()[:40]!r}")
        except: pass

    # cerca text "Hyrox" a la pàgina
    print(f"\nText 'Hyrox' apareix: {page.locator('text=Hyrox').count()} cops")
    print(f"Text 'HYROX' apareix: {page.locator('text=HYROX').count()} cops")
    
    browser.close()
