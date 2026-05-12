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

# Marge de seguretat: disparem aquest nombre de segons abans de l'hora d'obertura
# (per estar primer a la cua; el servidor de Crosshero pot tenir lleugera deriva)
FIRE_OFFSET_SECONDS = int(os.getenv("FIRE_OFFSET_SECONDS", "2"))

# Auth simple per l'API: header X-Api-Key
API_KEY = os.getenv("API_KEY", "")

TIMEZONE = os.getenv("TZ", "Europe/Madrid")
