"""Preferències d'auto-reserva persistents (JSON al volum)."""
import json
from pathlib import Path
from typing import Optional

from . import config


DEFAULT_PREFS = {
    "auto_schedule": True,
    "program": "Hyrox",
    "horizon_days": 14,
    "slots": [
        {"weekday": 0, "time": "07:00"},  # Dilluns
        {"weekday": 2, "time": "07:00"},  # Dimecres
        {"weekday": 5, "time": "07:00"},  # Dissabte
    ],
}


def _path() -> Path:
    return config.DATA_DIR / "preferences.json"


def load() -> dict:
    p = _path()
    if not p.exists():
        save(DEFAULT_PREFS)
        return DEFAULT_PREFS.copy()
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return DEFAULT_PREFS.copy()


def save(prefs: dict) -> dict:
    # Normalitzar i validar mínimament
    norm = {
        "auto_schedule": bool(prefs.get("auto_schedule", True)),
        "program": str(prefs.get("program", "Hyrox")),
        "horizon_days": int(prefs.get("horizon_days", 14)),
        "slots": [],
    }
    for s in prefs.get("slots", []):
        try:
            wd = int(s["weekday"])
            t = str(s["time"])
            if not (0 <= wd <= 6):
                continue
            if not (len(t) == 5 and t[2] == ":"):
                continue
            norm["slots"].append({"weekday": wd, "time": t})
        except Exception:
            continue
    # Dedup slots
    seen = set()
    deduped = []
    for s in norm["slots"]:
        key = (s["weekday"], s["time"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(s)
    norm["slots"] = sorted(deduped, key=lambda x: (x["weekday"], x["time"]))

    _path().write_text(json.dumps(norm, indent=2, ensure_ascii=False), encoding="utf-8")
    return norm
