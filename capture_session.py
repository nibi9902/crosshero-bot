from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://crosshero.com/athletes/sign_in")
    print("\n>>> Fes login manualment (inclòs reCAPTCHA) i arriba al dashboard.")
    print(">>> Quan hi siguis, torna aquí i prem ENTER.\n")
    input()
    context.storage_state(path="storage_state.json")
    print("✅ Sessió guardada a storage_state.json")
    browser.close()
