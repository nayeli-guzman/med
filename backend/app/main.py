# app/main.py
from fastapi import FastAPI, HTTPException, Query
from fastapi import HTTPException
import httpx
import asyncio
import re
from app.core import config

from app.clients import fhir_client, hl7_client, fda_client, ai_client
from app.services import aggregate
from app.services.filters import filter_bundle_by_subject, merge_quality

app = FastAPI(title="Oncology Intelligence")

@app.get("/health")
def health():
    return {"status": "ok"}

def _as_hits_list(h):
    if not h:
        return []
    if isinstance(h, list):
        return h
    if isinstance(h, dict):
        for k in ("results", "hits", "items", "data"):
            v = h.get(k)
            if isinstance(v, list):
                return v
    return []

def _filter_hits(hits, min_score=0.40):
    allow = ("ASCO","NCCN","ESMO","NIH","NCI","WHO","PUBMED","UPTODATE")
    out = []
    for h in hits or []:
        if not isinstance(h, dict):
            continue
        src = (h.get("source") or "").upper()
        score = h.get("relevance_score") or h.get("score") or 0
        if score >= min_score or any(s in src for s in allow):
            out.append(h)
    return out[:5]

def _norm(s: str | None) -> str:
    # quita todo lo no alfanumérico y pasa a minúscula
    return re.sub(r"[^A-Za-z0-9]", "", (s or "")).lower()

def _pid3_ids(pid_text: str) -> set[str]:
    """
    PID-3 puede venir con repeticiones separadas por '~' y componentes por '^'.
    Tomamos el componente 1 (el identificador) de cada repetición.
    Ej: 'P788166^^^MR~12345^^^SSN' -> {'p788166','12345'}
    """
    out = set()
    for rep in (pid_text or "").split("~"):
        comp = rep.split("^", 1)[0].strip()
        if comp:
            out.add(_norm(comp))
    return out

