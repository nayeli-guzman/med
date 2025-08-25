"""from clients.hl7_client import parse_hl7

raw = "MSH|^~\\&|LIS|HOSP|EMR|HOSP|202501011230||ORU^R01|1|P|2.3\r" \
      "PID|1||12345^^^HOSP^MR||DOE^JOHN||19800101|M\r" \
      "OBR|1||ABC|718-7^Hemoglobin^LN\r" \
      "OBX|1|NM|718-7^Hemoglobin^LN||12.3|g/dL|13-17|L|||F|202501011230\r"
print(parse_hl7(raw))"""

"""import asyncio, json, sys
from clients import ai_client
from core import config

def jprint(title, obj):
    print(f"\n=== {title} ===")
    try:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    except Exception:
        print(obj)

async def main():
    print(f"AI_BASE = {config.AI_BASE}")

    # 1) Probar knowledge_search
    query = "oncology adherence and drug interactions; antiemetic guidelines"
    try:
        hits = await ai_client.knowledge_search(query, k=3)
        jprint("knowledge_search hits", hits)
    except Exception as e:
        print(f"[knowledge_search] ERROR: {e.__class__.__name__}: {e}")

    # 2) Probar analyze con un contexto mínimo y seguro
    context = {
        "patient": {"id": "demo-1", "gender": "female", "birthDate": "1970-01-01"},
        "medications": ["warfarina", "aspirina"],
        "labs": [
            {"name": "Hemoglobina", "value": 10.8, "unit": "g/dL"},
            {"name": "Leucocitos", "value": 12.1, "unit": "10*3/uL"},
        ],
        "fda_evidence": [{"drug": "warfarina", "warnings": "riesgo de sangrado", "source": "OpenFDA"}],
        "rag_sources": [{"title": "ASCO antiemetic guideline", "url": "https://example.org/asco"}],
    }
    try:
        ai = await ai_client.analyze(context, task="adherence_and_interactions")
        jprint("analyze result", ai)
    except Exception as e:
        print(f"[analyze] ERROR: {e.__class__.__name__}: {e}")

if __name__ == "__main__":
    asyncio.run(main())
"""

#!/usr/bin/env python3
import os, json, asyncio, time
import redis.asyncio as redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
STREAM_RAW  = os.getenv("HL7_RAW_STREAM", "hl7:raw")
STREAM_NORM = os.getenv("HL7_NORM_STREAM", "hl7:norm")
STREAM_DLQ  = os.getenv("HL7_DLQ_STREAM", "hl7:dlq")

# Tu mensaje HL7 de prueba (usa \r como separador de segmentos)
HL7_MSG = "\r".join([
    "MSH|^~\\&|...|...|...|...|202501151030||ORU^R01|MSG00001|P|2.5",
    "PID|||12345^^^HOSP||DOE^JOHN||19900101|M",
    "OBX|1|NM|12345-6^Troponin I||0.09|ng/L|0.00-0.05|H|||F|||202501151025",
]) + "\r"

async def main():
    r = redis.from_url(REDIS_URL, decode_responses=True)

    # 1) Guarda offsets para leer sólo lo nuevo
    #    (Tomamos el último ID de cada stream)
    last_norm_id = "$"
    last_dlq_id  = "$"

    # 2) Inyecta en hl7:raw con el campo correcto ("message")
    msg_id = await r.xadd(STREAM_RAW, {"message": HL7_MSG})
    print(f"[test] Enviado a {STREAM_RAW}: msg_id={msg_id}")

    # 3) Espera a que el normalizer procese y publique en hl7:norm
    #    Poll suave por hasta 5 segundos
    deadline = time.time() + 5.0
    normalized_events = []
    dlq_events = []

    while time.time() < deadline and not normalized_events:
        # lee lo nuevo desde el último id
        norm = await r.xread({STREAM_NORM: last_norm_id}, count=10, block=500)
        if norm:
            # norm = [ (stream, [(id, fields), ...]) ]
            for _stream, entries in norm:
                for mid, fields in entries:
                    last_norm_id = mid
                    e = fields.get("e")
                    if e:
                        try:
                            normalized_events.append(json.loads(e))
                        except Exception:
                            print(f"[test] Evento e inválido en {STREAM_NORM}: {fields}")
        # si no llegó a norm, revisa si cayó en DLQ
        dlq = await r.xread({STREAM_DLQ: last_dlq_id}, count=10, block=100)
        if dlq:
            for _stream, entries in dlq:
                for mid, fields in entries:
                    last_dlq_id = mid
                    dlq_events.append((mid, fields))

    # 4) Reporte
    if normalized_events:
        print(f"\n✅ Normalizado(s) en {STREAM_NORM}: {len(normalized_events)}")
        for i, ev in enumerate(normalized_events, 1):
            print(f"--- evento {i} ---")
            print(json.dumps(ev, indent=2, ensure_ascii=False))
    else:
        print("\n❌ No se encontró evento en hl7:norm dentro del tiempo de espera.")

    if dlq_events:
        print(f"\n⚠️ DLQ ({STREAM_DLQ}) capturó {len(dlq_events)} entrad(a)s:")
        for mid, f in dlq_events[:3]:
            print(f"- {mid}: reason={f.get('reason')} err={f.get('err')}")
            # opcional: mostrar parte del mensaje crudo
            raw = f.get("m", "")
            if raw and len(raw) > 120:
                raw = raw[:120] + "..."
            if raw:
                print(f"  m={raw}")

if __name__ == "__main__":
    asyncio.run(main())
