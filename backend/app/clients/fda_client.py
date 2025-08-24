import asyncio, httpx, unicodedata
from app.core import config

def norm(s:str)->str:
    return unicodedata.normalize("NFKD", s).encode("ascii","ignore").decode().strip().lower()

async def query_openfda(drug:str):
    base = config.FDA_BASE; q = norm(drug)
    async with httpx.AsyncClient(timeout=15) as c:
        for path in (f"/drug/interactions.json?search={q}",
                     f"/drug/label.json?search={q}"):
            try:
                r = await c.get(base + path)
                if r.status_code >= 500:
                    await asyncio.sleep(0.3); continue
                r.raise_for_status()
                return {"endpoint": path, "payload": r.json()}
            except httpx.HTTPError:
                continue
    return {"endpoint": None, "payload": None}