@app.get("/patients/{patient_id}/insights")
async def insights(
    patient_id: str,
    strict: bool = True,
    max_fda: int = 3,
    max_labs: int = 10,
    demo_meds: str | None = Query(None, description="CSV de medicamentos para demo si FHIR no trae MR"),
):
    """
    Endpoint estrella:
    - Resuelve paciente por búsqueda (search-only) y valida id.
    - Trae MedicationRequest/Observation y FILTRA estrictamente por subject.reference.
    - Ingiere HL7, filtra por PID-3 contra id/identifiers.
    - Consulta OpenFDA y Clinical AI (RAG + analyze) con tolerancia a fallas.
    - Devuelve status ok/partial, citas y métricas de data quality.
    """
    unavailable: list[str] = []
    quality: dict = {}

    # 1) Token FHIR
    try:
        token = await fhir_client.get_token()
    except Exception as e:
        raise HTTPException(504, f"FHIR token failed: {e}")

    # 2) Paciente (search-only) + validación
    try:
        patient = await fhir_client.fetch_patient(patient_id, token)
    except Exception:
        raise HTTPException(404, f"Patient '{patient_id}' not found via search")
    real_id = patient.get("id")
    if strict and real_id != patient_id:
        raise HTTPException(404, f"Patient '{patient_id}' not found (mismatch: '{real_id}')")

    print(patient)
    ok_subjects = {f"Patient/{real_id}"} # siempre es el paciente-0
    mrns_ok = {i.get("value") for i in (patient.get("identifier") or []) if i.get("value")} # si hay MRN
    print("MRNs", mrns_ok)

    # 3) FHIR meds/obs en paralelo (y luego filtrar por subject/reference)
    async def _safe_fetch(fn, label):
        try:
            return await fn(real_id, token), None
        except Exception as e:
            return {"resourceType":"Bundle","type":"searchset","total":0,"entry":[]}, e
        
    print("Fetching FHIR meds/obs...")
    (meds_raw, meds_err), (obs_raw, obs_err) = await asyncio.gather(
        _safe_fetch(fhir_client.fetch_medications, "MedicationRequest"),
        _safe_fetch(fhir_client.fetch_observations, "Observation"),
    )
    print(obs_raw)

    if meds_err:
        unavailable.append("FHIR:MedicationRequest")
    if obs_err:
        unavailable.append("FHIR:Observation")

    meds_bundle, q_meds = filter_bundle_by_subject(meds_raw, ok_subjects)
    obs_bundle,  q_obs  = filter_bundle_by_subject(obs_raw, ok_subjects)
    quality["MedicationRequest"] = q_meds
    quality["Observation"] = q_obs

    print(quality)

    # 4) HL7 (best-effort) + filtro por PID-3 (id o MRN)
    hl7_obs = []
    hl7_quality = {"messages_total": 0, "parsed": 0, "matched": 0, "obx_kept": 0}
    max_messages = 100     # <- límite de mensajes a revisar
    max_hl7_obx = 12       # <- cuántas OBX como máximo quieres agregar

    try:
        msgs = await hl7_client.get_hl7_messages(limit=max_messages)
        seen_ids = set()
        ok_ids = {_norm(patient_id)} | {_norm(m) for m in (mrns_ok or []) if m}

        for m in (msgs or []):
            if len(hl7_obs) >= max_hl7_obx:
                break  # ya tenemos suficiente info para la demo

            mid = m.get("id")
            if mid in seen_ids:
                continue
            seen_ids.add(mid)

            hl7_quality["messages_total"] += 1
            raw = m.get("message") or m.get("raw_message") or m.get("raw") or ""
            if not raw:
                continue

            try:
                parsed = hl7_client.parse_hl7(raw)  # tu parser tolerante
                hl7_quality["parsed"] += 1
            except Exception:
                continue

            pid_text = parsed.get("patient_identifier") or ""
            pid_ids = _pid3_ids(pid_text)

            # match estricto por ids normalizados (evita falsos positivos de substring)
            if ok_ids & pid_ids:
                hl7_quality["matched"] += 1
                obs = parsed.get("observations") or []
                # opcional: filtra valores no numéricos para evitar ruido en insights
                obs = [o for o in obs if isinstance(o.get("value"), (int, float, float.__class__))]
                # corta si ya alcanzas el tope
                keep = max(0, max_hl7_obx - len(hl7_obs))
                hl7_obs.extend(obs[:keep])
                hl7_quality["obx_kept"] += min(len(obs), keep)

    except Exception:
        unavailable.append("HL7")

    # (opcional) incluye métricas en tu data_quality:
    quality["HL7"] = hl7_quality

    # 5) OpenFDA (cache en el cliente) — a partir de meds
    citations: list[dict] = []
    med_names = aggregate.extract_med_names(meds_bundle)[:max_fda]
    if not med_names and demo_meds:
        med_names = [m.strip() for m in demo_meds.split(",") if m.strip()]
        citations.append({"source":"DemoOverride","title":"medications"})
    fda_frags = []
    if med_names:
        for d in med_names:
            try:
                f = await fda_client.query_openfda(d)
                fda_frags.append({"drug": d, **f})
            except Exception:
                pass
        if not fda_frags:
            unavailable.append("FDA")

    # 6) RAG + Analyze (best-effort, contexto compacto)
    #     query concisa con meds + 2 labs
    labs_for_q = ", ".join(
        f"{x.get('name') or x.get('code')}={x.get('value')}{x.get('unit') or ''}"
        for x in (aggregate._fhir_observations(obs_bundle)[:2] if obs_bundle else [])
    )
    q = f"oncology adherence and drug interactions; meds: {', '.join(med_names)}; labs: {labs_for_q}".strip("; ")

    rag_hits = []
    try:
        ks = await ai_client.knowledge_search(q, k=5)
        rag_hits = _filter_hits(_as_hits_list(ks))
    except Exception:
        unavailable.append("AI:knowledge-search")

    try:
        context = aggregate.build_patient_context(
            patient=patient,
            meds_bundle=meds_bundle,
            obs_bundle=obs_bundle,
            hl7_obs=hl7_obs,
            fda_fragments=fda_frags,
            rag_hits=rag_hits
        )
        ai = await ai_client.analyze(context, task="adherence_and_interactions")
    except Exception as e:
        ai = {"status":"degraded", "reason": f"AI failed: {e.__class__.__name__}"}
        unavailable.append("AI:analyze")

    # 7) Ensamble (summary + citas + data_quality + status)
    ss = aggregate.summary(patient, meds_bundle, obs_bundle, hl7_obs)
    ss["abnormal_labs"] = ss.get("abnormal_labs", [])[:max_labs]

    # Citas FDA
    citations.extend(aggregate.citations(fda_frags))
    # Citas KnowledgeSearch
    for h in rag_hits:
        if isinstance(h, dict):
            citations.append({
                "source": "KnowledgeSearch",
                "title": h.get("title") or h.get("name") or "doc",
                "url": h.get("url") or h.get("link") or ""
            })

    data_quality = {
        "by_resource": quality,
        "overall": merge_quality(quality),
        "notes": [
            "Strict subject filtering applied to FHIR bundles",
            "Cancelled entries dropped",
            "HL7 matched by PID-3 against patient.id/identifiers"
        ]
    }

    status = "ok" if not unavailable and data_quality["overall"]["wrong_subject"] == 0 else "partial"

    return {
        "status": status,
        "unavailable_sources": unavailable,
        "patient": aggregate.min_patient(patient),
        "structured_summary": ss,
        "drug_interactions": aggregate.distill_interactions(fda_frags),
        "ai_insights": ai,
        "citations": citations,
        "data_quality": data_quality,
    }

@app.get("/patients")
async def patients(count: int = 5):
    try:
        token = await fhir_client.get_token()
    except Exception as e:
        raise HTTPException(504, f"FHIR token failed: {e}")

    try:
        bundle = await fhir_client.list_patients(count, token)
        return bundle
    except httpx.HTTPStatusError as e:
        # Si venía OperationOutcome ya lo formateamos en el mensaje de excepción
        raise HTTPException(e.response.status_code, f"{e}")
    except Exception as e:
        raise HTTPException(502, f"FHIR list failed: {e}")