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
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .crosshero import CrossheroClient, PROGRAMS
from . import config, preferences, watch


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


def _result_messages_text(result: dict) -> str:
    return " ".join(result.get("messages", [])).lower()


def _is_full_class(result: dict) -> bool:
    text = _result_messages_text(result)
    return "límite" in text and "alcanz" in text


def _is_success(result: dict) -> bool:
    text = _result_messages_text(result)
    return "reservada" in text and ("éxito" in text or "exito" in text)


def _is_too_late(result: dict) -> bool:
    text = _result_messages_text(result)
    return "cierran" in text and "antes de empezar" in text


def _parse_label(label: str) -> Optional[tuple[str, str, str]]:
    """label = 'Programa YYYY-MM-DD HH:MM' → (program, date, time). El programa
    pot contenir espais."""
    parts = label.rsplit(" ", 2)
    if len(parts) != 3:
        return None
    program, the_date, the_time = parts
    if len(the_date) != 10 or len(the_time) != 5:
        return None
    return program, the_date, the_time


def _already_reserved(client: CrossheroClient, class_id: str) -> bool:
    """Comprova si la classe ja és a la llista de reserves confirmades."""
    try:
        res = client.my_reservations()
        if not res.get("ok"):
            return False
        return any(r.get("class_id") == class_id for r in res.get("reservations", []))
    except Exception:
        return False


def _auto_watch_if_full(
    client: CrossheroClient, class_id: str, label: str, result: dict
) -> None:
    """Si la reserva ha fallat per estar plena (i no és nostra), afegeix la
    classe a la cua de vigilància perquè el polling busqui cancel·lacions."""
    if not _is_full_class(result):
        return
    if _already_reserved(client, class_id):
        print(f"[auto-watch] saltat {label}: ja la teníem reservada", flush=True)
        return
    parsed = _parse_label(label)
    if not parsed:
        return
    program, the_date, the_time = parsed
    try:
        h, m = map(int, the_time.split(":"))
        class_dt = datetime.combine(
            datetime.strptime(the_date, "%Y-%m-%d").date(),
            datetime.min.time(),
        ).replace(hour=h, minute=m, tzinfo=ZoneInfo(config.TIMEZONE))
    except Exception:
        return
    added = watch.add(class_id, program, the_date, the_time, class_dt, auto=True)
    if added:
        print(f"[auto-watch] afegit {label} (until {added['until']})", flush=True)


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
        _auto_watch_if_full(client, class_id, label, result)
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


def watch_polling_job():
    """
    Cada N segons: revisa la llista de classes a vigilar i intenta reservar-les.
    - Si ho aconsegueix → la treu de la llista.
    - Si "cierran 1 minuto antes" → massa tard, la treu.
    - Si "alcanzó el límite" → continua plena, la deixa per al pròxim cicle.
    - Si passa el deadline (until) → la treu per silenci.
    """
    items = watch.load()
    if not items:
        return

    tz = ZoneInfo(config.TIMEZONE)
    now = datetime.now(tz)
    remaining: list[dict] = []
    client: Optional[CrossheroClient] = None
    already_reserved_ids: Optional[set[str]] = None

    try:
        for it in items:
            try:
                until = datetime.fromisoformat(it["until"])
            except Exception:
                until = now  # malformat → eliminem

            if now >= until:
                _append_history({
                    "ts": now.isoformat(),
                    "label": "WATCH_EXPIRED",
                    "watch_label": it.get("label"),
                    "class_id": it.get("class_id"),
                })
                continue

            if client is None:
                client = CrossheroClient(config.STORAGE_STATE)

            # Una sola crida my_reservations() per cicle: si la classe ja és
            # nostra (ara o per reserva manual fora del bot), la traiem.
            if already_reserved_ids is None:
                try:
                    res = client.my_reservations()
                    already_reserved_ids = {
                        r.get("class_id") for r in res.get("reservations", [])
                        if r.get("class_id")
                    } if res.get("ok") else set()
                except Exception:
                    already_reserved_ids = set()

            if it["class_id"] in already_reserved_ids:
                _append_history({
                    "ts": now.isoformat(),
                    "label": "WATCH_ALREADY_RESERVED",
                    "watch_label": it.get("label"),
                    "class_id": it.get("class_id"),
                })
                continue

            try:
                result = client.reserve_class(it["class_id"])
            except Exception as e:
                _append_history({
                    "ts": now.isoformat(),
                    "label": "WATCH_ERROR",
                    "watch_label": it.get("label"),
                    "class_id": it.get("class_id"),
                    "error": f"{type(e).__name__}: {e}",
                })
                remaining.append(it)
                continue

            if _is_success(result):
                _append_history({
                    "ts": now.isoformat(),
                    "label": "WATCH_HIT",
                    "watch_label": it.get("label"),
                    "class_id": it.get("class_id"),
                    "result": result,
                })
                # No la mantenim — ja és nostra
            elif _is_too_late(result):
                _append_history({
                    "ts": now.isoformat(),
                    "label": "WATCH_TOO_LATE",
                    "watch_label": it.get("label"),
                    "class_id": it.get("class_id"),
                })
            elif _is_full_class(result):
                # Continua plena — mantenir per al pròxim cicle
                remaining.append(it)
            else:
                # Cas ambigu (incloent "empiezan N días antes" o respostes no
                # categoritzades). Per seguretat, log + mantenir.
                _append_history({
                    "ts": now.isoformat(),
                    "label": "WATCH_UNKNOWN",
                    "watch_label": it.get("label"),
                    "class_id": it.get("class_id"),
                    "result": result,
                })
                remaining.append(it)
    finally:
        if client:
            client.close()

    watch.save(remaining)


