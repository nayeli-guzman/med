# app/clients/ai_client.py
import httpx
from app.core import config


def _coerce_ai_insights(j):
    # aceptamos dict/str/list y normalizamos a un payload estable
    if isinstance(j, dict):
        base = {"status": "ok"}
        # mapea claves comunes o devuelve todo en 'raw' si es raro
        keys = {"key_findings", "next_best_actions", "patient_friendly_advice", "risk_score"}
        if any(k in j for k in keys):
            base.update({k: j.get(k) for k in keys if k in j})
            return base
        return {"status": "ok", "raw": j}
    if isinstance(j, str):
        return {"status": "ok", "summary": j[:1200]}
    if isinstance(j, list):
        return {"status":"ok", "bullets": j[:10]}
    return {"status":"ok"}

async def knowledge_search(query:str, k:int=3):
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{config.AI_BASE}/ai/knowledge-search",
                         json={"query": query, "max_results": k})
        r.raise_for_status()
        j = r.json()
        # normaliza a lista
        if isinstance(j, list): return j
        if isinstance(j, dict):
            for key in ("results","hits","items","data"):
                v = j.get(key)
                if isinstance(v, list): return v
        return []

async def analyze(context:dict, task:str):
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{config.AI_BASE}/ai/analyze",
                         json={"task": task, "context": context})
        r.raise_for_status()
        return _coerce_ai_insights(r.json())
