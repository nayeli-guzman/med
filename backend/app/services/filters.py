# app/services/filters.py
from typing import Dict, List, Tuple

def filter_bundle_by_subject(bundle: Dict, ok_subjects: set[str]) -> Tuple[Dict, Dict[str, int]]:
    """
    Devuelve (bundle_filtrado, metrics).
    metrics: {'total':N, 'kept':K, 'wrong_subject':x, 'cancelled':y, 'missing_subject':z}
    Mantiene otros recursos no-Observation/MedicationRequest (p.ej. Medication incluídos).
    """
    if not bundle or bundle.get("resourceType") != "Bundle":
        return {"resourceType":"Bundle","type":"searchset","total":0,"entry":[]}, {"total":0,"kept":0,"wrong_subject":0,"cancelled":0,"missing_subject":0}

    entries = bundle.get("entry") or []
    kept, out, m = 0, [], {"total":0,"kept":0,"wrong_subject":0,"cancelled":0,"missing_subject":0}

    for e in entries:
        r = e.get("resource") or {}
        rt = r.get("resourceType")
        if rt not in ("Observation","MedicationRequest"):
            # deja passthrough (e.g., Medication incluidos) sin contar como “total”
            out.append(e)
            continue

        m["total"] += 1
        # subject/ref
        ref = ((r.get("subject") or {}).get("reference")) or ""
        if not ref:
            m["missing_subject"] += 1
            continue
        if ref not in ok_subjects:
            m["wrong_subject"] += 1
            continue
        # status
        st = (r.get("status") or "").lower()
        if st == "cancelled":
            m["cancelled"] += 1
            continue

        out.append(e)
        kept += 1

    m["kept"] = kept
    return {**bundle, "entry": out, "total": kept}, m

def merge_quality(q: Dict[str, Dict[str,int]]) -> Dict[str,int]:
    """Suma métricas por tipo de recurso."""
    out = {"total":0,"kept":0,"wrong_subject":0,"cancelled":0,"missing_subject":0}
    for k in q.values():
        for t in out: out[t] += k.get(t,0)
    return out