def _schedule_one(client: CrossheroClient, program: str, the_date: date, the_time: str) -> dict:
    """Localitza la classe i la programa al scheduler. Retorna info."""
    tz = ZoneInfo(config.TIMEZONE)
    lst = client.list_classes(program, the_date)
    if not lst.get("ok"):
        return {"ok": False, "reason": lst.get("error", "unknown")}
    match = next((c for c in lst["classes"] if c["hora"] == the_time), None)
    if not match:
        return {"ok": False, "reason": f"no hi ha classe {program} {the_time} el {the_date}"}

    class_id = match["id"]
    h, m = map(int, the_time.split(":"))
    class_dt = datetime.combine(the_date, datetime.min.time()).replace(
        hour=h, minute=m, tzinfo=tz
    )
    fire_at = class_dt - timedelta(days=config.RESERVATION_LEAD_DAYS) \
                      + timedelta(seconds=config.FIRE_DELAY_SECONDS)
    now = datetime.now(tz)
    if fire_at <= now:
        fire_at = now + timedelta(seconds=2)

    job_id = f"{program}-{the_date}-{the_time}-{class_id[:6]}"
    label = f"{program} {the_date} {the_time}"

    # Skip if ja està programat
    existing = scheduler.get_job(job_id)
    if existing:
        return {"ok": True, "skipped": True, "job_id": job_id}

    scheduler.add_job(
        reserve_job,
        trigger=DateTrigger(run_date=fire_at),
        args=[class_id, label],
        id=job_id,
        replace_existing=True,
        misfire_grace_time=300,
    )
    return {
        "ok": True,
        "job_id": job_id,
        "class_id": class_id,
        "fires_at": fire_at.isoformat(),
    }


