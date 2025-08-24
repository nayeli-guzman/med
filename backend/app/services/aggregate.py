# backend/app/services/aggregate.py
from __future__ import annotations
from typing import Any, Dict, List

# -------- Helpers básicos --------
def _unique(seq: List[str]) -> List[str]:
    seen = set()
    out = []
    for s in seq:
        if s and s not in seen:
            out.append(s); seen.add(s)
    return out

def _get(d: Dict, path: List[str], default=None):
    cur = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur

# -------- Extractores FHIR --------
# app/services/aggregate.py
def extract_med_names(bundle: dict | None) -> list[str]:
    if not bundle:
        return []
    meds, included = [], {}
    # indexa Medication incluidos (si hubiera)
    for e in bundle.get("entry", []):
        r = e.get("resource") or {}
        if r.get("resourceType") == "Medication":
            included[r.get("id")] = r

    for e in bundle.get("entry", []):
        r = e.get("resource") or {}
        rt = r.get("resourceType")
        if rt not in ("MedicationRequest", "MedicationStatement"):
            continue

        name = None
        # CodeableConcept
        mcc = (r.get("medicationCodeableConcept") or {})
        name = mcc.get("text")
        if not name:
            cods = mcc.get("coding") or []
            if cods:
                name = cods[0].get("display") or cods[0].get("code")

        # Reference
        if not name:
            ref = (r.get("medicationReference") or {}).get("reference")
            if ref and ref.startswith("Medication/"):
                med = included.get(ref.split("/",1)[1], {})
                code = med.get("code") or {}
                name = code.get("text")
                if not name:
                    cods = code.get("coding") or []
                    if cods:
                        name = cods[0].get("display") or cods[0].get("code")

        if name:
            name = name.strip()
            if name and name.lower() not in [m.lower() for m in meds]:
                meds.append(name)
    return meds


def _fhir_observations(obs_bundle: Dict | None) -> List[Dict[str, Any]]:
    """Normaliza Observations FHIR a una lista sencilla."""
    out: List[Dict[str, Any]] = []
    if not obs_bundle:
        return out
    for e in obs_bundle.get("entry", []):
        o = e.get("resource", {}) or {}
        code = None
        text = None
        coding = _get(o, ["code", "coding"], []) or []
        if coding:
            code = coding[0].get("code")
            text = coding[0].get("display")
        if not text:
            text = _get(o, ["code", "text"])
        vq = o.get("valueQuantity") or {}
        out.append({
            "code": code,
            "name": text,
            "value": vq.get("value"),
            "unit": vq.get("unit"),
            "effective_dt": o.get("effectiveDateTime") or o.get("issued"),
            "flag": _get(o, ["interpretation", "coding"], [{}])[0].get("code"),
            "source": "FHIR",
        })
    return out

# -------- Resumen de paciente --------
def min_patient(patient: Dict) -> Dict[str, Any]:
    """Resumen mínimo del paciente para el payload final."""
    pid = patient.get("id")
    birth = patient.get("birthDate")
    name = None
    try:
        n = (patient.get("name") or [])[0]
        name = n.get("text")
        if not name:
            given = (n.get("given") or [""])[0]
            family = n.get("family") or ""
            name = f"{given} {family}".strip() or None
    except Exception:
        pass
    gender = patient.get("gender")
    return {"id": pid, "name": name, "birthDate": birth, "gender": gender}

# -------- Resumen estructurado para la respuesta --------
def summary(patient: Dict, meds_bundle: Dict | None, obs_bundle: Dict | None, hl7_obs: List[Dict] | None) -> Dict[str, Any]:
    meds = extract_med_names(meds_bundle)
    labs = _fhir_observations(obs_bundle)
    if hl7_obs:
        labs.extend(hl7_obs)
    # Si quieres solo “anormales” necesitarías lógica; para demo limitamos a 10
    return {"medications": meds, "abnormal_labs": labs[:10]}

# -------- OpenFDA → interacciones --------
def distill_interactions(fda_fragments: List[Dict] | None) -> List[Dict[str, Any]]:
    """
    Intenta extraer algo legible de la carga de la API FDA.
    Como el esquema puede variar, devolvemos un resumen seguro.
    """
    out: List[Dict[str, Any]] = []
    for frag in (fda_fragments or []):
        drug = frag.get("drug")
        endpoint = frag.get("endpoint")
        payload = frag.get("payload") or {}
        item = {"drug": drug, "source": endpoint, "evidence": []}
        # Heurísticas comunes
        for key in ("interactions", "warnings", "contraindications", "results"):
            if key in payload and payload[key]:
                # Agarra hasta 2 fragmentos/strings cortos
                val = payload[key]
                if isinstance(val, list):
                    sample = val[:2]
                elif isinstance(val, dict):
                    sample = list(val.items())[:2]
                else:
                    sample = [str(val)[:300]]
                item["evidence"].append({key: sample})
        out.append(item)
    return out

def citations(fda_fragments: List[Dict] | None) -> List[Dict[str, str]]:
    cites: List[Dict[str, str]] = []
    for frag in (fda_fragments or []):
        ep = frag.get("endpoint")
        if ep:
            cites.append({"source": "OpenFDA", "endpoint": ep})
    return cites

# -------- Contexto para IA (RAG) --------
def build_patient_context(
    patient: Dict,
    meds_bundle: Dict | None,
    obs_bundle: Dict | None,
    hl7_obs: List[Dict] | None,
    fda_fragments: List[Dict] | None,
    rag_hits: List[Dict] | None,
) -> Dict[str, Any]:
    ctx = {
        "patient": min_patient(patient),
        "medications": extract_med_names(meds_bundle),
        "labs": (_fhir_observations(obs_bundle) + (hl7_obs or []))[:20],
        "fda_evidence": [],
        "rag_sources": rag_hits or [],
    }
    # Reducimos la evidencia de FDA a trozos pequeños para el prompt
    for f in (fda_fragments or []):
        piece = {"drug": f.get("drug"), "endpoint": f.get("endpoint")}
        payload = f.get("payload") or {}
        for key in ("interactions", "warnings", "contraindications"):
            if key in payload:
                txt = payload[key]
                if isinstance(txt, str):
                    piece[key] = txt[:500]
                else:
                    piece[key] = str(txt)[:500]
        ctx["fda_evidence"].append(piece)
    return ctx
