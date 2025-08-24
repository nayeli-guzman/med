# app/clients/fhir_client.py
import time, asyncio, httpx
from app.core import config

TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)
CANDIDATE_TOKEN_PATHS = ("/oauth/token", "/token", "/auth/token", "/oauth2/token")

_token: str | None = None
_token_exp_epoch: float = 0.0


def _headers(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}", "Accept": "application/fhir+json"}

def _is_search_path(path: str) -> bool:
    # considera search si el path termina en el nombre del recurso (no /{id})
    return path.endswith("/fhir/Patient") or path.endswith("/fhir/Observation") or path.endswith("/fhir/MedicationRequest")

def _empty_bundle():
    return {"resourceType":"Bundle", "type":"searchset", "total":0, "entry":[]}
# -------------------------------------

async def get_token(force_refresh: bool = False) -> str:
    global _token, _token_exp_epoch
    if not force_refresh and _token and time.time() < (_token_exp_epoch - 60):
        return _token

    # warm-up (best-effort)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5, 5, 5, 5)) as c:
            await c.get(f"{config.FHIR_BASE}/health")
    except Exception:
        pass

    form = {
        "grant_type": "client_credentials",
        "client_id": config.FHIR_CLIENT_ID,
        "client_secret": config.FHIR_CLIENT_SECRET,
    }
    paths = [config.FHIR_TOKEN_URL] if getattr(config, "FHIR_TOKEN_URL", None) else CANDIDATE_TOKEN_PATHS

    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        for p in paths:
            if not p:
                continue
            url = p if str(p).startswith("http") else f"{config.FHIR_BASE}{p}"
            delay = 0.4
            for _ in range(3):
                try:
                    r = await c.post(url, data=form, headers={"Content-Type":"application/x-www-form-urlencoded"})
                    r.raise_for_status()
                    j = r.json()
                    token = j.get("access_token") or j.get("accessToken")
                    if not token:
                        raise RuntimeError(f"token endpoint sin access_token: {j}")
                    _token = token
                    _token_exp_epoch = time.time() + int(j.get("expires_in", 1800))
                    return _token
                except (httpx.ReadTimeout, httpx.ConnectTimeout):
                    await asyncio.sleep(delay); delay *= 2; continue
                except httpx.HTTPStatusError as e:
                    if 500 <= e.response.status_code < 600:
                        await asyncio.sleep(delay); delay *= 2; continue
                    if e.response.status_code == 404:
                        break
                    raise
    raise RuntimeError("no se pudo obtener token FHIR")

async def _fhir_get(path: str, token: str, params: dict | None = None):
    if params is None: params = {}
    params.setdefault("_format", "json")
    url = f"{config.FHIR_BASE}{path}"

    delay = 0.4
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        for attempt in range(2):  # 1 intento + 1 retry si hubo 401
            r = await c.get(url, headers=_headers(token), params=params)
            if r.status_code == 401 and attempt == 0:
                token = await get_token(force_refresh=True)
                await asyncio.sleep(0)  # yield
                continue  # reintenta con token nuevo
            # Manejo de OperationOutcome
            if r.status_code >= 400:
                try:
                    body = r.json()
                    if body.get("resourceType") == "OperationOutcome":
                        issues = body.get("issue", [])
                        diag = "; ".join(f"{i.get('code')}: {i.get('diagnostics')}" for i in issues if i)
                        # Si el server falla (5xx) y es una búsqueda, degradamos a bundle vacío
                        if r.status_code >= 500 and _is_search_path(path):
                            return _empty_bundle()
                        # para otros casos, levantamos error con detalle legible
                        raise httpx.HTTPStatusError(f"FHIR {r.status_code} OperationOutcome: {diag}", request=r.request, response=r)
                except ValueError:
                    # respuesta no-JSON, sigue el manejo estándar
                    pass
            r.raise_for_status()
            return r.json()

    # si llegamos aquí fue 401 dos veces, o algo raro
    raise httpx.HTTPStatusError("FHIR unauthorized after token refresh", request=None, response=None)

# --------- funciones de alto nivel recomendadas ---------
async def list_patients(count: int, token: str):
    return await _fhir_get("/fhir/Patient", token, params={"_count": count})

async def fetch_patient(patient_id: str, token: str):
    # 1) intento de lectura directa
    try:
        return await _fhir_get(f"/fhir/Patient/{patient_id}", token)
    except httpx.HTTPStatusError as e:
        # 2) fallback por _id
        if getattr(e, "response", None) and e.response.status_code == 404:
            bundle = await _fhir_get("/fhir/Patient", token, params={"_id": patient_id})
            entries = bundle.get("entry") or []
            if entries:
                return entries[0].get("resource", {})
        # 3) si fue otro error, re-lanza
        raise