def auto_schedule_job():
    """
    Cron diari: mira les preferències i programa les classes properes
    que encara no estiguin a la cua.
    """
    prefs = preferences.load()
    if not prefs.get("auto_schedule"):
        return

    tz = ZoneInfo(config.TIMEZONE)
    today = datetime.now(tz).date()
    horizon = int(prefs.get("horizon_days", 14))
    program = prefs.get("program", "Hyrox")

    actions = []
    client = CrossheroClient(config.STORAGE_STATE)
    try:
        for offset in range(horizon):
            d = today + timedelta(days=offset)
            wd = d.weekday()  # 0=Dl
            for slot in prefs.get("slots", []):
                if slot["weekday"] != wd:
                    continue
                res = _schedule_one(client, program, d, slot["time"])
                actions.append({"date": str(d), "time": slot["time"], **res})
    finally:
        client.close()

    print(f"[auto_schedule] actions: {actions}", flush=True)
    _append_history({
        "ts": datetime.now(tz).isoformat(),
        "label": "AUTO_SCHEDULE",
        "actions": actions,
    })


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start()
    # Cron diari a les 03:00 que assegura que totes les classes
    # que coincideixen amb les preferències estan programades
    scheduler.add_job(
        auto_schedule_job,
        trigger=CronTrigger(hour=3, minute=0, timezone=config.TIMEZONE),
        id="__auto_schedule_daily",
        replace_existing=True,
    )
    # Polling de classes a vigilar (busca cancel·lacions a classes plenes).
    # No fa cap crida HTTP si la llista watch.json és buida.
    scheduler.add_job(
        watch_polling_job,
        trigger=IntervalTrigger(
            seconds=config.WATCH_POLL_SECONDS,
            jitter=config.WATCH_POLL_JITTER,
        ),
        id="__watch_polling",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    # I executar-lo també a l'arrencada (per omplir la cua immediatament)
    try:
        auto_schedule_job()
    except Exception as e:
        print(f"[startup auto_schedule] {e}", flush=True)
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


@app.get("/reservations")
def my_reservations(_: None = Depends(require_api_key)):
    """Llista les reserves actualment confirmades a Crosshero."""
    client = CrossheroClient(config.STORAGE_STATE)
    try:
        return client.my_reservations()
    finally:
        client.close()


@app.get("/preferences")
def get_preferences(_: None = Depends(require_api_key)):
    return preferences.load()


@app.put("/preferences")
def put_preferences(prefs: dict, _: None = Depends(require_api_key)):
    saved = preferences.save(prefs)
    # Auto-aplica les noves preferències immediatament
    if saved.get("auto_schedule"):
        try:
            auto_schedule_job()
        except Exception as e:
            print(f"[put_preferences auto] {e}", flush=True)
    return saved


@app.post("/run-auto-schedule")
def run_auto_schedule(_: None = Depends(require_api_key)):
    """Força una execució del cron d'auto-reserva ara mateix."""
    auto_schedule_job()
    return {"ok": True}


class WatchAddRequest(BaseModel):
    program: str = Field(..., examples=["Hyrox"])
    class_date: date = Field(..., alias="date")
    class_time: str = Field(..., alias="time", examples=["07:00"])

    model_config = {"populate_by_name": True}


@app.get("/watch")
def list_watch(_: None = Depends(require_api_key)):
    """Llista classes actualment a la cua de vigilància."""
    return {
        "items": watch.load(),
        "poll_seconds": config.WATCH_POLL_SECONDS,
        "deadline_hours": config.WATCH_DEADLINE_HOURS,
    }


@app.post("/watch")
def add_watch(req: WatchAddRequest, _: None = Depends(require_api_key)):
    """
    Afegeix una classe a la cua de vigilància. Resol class_id consultant
    Crosshero pel dia/hora indicats.
    """
    tz = ZoneInfo(config.TIMEZONE)
    client = CrossheroClient(config.STORAGE_STATE)
    try:
        lst = client.list_classes(req.program, req.class_date)
        if not lst.get("ok"):
            raise HTTPException(400, f"No s'han pogut llistar classes: {lst.get('error')}")
        match = next((c for c in lst["classes"] if c["hora"] == req.class_time), None)
        if not match:
            raise HTTPException(
                404,
                f"No hi ha classe {req.program} a les {req.class_time} el {req.class_date}",
            )
    finally:
        client.close()

    h, m = map(int, req.class_time.split(":"))
    class_dt = datetime.combine(req.class_date, datetime.min.time()).replace(
        hour=h, minute=m, tzinfo=tz,
    )
    if class_dt <= datetime.now(tz):
        raise HTTPException(400, "La classe ja ha passat")

    item = watch.add(
        match["id"],
        req.program,
        str(req.class_date),
        req.class_time,
        class_dt,
        auto=False,
    )
    if item is None:
        return {"ok": True, "already_watching": True, "class_id": match["id"]}
    return {"ok": True, "item": item}


@app.delete("/watch/{class_id}")
def delete_watch(class_id: str, _: None = Depends(require_api_key)):
    if watch.remove(class_id):
        return {"ok": True, "removed": class_id}
    raise HTTPException(404, "No estava a la llista de vigilància")


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
                              + timedelta(seconds=config.FIRE_DELAY_SECONDS)

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
        # Saltar jobs interns del sistema (prefix __)
        if j.id.startswith("__"):
            continue
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
