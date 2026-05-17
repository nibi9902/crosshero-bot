"""
Client HTTP per Crosshero (sense Playwright).

Reutilitza les cookies de storage_state.json (capturat amb Playwright un sol cop)
i fa peticions HTTP directes contra els formularis de Rails.
"""
import json
import re
import time
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import httpx
from bs4 import BeautifulSoup


BASE_URL = "https://crosshero.com"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

# Catàleg de programes (es pot extreure dinàmicament; per ara hardcoded del descobriment)
PROGRAMS = {
    "CrossFit": "66b35ee3b468ff00308e782f",
    "ESQUENA SANA": "6957c898c5f12dadf8803b79",
    "Full body": "66c5897063564f00359d56a3",
    "Hyrox": "66c5895463564f00309d611b",
    "Ioga": "6979ffe7fe0ac0628bac3e80",
    "SKILLS (GYM/HALTERO)": "66c589a963564f00309d6175",
    "Strength": "66c5898ef4e3520036013866",
}


def _load_cookies(storage_state_path: Path) -> dict:
    """Extreu cookies del fitxer storage_state.json de Playwright."""
    data = json.loads(storage_state_path.read_text())
    cookies = {}
    for c in data.get("cookies", []):
        if "crosshero.com" in c.get("domain", ""):
            cookies[c["name"]] = c["value"]
    return cookies


