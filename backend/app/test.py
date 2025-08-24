# from clients.hl7_client import parse_hl7

"""
raw = "MSH|^~\\&|LIS|HOSP|EMR|HOSP|202501011230||ORU^R01|1|P|2.3\r" \
      "PID|1||12345^^^HOSP^MR||DOE^JOHN||19800101|M\r" \
      "OBR|1||ABC|718-7^Hemoglobin^LN\r" \
      "OBX|1|NM|718-7^Hemoglobin^LN||12.3|g/dL|13-17|L|||F|202501011230\r"
print(parse_hl7(raw))
"""

import asyncio, json, sys
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
