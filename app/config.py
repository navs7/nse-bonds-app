import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

UPSTOX_ACCESS_TOKEN = os.environ.get("UPSTOX_ACCESS_TOKEN", "")
QUOTE_STALE_AFTER_SECONDS = int(os.environ.get("QUOTE_STALE_AFTER_SECONDS", "300"))
MIN_YTM = float(os.environ.get("MIN_YTM", "12"))
MIN_DAYS_LEFT = int(os.environ.get("MIN_DAYS_LEFT", "90"))
MIN_GROSS_PROFIT = float(os.environ.get("MIN_GROSS_PROFIT", "10000"))
REF_MAX_AGE_DAYS = int(os.environ.get("REF_MAX_AGE_DAYS", "30"))
BONDS_CSV = ROOT / os.environ.get("BONDS_CSV", "data/bonds.csv")
