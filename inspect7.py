import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

STORAGE = Path("storage_state.json")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
HYROX = "66c5895463564f00309d611b"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
    context = browser.new_context(storage_state=str(STORAGE), viewport={"width": 1400, "height": 900}, user_agent=UA, locale="es-ES")
    page = context.new_page()
    page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

    # Prova URL amb data + programa
    test_url = f"https://crosshero.com/dashboard/classes?date=15/05/2026&program_id={HYROX}"
    page.goto(test_url, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    print(f"URL: {page.url}")
    
    # Forms info
    forms = page.locator("form").all()
    print(f"\nForms: {len(forms)}")
    for i, f in enumerate(forms):
        try:
            action = f.get_attribute("action")
            method = f.get_attribute("method") or "GET"
            print(f"\n[Form {i}] {method} {action}")
            inputs = f.locator("input, select").all()
            for inp in inputs:
                tag = inp.evaluate("e => e.tagName")
                name = inp.get_attribute("name") or ""
                typ = inp.get_attribute("type") or ""
                val = inp.get_attribute("value") or ""
                if tag == "SELECT":
                    opts = inp.locator("option").all()
                    print(f"    SELECT {name!r} → {len(opts)} opcions")
                    for o in opts:
                        try:
                            ov = o.get_attribute("value") or ""
                            ot = o.inner_text().strip()
                            sel = "✓" if o.get_attribute("selected") else " "
                            print(f"      [{sel}] {ov!r} :: {ot!r}")
                        except: pass
                else:
                    print(f"    {tag} {typ!r} name={name!r} value={val!r}")
        except Exception as e:
            print(f"  err: {e}")

    # Botons visibles
    print("\nBOTONS:")
    btns = page.locator("button, input[type=submit]").all()
    for b in btns[:10]:
        try:
            t = (b.inner_text() or b.get_attribute("value") or "").strip()
            if t: print(f"  {t!r}")
        except: pass
    
    page.screenshot(path="/tmp/ch7.png", full_page=True)
    Path("/tmp/ch7.html").write_text(page.content())
    browser.close()
