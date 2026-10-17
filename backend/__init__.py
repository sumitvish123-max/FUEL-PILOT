"""FuelPilot backend package. Loads KEY=VALUE pairs from a .env file before anything else."""
import os
from pathlib import Path
def _load_env_files() -> None:
    here = Path(__file__).resolve().parent
    for path in (here.parent / ".env", here / ".env"):
        if not path.is_file():
            continue
        raw = path.read_bytes()
        text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8-sig", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
_load_env_files()