import sys, json, re
from pathlib import Path
from playwright.sync_api import sync_playwright

STORAGE = Path("storage_state.json")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(storage_state=str(STORAGE))
    page = context.new_page()
    page.goto("https://crosshero.com/dashboard", wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle", timeout=15000)

    if "sign_in" in page.url:
        print("SESSION_EXPIRED")
        sys.exit(1)

    page.get_by_role("link", name=" Horarios").click()
    page.wait_for_load_state("networkidle", timeout=15000)

    # Llista programes disponibles abans de filtrar
    page.get_by_title("Todos programas").click()
    page.wait_for_timeout(500)
    tree_items = page.get_by_role("treeitem").all()
    print("PROGRAMES_DISPONIBLES:")
    for t in tree_items:
        try: print(f"  - {t.inner_text().strip()!r}")
        except: pass

    # Filtra Hyrox
    page.get_by_role("treeitem", name="Hyrox").click()
    page.wait_for_timeout(2500)

    page.screenshot(path="/tmp/ch_full.png", full_page=True)
    Path("/tmp/ch.html").write_text(page.content())

    print(f"\nURL_ACTUAL: {page.url}")
    print(f"HTML_BYTES: {len(page.content())}")

    # Tots els links amb el seu text/href
    print("\nTOTS_LINKS_NO_BUITS:")
    links = page.locator("a").all()
    for i, link in enumerate(links):
        try:
            text = (link.inner_text() or "").strip()
            href = link.get_attribute("href") or ""
            if text and ("hyrox" in text.lower() or re.search(r"\d{1,2}:\d{2}", text) or re.search(r"/(class|sched|booking|reserv)", href.lower())):
                print(f"[{i}] {href!r} :: {text!r}")
        except: pass

    browser.close()
