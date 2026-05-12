"""
Llista totes les classes d'un programa visibles al calendari de Crosshero.

Ús:
    python list_classes.py --program "Hyrox"
    python list_classes.py --program "Hyrox" --days 7

Imprimeix JSON amb les classes trobades (data, hora, places, URL).
"""
import argparse
import json
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


CROSSHERO_DASHBOARD = "https://crosshero.com/dashboard"
DEFAULT_STORAGE = Path(__file__).parent / "storage_state.json"


def extract_classes(page, program: str) -> list:
    """
    Recorre el calendari visible i extreu informació estructurada de cada classe.
    Estratègia: agafem tots els links que portin a /classes/, llegim el seu HTML
    intern (que inclou hora, dia, places...) i el de l'element pare per la data.
    """
    classes = []

    # Tots els links a classes (típic Crosshero: /athletes/classes/<id>)
    links = page.locator("a[href*='/classes/']").all()
    for link in links:
        href = link.get_attribute("href") or ""
        # Extreu ID
        m = re.search(r"/classes/(\d+)", href)
        class_id = m.group(1) if m else None

        # Text complet del link (inclou tots els fills)
        text = link.inner_text().strip()
        # Nom del programa està al text? si no és el que busquem, saltar
        if program.lower() not in text.lower():
            continue

        # Buscar hora HH:MM dins del text
        hour_match = re.search(r"\b(\d{1,2}:\d{2})\b", text)
        hora = hour_match.group(1) if hour_match else None

        # Buscar "X de Y" (places ocupades / totals)
        places_match = re.search(r"(\d+)\s*de\s*(\d+)", text)
        places = None
        if places_match:
            places = {
                "ocupades": int(places_match.group(1)),
                "totals": int(places_match.group(2)),
                "lliures": int(places_match.group(2)) - int(places_match.group(1)),
            }

        # Data: intentem llegir-la del contenidor pare (capçalera de columna)
        # Crosshero típicament té una taula amb una capçalera per dia
        try:
            # Cerca l'ancestor amb data al heading
            col = link.locator("xpath=ancestor::*[contains(@class,'col') or contains(@class,'day')][1]")
            col_text = col.inner_text()[:200] if col.count() > 0 else ""
        except Exception:
            col_text = ""

        classes.append({
            "id": class_id,
            "href": href,
            "program": program,
            "hora": hora,
            "places": places,
            "text_raw": text,
            "col_context": col_text[:120],
        })

    return classes


def main(program: str, days: int = 7, headless: bool = True,
         storage_state: Path = DEFAULT_STORAGE) -> dict:
    if not storage_state.exists():
        return {"ok": False, "error": f"No existeix {storage_state}"}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(storage_state=str(storage_state))
        page = context.new_page()

        try:
            page.goto(CROSSHERO_DASHBOARD, wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle", timeout=10000)

            if "sign_in" in page.url:
                return {"ok": False, "error": "Sessió caducada"}

            page.get_by_role("link", name=" Horarios").click()
            page.wait_for_load_state("networkidle", timeout=10000)

            # Filtrar per programa
            page.get_by_title("Todos programas").click()
            page.get_by_role("treeitem", name=program).click()
            page.wait_for_timeout(1500)

            all_classes = []
            # Crosshero mostra una setmana. Avancem fins a 'days' dies amb el botó "Next"
            # (per ara llegim la setmana actual; després iterem si cal)
            all_classes.extend(extract_classes(page, program))

            # Si volem més dies, intentem avançar setmana
            weeks_to_advance = (days // 7)
            for _ in range(weeks_to_advance):
                next_btn = page.get_by_role("button", name=re.compile("Next|Sig|>", re.I))
                if next_btn.count() == 0:
                    break
                next_btn.first.click()
                page.wait_for_timeout(1500)
                all_classes.extend(extract_classes(page, program))

            return {"ok": True, "count": len(all_classes), "classes": all_classes}

        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--program", required=True)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--no-headless", action="store_true",
                        help="Mostra el navegador (útil per debug)")
    args = parser.parse_args()

    res = main(args.program, args.days, headless=not args.no_headless)
    print(json.dumps(res, indent=2, ensure_ascii=False))
    sys.exit(0 if res["ok"] else 1)
