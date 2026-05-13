"""
API FastAPI del bot de reserves Crosshero.

Endpoints:
  GET  /health
  GET  /classes?program=Hyrox&date=YYYY-MM-DD
  POST /schedule  → programa una o més reserves
  GET  /pending   → llista reserves programades
  DELETE /pending/{job_id}
  GET  /programs  → llista programes coneguts
"""
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path  # noqa
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Header, Depends, Request, Cookie, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel, Field

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.triggers.date import DateTrigger

from .crosshero import CrossheroClient, PROGRAMS
from . import config


scheduler = BackgroundScheduler(
    jobstores={"default": SQLAlchemyJobStore(url=f"sqlite:///{config.JOBS_DB}")},
    timezone=config.TIMEZONE,
)


import json as _json_mod

HISTORY_FILE = config.DATA_DIR / "history.jsonl"


def _append_history(entry: dict):
    try:
        with HISTORY_FILE.open("a", encoding="utf-8") as f:
            f.write(_json_mod.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception as e:
        print(f"[history] error: {e}", flush=True)


def reserve_job(class_id: str, label: str):
    """Funció que executa APScheduler a l'hora exacta."""
    started_at = datetime.now(ZoneInfo(config.TIMEZONE)).isoformat()
    client = CrossheroClient(config.STORAGE_STATE)
    try:
        result = client.reserve_class(class_id)
        print(f"[reserve_job] {label} → {result}", flush=True)
        _append_history({
            "ts": started_at,
            "label": label,
            "class_id": class_id,
            "result": result,
        })
        return result
    except Exception as e:
        err = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        print(f"[reserve_job] {label} → EXCEPTION {err}", flush=True)
        _append_history({
            "ts": started_at,
            "label": label,
            "class_id": class_id,
            "result": err,
        })
        return err
    finally:
        client.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Crosshero Bot", lifespan=lifespan)


AUTH_COOKIE = "crosshero_auth"


def require_api_key(
    x_api_key: Optional[str] = Header(default=None),
    crosshero_auth: Optional[str] = Cookie(default=None),
):
    if not config.API_KEY:
        return
    if x_api_key == config.API_KEY or crosshero_auth == config.API_KEY:
        return
    raise HTTPException(401, "Invalid API key")


@app.post("/auth")
def auth(response: Response, x_api_key: Optional[str] = Header(default=None)):
    """Estableix la cookie d'autenticació si l'API key és correcta."""
    if not config.API_KEY:
        return {"ok": True, "note": "no API_KEY configurada"}
    if x_api_key != config.API_KEY:
        raise HTTPException(401, "Invalid API key")
    response.set_cookie(
        key=AUTH_COOKIE,
        value=config.API_KEY,
        max_age=60 * 60 * 24 * 365,  # 1 any
        secure=True,
        httponly=False,  # JS pot llegir-la per detectar estat (no és secret pel client)
        samesite="lax",
        path="/",
    )
    return {"ok": True}


@app.post("/logout")
def logout(response: Response):
    response.delete_cookie(AUTH_COOKIE, path="/")
    return {"ok": True}


# ---------- Schemas ----------

class ScheduleItem(BaseModel):
    program: str = Field(..., examples=["Hyrox"])
    class_date: date = Field(..., alias="date", description="Data de la classe (YYYY-MM-DD)")
    class_time: str = Field(..., alias="time", examples=["19:00"], description="Hora HH:MM")

    model_config = {"populate_by_name": True}


class ScheduleRequest(BaseModel):
    items: list[ScheduleItem]


class ScheduledJobOut(BaseModel):
    id: str
    program: str
    class_date: str
    class_time: str
    fires_at: str
    next_run: Optional[str] = None


# ---------- Endpoints ----------

UI_HTML = (Path(__file__).parent / "ui.html").read_text(encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
def root():
    return UI_HTML


@app.get("/ui", response_class=HTMLResponse)
def ui():
    return UI_HTML


@app.get("/health")
def health():
    has_session = config.STORAGE_STATE.exists()
    return {
        "ok": True,
        "scheduler_running": scheduler.running,
        "has_session": has_session,
    }


@app.post("/session")
async def upload_session(request: Request, _: None = Depends(require_api_key)):
    """
    Puja un nou storage_state.json al volum (per inicialitzar o refrescar la sessió).
    Body: JSON tal qual de Playwright storage_state.
    """
    import json as _json
    body = await request.body()
    try:
        data = _json.loads(body)
        if not isinstance(data, dict) or "cookies" not in data:
            raise ValueError("Falta 'cookies' al JSON")
    except Exception as e:
        raise HTTPException(400, f"JSON invàlid: {e}")
    config.STORAGE_STATE.write_bytes(body)
    return {"ok": True, "bytes": len(body), "cookies": len(data.get("cookies", []))}


@app.get("/programs")
def list_programs():
    return {"programs": list(PROGRAMS.keys())}


DAYS_CA = ["Dl", "Dm", "Dc", "Dj", "Dv", "Ds", "Dg"]


@app.get("/upcoming")
def list_upcoming(
    program: str = "Hyrox",
    days: int = 10,
    skip_booked: bool = True,
    _: None = Depends(require_api_key),
):
    """
    Retorna totes les classes d'un programa dels propers `days` dies.
    Cada classe inclou label llegible per l'Atajo iPhone (selector múltiple).

    Si skip_booked=True (per defecte), omet les que ja tens reservades o programades.
    """
    tz = ZoneInfo(config.TIMEZONE)
    today = datetime.now(tz).date()

    # IDs ja a la cua del scheduler
    scheduled_ids = set()
    for j in scheduler.get_jobs():
        if j.args and len(j.args) >= 1:
            scheduled_ids.add(j.args[0])

    client = CrossheroClient(config.STORAGE_STATE)
    all_items = []
    try:
        for offset in range(days):
            d = today + timedelta(days=offset)
            res = client.list_classes(program, d)
            if not res.get("ok"):
                continue
            for c in res["classes"]:
                # label estil "Dl 18/05 07:00"
                weekday = DAYS_CA[d.weekday()]
                label = f"{weekday} {d.strftime('%d/%m')} {c['hora']}"
                item = {
                    "label": label,
                    "program": program,
                    "date": d.isoformat(),
                    "time": c["hora"],
                    "class_id": c["id"],
                    "already_scheduled": c["id"] in scheduled_ids,
                }
                if skip_booked and item["already_scheduled"]:
                    continue
                all_items.append(item)
    finally:
        client.close()

    return {"ok": True, "program": program, "count": len(all_items), "items": all_items}


@app.get("/classes")
def list_classes(program: str, date: str, _: None = Depends(require_api_key)):
    """date format: YYYY-MM-DD"""
    try:
        d = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, "Format data invàlid (YYYY-MM-DD)")
    client = CrossheroClient(config.STORAGE_STATE)
    try:
        return client.list_classes(program, d)
    finally:
        client.close()


@app.post("/schedule")
def schedule(req: ScheduleRequest, _: None = Depends(require_api_key)):
    """
    Per cada item:
      - resol class_id consultant Crosshero pel dia/hora
      - calcula moment d'obertura: classe - RESERVATION_LEAD_DAYS, mateixa hora
      - programa job al scheduler que s'executarà aleshores
    """
    tz = ZoneInfo(config.TIMEZONE)
    client = CrossheroClient(config.STORAGE_STATE)
    scheduled, errors = [], []

    try:
        for item in req.items:
            # 1) trobar la classe
            lst = client.list_classes(item.program, item.class_date)
            if not lst.get("ok"):
                errors.append({"item": item.model_dump(mode="json"), "error": lst.get("error")})
                continue

            match = next((c for c in lst["classes"] if c["hora"] == item.class_time), None)
            if not match:
                errors.append({
                    "item": item.model_dump(mode="json"),
                    "error": f"No hi ha classe {item.program} a les {item.class_time} el {item.class_date}",
                    "available": [c["hora"] for c in lst["classes"]],
                })
                continue

            class_id = match["id"]

            # 2) calcular moment d'obertura
            h, m = map(int, item.class_time.split(":"))
            class_dt = datetime.combine(item.class_date, datetime.min.time()).replace(
                hour=h, minute=m, tzinfo=tz
            )
            fire_at = class_dt - timedelta(days=config.RESERVATION_LEAD_DAYS) \
                              - timedelta(seconds=config.FIRE_OFFSET_SECONDS)

            now = datetime.now(tz)
            if fire_at <= now:
                # Reserva ja oberta — la disparem immediatament
                fire_at = now + timedelta(seconds=2)

            # 3) programar job
            job_id = f"{item.program}-{item.class_date}-{item.class_time}-{class_id[:6]}"
            label = f"{item.program} {item.class_date} {item.class_time}"

            job = scheduler.add_job(
                reserve_job,
                trigger=DateTrigger(run_date=fire_at),
                args=[class_id, label],
                id=job_id,
                replace_existing=True,
                misfire_grace_time=300,
            )

            scheduled.append({
                "job_id": job.id,
                "program": item.program,
                "class_date": str(item.class_date),
                "class_time": item.class_time,
                "class_id": class_id,
                "fires_at": fire_at.isoformat(),
            })
    finally:
        client.close()

    return {"scheduled": scheduled, "errors": errors}


@app.get("/history")
def history(limit: int = 50, _: None = Depends(require_api_key)):
    """Retorna les últimes execucions del scheduler (èxits i errors)."""
    if not HISTORY_FILE.exists():
        return {"entries": []}
    lines = HISTORY_FILE.read_text(encoding="utf-8").strip().split("\n")
    entries = []
    for line in lines[-limit:]:
        try:
            entries.append(_json_mod.loads(line))
        except Exception:
            pass
    entries.reverse()  # més recent primer
    return {"entries": entries, "total": len(lines)}


@app.get("/pending")
def pending(_: None = Depends(require_api_key)):
    out = []
    for j in scheduler.get_jobs():
        parts = j.id.split("-")
        out.append({
            "id": j.id,
            "next_run": j.next_run_time.isoformat() if j.next_run_time else None,
            "args": j.args,
        })
    return {"jobs": out}


@app.delete("/pending/{job_id}")
def delete_pending(job_id: str, _: None = Depends(require_api_key)):
    try:
        scheduler.remove_job(job_id)
        return {"ok": True, "removed": job_id}
    except Exception as e:
        raise HTTPException(404, f"Job no trobat: {e}")
