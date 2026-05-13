"""
Client HTTP per Crosshero (sense Playwright).

Reutilitza les cookies de storage_state.json (capturat amb Playwright un sol cop)
i fa peticions HTTP directes contra els formularis de Rails.
"""
import json
import re
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

    def my_reservations(self) -> dict:
        """
        Llegeix les reserves confirmades del usuari a Crosshero
        (extretes de /dashboard, que llista totes les properes).
        """
        from urllib.parse import unquote
        r = self.client.get("/dashboard")
        if r.status_code != 200 or "sign_in" in str(r.url):
            return {"ok": False, "error": f"HTTP {r.status_code} o sessió caducada"}

        soup = BeautifulSoup(r.text, "html.parser")
        # Programa ID → nom (invers)
        prog_by_id = {v: k for k, v in PROGRAMS.items()}

        out = []
        seen = set()
        # Iterem sobre cada link de cancel·lació (= reserva activa)
        for cancel in soup.find_all("a", href=re.compile(r"/class_reservations/[^/]+/confirm_destroy")):
            # Pugem per pares fins trobar un link a /dashboard/classes?date=...
            container = None
            for parent in cancel.parents:
                cl = parent.find("a", href=re.compile(r"/dashboard/classes\?"))
                if cl:
                    container = parent
                    container_class_link = cl
                    break
            else:
                continue

            href = container_class_link.get("href", "")
            m_date = re.search(r"date=([^&]+)", href)
            m_id = re.search(r"id=([^&]+)", href)
            m_prog = re.search(r"program_id=([^&]+)", href)
            if not (m_date and m_id):
                continue

            class_id = m_id.group(1)
            if class_id in seen:
                continue
            seen.add(class_id)

            date_str = unquote(m_date.group(1))  # DD/MM/YYYY
            program_id = m_prog.group(1) if m_prog else None
            program_name = prog_by_id.get(program_id)

            text = container.get_text(" ", strip=True)
            m_time = re.search(r"\b(\d{1,2}:\d{2})\b", text)
            hora = m_time.group(1) if m_time else None

            # Converteix DD/MM/YYYY → YYYY-MM-DD
            try:
                d_iso = datetime.strptime(date_str, "%d/%m/%Y").date().isoformat()
            except Exception:
                d_iso = None

            out.append({
                "date": d_iso,
                "date_raw": date_str,
                "time": hora,
                "program": program_name,
                "program_id": program_id,
                "class_id": class_id,
                "cancel_url": cancel.get("href"),
            })

        # Ordena per data ascendent
        out.sort(key=lambda x: (x["date"] or "", x["time"] or ""))
        return {"ok": True, "count": len(out), "reservations": out}

    def reserve_class(self, class_id: str, csrf_token: Optional[str] = None) -> dict:
        """
        Reserva una classe pel seu ID. Si no es passa csrf_token, en demana un nou.
        """
        if not csrf_token:
            # Necessitem un token fresc — anem al dashboard
            r = self.client.get("/dashboard")
            soup = BeautifulSoup(r.text, "html.parser")
            token = soup.select_one("meta[name='csrf-token']") or soup.select_one("input[name='authenticity_token']")
            if not token:
                return {"ok": False, "error": "No s'ha pogut obtenir CSRF token"}
            csrf_token = token.get("content") or token.get("value")

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
        }

        # Buscar missatges flash a la resposta
        soup = BeautifulSoup(r.text, "html.parser")
        flash = soup.select(".alert, .flash, .notification")
        if flash:
            result["messages"] = [f.get_text(strip=True)[:200] for f in flash][:3]

        # Detectar errors comuns al HTML
        if "no se ha podido" in r.text.lower() or "error" in r.text.lower()[:5000]:
            # Comprovació superficial, no concloent
            pass

        return result


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
