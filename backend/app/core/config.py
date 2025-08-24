# app/core/config.py
import os
from pathlib import Path
from dotenv import load_dotenv

# Intenta cargar .env en backend/, si no existe, busca en la raíz del repo
_here = Path(__file__).resolve()
backend_dir = _here.parents[2]      # .../backend
repo_root   = _here.parents[3]      # .../ (ajusta si tu estructura difiere)

# Prioriza .env en backend; si no, usa el de la raíz
candidates = [backend_dir / ".env", repo_root / ".env"]
for p in candidates:
    if p.exists():
        load_dotenv(p)
        break

def env(name: str, default: str | None = None) -> str:
    v = os.getenv(name, default)
    if v is None:
        raise RuntimeError(f"Missing required env var: {name}")
    return v

FHIR_BASE = env("FHIR_BASE")
HL7_BASE  = env("HL7_BASE")
FDA_BASE  = env("FDA_BASE")
AI_BASE   = env("AI_BASE")
FHIR_CLIENT_ID     = env("FHIR_CLIENT_ID")
FHIR_CLIENT_SECRET = env("FHIR_CLIENT_SECRET")
FHIR_TOKEN_URL  = os.getenv("FHIR_TOKEN_URL")  # opcional
