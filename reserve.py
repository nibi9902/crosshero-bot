"""
Reserva una classe a Crosshero usant una sessió guardada.

Ús local:
    python reserve.py --program "Hyrox" --date 2026-05-15 --time 19:00 --dry-run

Sense --dry-run, fa la reserva real.
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout


CROSSHERO_DASHBOARD = "https://crosshero.com/dashboard"
DEFAULT_STORAGE = Path(__file__).parent / "storage_state.json"

# Mesos en castellà (Crosshero mostra "15 de mayo" estil)
MESOS_ES = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril",
    5: "mayo", 6: "junio", 7: "julio", 8: "agosto",
    9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre",
}


def reserve(program: str, target_date: str, target_time: str,
            storage_state: Path = DEFAULT_STORAGE,
            dry_run: bool = False, headless: bool = False) -> dict:
    """
    program: nom del programa, ex "Hyrox"
    target_date: YYYY-MM-DD
    target_time: HH:MM (24h)
    """
    if not storage_state.exists():
        return {"ok": False, "error": f"No existeix {storage_state}"}

    try:
        dt = datetime.strptime(f"{target_date} {target_time}", "%Y-%m-%d %H:%M")
    except ValueError as e:
        return {"ok": False, "error": f"Format data/hora invàlid: {e}"}

    day_num = dt.day
    month_name = MESOS_ES[dt.month]
    # patró del link al calendari: "Hyrox 15 de mayo" (segons reserve.py codegen)
    class_link_pattern = f"{program} {day_num} de"

    result = {"ok": False, "details": {
        "program": program,
        "date": target_date,
        "time": target_time,
        "day_num": day_num,
        "month": month_name,
        "dry_run": dry_run,
    }}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(storage_state=str(storage_state))
        page = context.new_page()

        try:
            # 1. Anar al dashboard (ja loguejat per la sessió)
            page.goto(CROSSHERO_DASHBOARD, wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle", timeout=10000)

            # Si redirigeix a login, la sessió ha caducat
            if "sign_in" in page.url:
                result["error"] = "Sessió caducada. Refresca storage_state.json."
                return result

            # 2. Anar a Horarios
            page.get_by_role("link", name=" Horarios").click()
            page.wait_for_load_state("networkidle", timeout=10000)

            # 3. Filtrar per programa
            page.get_by_title("Todos programas").click()
            page.get_by_role("treeitem", name=program).click()
            page.wait_for_timeout(1500)

            # 4. Trobar la classe del dia/hora
            #    El link conté "Hyrox 15 de mayo" — però només per dia.
            #    Per filtrar per hora, llegim tots els links del programa+dia
            #    i comparem text contra l'hora.
            candidates = page.get_by_role("link", name=class_link_pattern).all()
            if not candidates:
                result["error"] = f"No s'ha trobat cap classe '{class_link_pattern}'"
                return result

            # Buscar la classe amb l'hora correcta
            target_link = None
            for link in candidates:
                text = link.inner_text()
                if target_time in text:
                    target_link = link
                    break

            if target_link is None:
                texts = [l.inner_text()[:80] for l in candidates]
                result["error"] = f"Cap classe a les {target_time}. Trobats: {texts}"
                return result

            target_link.click()
            page.wait_for_load_state("networkidle", timeout=10000)

            # 5. Botó "Reservar clase"
            reserve_btn = page.get_by_role("button", name="Reservar clase")
            if reserve_btn.count() == 0:
                # Potser ja estem inscrits, o la classe està plena
                page_text = page.locator("body").inner_text()
                if "Cancelar" in page_text or "cancelar" in page_text:
                    result["ok"] = True
                    result["note"] = "Ja estaves reservat"
                    return result
                result["error"] = "No es veu el botó 'Reservar clase'. Possiblement plena o tancada."
                return result

            if dry_run:
                result["ok"] = True
                result["note"] = "DRY-RUN: botó 'Reservar clase' trobat però no clicat"
                return result

            reserve_btn.click()
            page.wait_for_timeout(2000)

            result["ok"] = True
            result["note"] = "Reserva feta"
            return result

        except PWTimeout as e:
            result["error"] = f"Timeout: {e}"
            return result
        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}"
            return result
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--program", required=True, help="Nom del programa, ex: Hyrox")
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--time", required=True, help="HH:MM")
    parser.add_argument("--dry-run", action="store_true", help="No clica el botó final")
    parser.add_argument("--headless", action="store_true", help="Sense interfície gràfica")
    args = parser.parse_args()

    res = reserve(args.program, args.date, args.time,
                  dry_run=args.dry_run, headless=args.headless)
    print(res)
    sys.exit(0 if res["ok"] else 1)
