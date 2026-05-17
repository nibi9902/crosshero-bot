"""Llista de classes a vigilar (polling cada N segons buscant cancel·lacions)."""
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from . import config


def _path() -> Path:
    return config.DATA_DIR / "watch.json"


def _now_iso() -> str:
    return datetime.now(ZoneInfo(config.TIMEZONE)).isoformat()


def load() -> list[dict]:
    p = _path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("items", []) if isinstance(data, dict) else []
    except Exception:
        return []


def save(items: list[dict]) -> None:
    _path().write_text(
        json.dumps({"items": items}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _compute_until(class_dt: datetime) -> datetime:
    return class_dt - timedelta(hours=config.WATCH_DEADLINE_HOURS)


def add(class_id: str, program: str, the_date: str, the_time: str,
        class_dt: datetime, auto: bool = False) -> Optional[dict]:
    """
    Afegeix una classe a la llista de vigilància.
    Idempotent per class_id: si ja existeix, no es duplica.
    Retorna l'item afegit (o None si ja existia).
    """
    items = load()
    for it in items:
        if it.get("class_id") == class_id:
            return None
    until = _compute_until(class_dt)
    item = {
        "class_id": class_id,
        "program": program,
        "date": the_date,
        "time": the_time,
        "label": f"{program} {the_date} {the_time}",
        "added_at": _now_iso(),
        "until": until.isoformat(),
        "auto": auto,
    }
    items.append(item)
    save(items)
    return item


def remove(class_id: str) -> bool:
    items = load()
    new_items = [it for it in items if it.get("class_id") != class_id]
    if len(new_items) == len(items):
        return False
    save(new_items)
    return True
