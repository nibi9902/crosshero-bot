import os
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
if not DATA_DIR.exists():
    # fallback local
    DATA_DIR = Path(__file__).parent.parent / "data"
    DATA_DIR.mkdir(exist_ok=True)

STORAGE_STATE = DATA_DIR / "storage_state.json"
JOBS_DB = DATA_DIR / "jobs.sqlite"

# Hores abans que obre la reserva (3 dies al box del usuari)
RESERVATION_LEAD_DAYS = int(os.getenv("RESERVATION_LEAD_DAYS", "3"))

# Retard: disparem aquest nombre de segons DESPRÉS de l'hora d'obertura.
# Crosshero rebutja peticions arribades abans del segon exacte d'obertura
# ("Las reservas para esta clase empiezan 3 días antes de empezar la clase"),
# així que cal disparar com a mínim 1s després per cobrir el jitter d'APScheduler.
FIRE_DELAY_SECONDS = int(os.getenv("FIRE_DELAY_SECONDS", "1"))

# Vigilància de classes plenes (polling buscant cancel·lacions)
WATCH_POLL_SECONDS = int(os.getenv("WATCH_POLL_SECONDS", "60"))
# Deixem de vigilar aquest nombre d'hores abans de la classe (per no fer soroll
# inútil quan ja és clar que ningú es traurà a última hora).
WATCH_DEADLINE_HOURS = int(os.getenv("WATCH_DEADLINE_HOURS", "9"))

# Auth simple per l'API: header X-Api-Key
API_KEY = os.getenv("API_KEY", "")

TIMEZONE = os.getenv("TZ", "Europe/Madrid")