class CrossheroClient:
    def __init__(self, storage_state_path: Path):
        self.storage_state_path = storage_state_path
        cookies = _load_cookies(storage_state_path)
        self.client = httpx.Client(
            base_url=BASE_URL,
            headers={"User-Agent": UA, "Accept-Language": "es-ES,es;q=0.9"},
            cookies=cookies,
            follow_redirects=True,
            timeout=20.0,
        )

    def close(self):
        self.client.close()

    def list_classes(self, program: str, target_date: date) -> dict:
        """
        Retorna les classes disponibles d'un programa per un dia concret.
        target_date: datetime.date
        """
        if program not in PROGRAMS:
            return {"ok": False, "error": f"Programa desconegut: {program}. Disponibles: {list(PROGRAMS.keys())}"}

        program_id = PROGRAMS[program]
        date_str = target_date.strftime("%d/%m/%Y")
        r = self.client.get("/dashboard/classes", params={
            "date": date_str,
            "program_id": program_id,
        })
        if r.status_code != 200:
            return {"ok": False, "error": f"HTTP {r.status_code}"}

        if "sign_in" in str(r.url):
            return {"ok": False, "error": "Sessió caducada"}

        soup = BeautifulSoup(r.text, "html.parser")
        token = soup.select_one("input[name='authenticity_token']")
        token_val = token["value"] if token else None

        select = soup.select_one("select[name='class_reservation[single_class_id]']")
        classes = []
        if select:
            for opt in select.select("option"):
                val = opt.get("value", "").strip()
                txt = opt.get_text(strip=True)
                if val:
                    classes.append({"id": val, "hora": txt})

        return {
            "ok": True,
            "program": program,
            "program_id": program_id,
            "date": date_str,
            "classes": classes,
            "csrf_token": token_val,
        }

    def my_reservations(self, only_future: bool = True, max_pages: int = 5) -> dict:
        """
        Llegeix les reserves ACTIVES de l'usuari paginant /dashboard/reservations.

        Considera "activa" una fila que té link "Cancelar" (confirm_destroy).
        Les que només tenen "Ver clase" són històriques (atendides o cancel·lades).
        """
        today = date.today()
        prog_by_id = {v: k for k, v in PROGRAMS.items()}
        all_items = []
        seen_keys = set()

        for page in range(1, max_pages + 1):
            params = {"page": page} if page > 1 else {}
            r = self.client.get("/dashboard/reservations", params=params)
            if r.status_code != 200 or "sign_in" in str(r.url):
                if page == 1:
                    return {"ok": False, "error": f"HTTP {r.status_code} o sessió caducada"}
                break

            soup = BeautifulSoup(r.text, "html.parser")
            rows_on_page = 0

            for tr in soup.select("tbody tr"):
                rows_on_page += 1
                # Indicador d'activa: té link de cancel·lació
                cancel_link = None
                for a in tr.select(".dropdown-menu a, .table-buttons a"):
                    href = a.get("href", "")
                    text = a.get_text(strip=True).lower()
                    if "confirm_destroy" in href or "cancelar" in text:
                        cancel_link = a
                        break
                if not cancel_link:
                    continue  # Històrica/cancel·lada — saltem

                # Programa
                prog_td = tr.select_one(".reservation-program")
                program = prog_td.get_text(" ", strip=True) if prog_td else None

                # Data + hora
                date_td = tr.select_one(".reservation-date")
                date_text = date_td.get_text(" ", strip=True) if date_td else ""
                m_dt = re.search(r"(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2})", date_text)
                if not m_dt:
                    continue
                date_s = m_dt.group(1)
                hora = m_dt.group(2)

                # Marca "Cancelada" al text → no és activa (per seguretat)
                if "cancelada" in date_text.lower():
                    continue

                try:
                    d = datetime.strptime(date_s, "%d/%m/%Y").date()
                except Exception:
                    continue

                if only_future and d < today:
                    continue

                key = (date_s, hora)
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                # Class_id i program_id (del link "Ver clase")
                class_id = None
                program_id = None
                for a in tr.select(".dropdown-menu a"):
                    href = a.get("href", "")
                    if "/dashboard/classes" in href:
                        m_id = re.search(r"[?&]id=([^&]+)", href)
                        m_prog = re.search(r"program_id=([^&]+)", href)
                        if m_id:
                            class_id = m_id.group(1)
                        if m_prog:
                            program_id = m_prog.group(1)
                            if not program:
                                program = prog_by_id.get(program_id)
                        break

                all_items.append({
                    "date": d.isoformat(),
                    "date_raw": date_s,
                    "time": hora,
                    "program": program,
                    "program_id": program_id,
                    "class_id": class_id,
                    "cancel_url": cancel_link.get("href"),
                    "is_today": d == today,
                    "is_past": d < today,
                })

            if rows_on_page == 0:
                break

        all_items.sort(key=lambda x: (x["date"], x["time"]))
        return {"ok": True, "count": len(all_items), "reservations": all_items}

    def reserve_class(
        self,
        class_id: str,
        csrf_token: Optional[str] = None,
        max_attempts: int = 4,
        retry_delay: float = 0.5,
    ) -> dict:
        """
        Reserva una classe pel seu ID. Si no es passa csrf_token, en demana un nou.

        Reintenta automàticament si Crosshero respon que la finestra de reserva
        encara no s'ha obert ("empiezan N días antes"), per cobrir el cas que
        el dispar arribi un instant abans del segon exacte d'obertura.
        """
        if not csrf_token:
            r = self.client.get("/dashboard")
            soup = BeautifulSoup(r.text, "html.parser")
            token = soup.select_one("meta[name='csrf-token']") or soup.select_one("input[name='authenticity_token']")
            if not token:
                return {"ok": False, "error": "No s'ha pogut obtenir CSRF token"}
            csrf_token = token.get("content") or token.get("value")

        last_result: dict = {}
        for attempt in range(1, max_attempts + 1):
            r = self.client.post(
                "/dashboard/class_reservations",
                data={
                    "authenticity_token": csrf_token,
                    "redirect_to": "",
                    "fullscreen": "",
                    "class_reservation[single_class_id]": class_id,
                },
                headers={"Referer": f"{BASE_URL}/dashboard/classes"},
            )

            success = r.status_code in (200, 302) and "sign_in" not in str(r.url)
            result = {
                "ok": success,
                "status": r.status_code,
                "final_url": str(r.url),
                "class_id": class_id,
                "attempts": attempt,
            }

            soup = BeautifulSoup(r.text, "html.parser")
            flash = soup.select(".alert, .flash, .notification")
            if flash:
                result["messages"] = [f.get_text(strip=True)[:200] for f in flash][:3]

            messages = result.get("messages", [])
            too_early = any(
                "empiezan" in m.lower() and "antes" in m.lower()
                for m in messages
            )

            last_result = result
            if not too_early or attempt == max_attempts:
                return result
            time.sleep(retry_delay)

        return last_result


if __name__ == "__main__":
    import argparse, sys
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["list", "reserve", "programs"])
    parser.add_argument("--program", default="Hyrox")
    parser.add_argument("--date", help="YYYY-MM-DD")
    parser.add_argument("--class-id", help="ID de classe per reservar")
    parser.add_argument("--storage", default="storage_state.json")
    args = parser.parse_args()

    client = CrossheroClient(Path(args.storage))
    try:
        if args.action == "programs":
            print(json.dumps(PROGRAMS, indent=2, ensure_ascii=False))
        elif args.action == "list":
            d = datetime.strptime(args.date, "%Y-%m-%d").date()
            res = client.list_classes(args.program, d)
            print(json.dumps(res, indent=2, ensure_ascii=False))
        elif args.action == "reserve":
            res = client.reserve_class(args.class_id)
            print(json.dumps(res, indent=2, ensure_ascii=False))
    finally:
        client.close()
