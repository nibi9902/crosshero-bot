"""
Inspecciona el DOM del calendari Crosshero filtrat per programa.
Guarda HTML i pantalla a /tmp per analitzar l'estructura.
"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

STORAGE = Path(__file__).parent / "storage_state.json"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context(storage_state=str(STORAGE))
    page = context.new_page()
    page.goto("https://crosshero.com/dashboard", wait_until="domcontentloaded")
    page.wait_for_load_state("networkidle", timeout=10000)

    if "sign_in" in page.url:
        print("❌ Sessió caducada")
        sys.exit(1)

    page.get_by_role("link", name=" Horarios").click()
    page.wait_for_load_state("networkidle", timeout=10000)

    page.get_by_title("Todos programas").click()
    page.get_by_role("treeitem", name="Hyrox").click()
    page.wait_for_timeout(2000)

    # Captura screenshot
    page.screenshot(path="/tmp/crosshero_horarios.png", full_page=True)
    print("📸 Screenshot: /tmp/crosshero_horarios.png")

    # Guarda HTML complet
    html = page.content()
    Path("/tmp/crosshero_horarios.html").write_text(html)
    print(f"📄 HTML guardat: /tmp/crosshero_horarios.html ({len(html)} bytes)")

    # Llista tots els links visibles que continguin "Hyrox" o números
    print("\n--- Links amb 'Hyrox' o que semblin classes ---")
    links = page.locator("a").all()
    for i, link in enumerate(links):
        try:
            text = link.inner_text().strip()
            href = link.get_attribute("href") or ""
            if "hyrox" in text.lower() or "hyrox" in href.lower():
                print(f"[{i}] href={href!r}")
                print(f"     text={text!r}")
                print(f"     html={link.inner_html()[:200]!r}")
                print()
        except Exception:
            pass

    input("\n>>> ENTER per tancar...")
    browser.close()