async def fetch_medications(patient_id: str, token: str):
    """
    Intenta varias variantes de búsqueda y filtra por subject.reference.
    Si no hay MR, intenta MedicationStatement como fallback.
    """
    want = f"Patient/{patient_id}"
    tries = [
        ("/fhir/MedicationRequest", {"subject": want, "_include":"MedicationRequest:medication", "_count":50}),
        ("/fhir/MedicationRequest", {"patient": patient_id, "_include":"MedicationRequest:medication", "_count":50}),
        ("/fhir/MedicationRequest", {"subject": patient_id, "_include":"MedicationRequest:medication", "_count":50}),
    ]
    print("Trying MedicationRequest paths...")
    # 1) MedicationRequest
    for path, params in tries:
        try:
            b = await _fhir_get(path, token, params=params)
            print("Medicamentos")
            print(b)
        except httpx.HTTPStatusError as e:
            if getattr(e, "response", None) and e.response.status_code in (400,404,409,422,429,500,502,503):
                continue
            raise
        # filtra por subject
        entries = []
        for e in (b.get("entry") or []):
            r = e.get("resource") or {}
            if r.get("resourceType") != "MedicationRequest":
                entries.append(e); continue
            if (r.get("subject") or {}).get("reference") == want:
                entries.append(e)
        if any((e.get("resource") or {}).get("resourceType") == "MedicationRequest" for e in entries):
            return {**b, "entry": entries}
    print("No MedicationRequest found, trying MedicationStatement...")
   
    # 2) Fallback: MedicationStatement
    try:
        b = await _fhir_get("/fhir/MedicationStatement", token,
                            params={"subject": want, "_count":50})
        print("Medicamentos")
        print(b)
        # filtra por subject
        entries = []
        for e in (b.get("entry") or []):
            r = e.get("resource") or {}
            if r.get("resourceType") != "MedicationStatement":
                continue
            if (r.get("subject") or {}).get("reference") == want:
                entries.append(e)
        if entries:
            # envolvemos como si fuera MR para el extractor
            return {"resourceType":"Bundle","type":"searchset","entry":entries}
    except Exception:
        pass

    return {"resourceType":"Bundle","type":"searchset","total":0,"entry":[]}

async def fetch_observations(patient_id: str, token: str,
                             max_items: int = 200, page_limit: int = 5) -> dict:
    """
    Busca Observation por patient/subject, sigue paginación y
    FILTRA client-side para quedarnos estrictamente con las del paciente.
    Devuelve un Bundle con solo las entradas válidas.
    """
    want = f"Patient/{patient_id}"
    url = f"{config.FHIR_BASE}/fhir/Observation"
    params = {"subject": want, "_count": 100, "_format": "json"}

    kept_entries: list[dict] = []
    pages = 0
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        while url and pages < page_limit and len(kept_entries) < max_items:
            r = await c.get(url, headers=_headers(token), params=params)
            # reintento simple si el token expiró
            if r.status_code == 401:
                from .fhir_client import get_token
                token = await get_token(force_refresh=True)  # type: ignore
                r = await c.get(url, headers=_headers(token), params=params)

            # si el server devuelve OperationOutcome, degrada a vacío
            if r.status_code >= 400:
                try:
                    body = r.json()
                    if body.get("resourceType") == "OperationOutcome":
                        break  # devolvemos lo que tengamos (quizá nada)
                except Exception:
                    pass
                r.raise_for_status()

            b = r.json() or {}
            for e in (b.get("entry") or []):
                res = e.get("resource") or {}
                if res.get("resourceType") != "Observation":
                    continue
                ref = ((res.get("subject") or {}).get("reference")) or ""
                if ref != want:
                    continue  # <<< evita mezclar pacientes
                if (res.get("status") or "").lower() == "cancelled":
                    continue
                kept_entries.append(e)
                if len(kept_entries) >= max_items:
                    break

            # siguiente página (si existe)
            next_url = None
            for link in (b.get("link") or []):
                if (link.get("relation") or link.get("rel")) == "next":
                    next_url = link.get("url")
                    break
            url = next_url
            params = None  # cuando seguimos link absoluto, no volver a pasar params
            pages += 1

    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": len(kept_entries),
        "entry": kept_entries,
    }