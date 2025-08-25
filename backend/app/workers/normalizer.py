# app/workers/normalizer.py
import asyncio, json, os, logging, time
from datetime import datetime, timezone
from typing import List, Dict, Any

from app.clients.redis_client import get_redis
from app.clients import hl7_client
from app.models.event_common import EventCommon  # contrato del evento

STREAM_RAW  = os.getenv("HL7_RAW_STREAM", "hl7:raw")
STREAM_NORM = os.getenv("HL7_NORM_STREAM", "hl7:norm")
STREAM_DLQ  = os.getenv("HL7_DLQ_STREAM", "hl7:dlq")
GROUP       = os.getenv("HL7_GROUP", "normgrp")
CONSUMER    = os.getenv("CONSUMER", "norm-1")
COUNT       = int(os.getenv("HL7_NORMALIZE_COUNT", "256"))
BLOCK_MS    = int(os.getenv("HL7_NORMALIZE_BLOCK_MS", "1000"))
MAXLEN_NORM = int(os.getenv("HL7_NORM_MAXLEN", "100000"))
MAXLEN_DLQ  = int(os.getenv("HL7_DLQ_MAXLEN", "50000"))

logging.basicConfig(level=os.getenv("LOGLEVEL","INFO"))
log = logging.getLogger("normalizer")

def now_ms() -> int:
    return int(time.time() * 1000)

async def ensure_group(r):
    try:
        await r.xgroup_create(STREAM_RAW, GROUP, id="0-0", mkstream=True)
        log.info(f"[normalizer] group {GROUP} created on {STREAM_RAW}")
    except Exception:
        # grupo ya existe
        pass

def _reason_from_exception(e: Exception) -> str:
    s = str(e).lower()
    if "identity_missing" in s or ("missing" in s and "pid" in s):
        return "identity_missing"
    if "schema_validation_failed" in s:
        return "schema_validation_failed"
    if "missing_code" in s:
        return "missing_code"
    if "ts_must_be_int" in s:
        return "invalid_ts"
    if "encoding" in s:
        return "encoding_error"
    if "empty_message" in s:
        return "empty_message"
    if "version" in s or "v2.3" in s or "v2.5" in s:
        return "unsupported_or_mixed_version"
    return "malformed_hl7"

def _parse_hl7_ts(ts_str: str) -> int:
    """
    HL7 suele venir como YYYYMMDD[HHMMSS(.s+)[+/-ZZZZ]]
    Para demo, soportamos YYYYMMDD y YYYYMMDDHHMMSS.
    """
    ts = now_ms()
    try:
        s = (ts_str or "").strip()
        if not s or not s.isdigit():
            return ts
        if len(s) >= 14:
            dt = datetime.strptime(s[:14], "%Y%m%d%H%M%S")
        elif len(s) >= 8:
            dt = datetime.strptime(s[:8], "%Y%m%d")
        else:
            return ts
        return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
    except Exception:
        return ts

def _mk_idem(parsed: Dict[str, Any], raw: str) -> str:
    # MSH-10 Message Control ID si está; fallback al hash del mensaje
    mcid = parsed.get("MSH", {}).get("10") or parsed.get("message_control_id")
    base = mcid or raw
    return str(hash(base))

def _extract_obx_list(parsed: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Asegura que tengamos una lista de OBX. Si viene dict, la convierte en [dict].
    Si no hay OBX, devuelve [].
    """
    obx = parsed.get("OBX")
    if obx is None:
        return []
    if isinstance(obx, list):
        return [x for x in obx if x]
    if isinstance(obx, dict):
        return [obx]
    return []

def _to_event_common_from_obx(parsed: Dict[str, Any], obx: Dict[str, Any], raw: str) -> Dict[str, Any]:
    """
    Construye un evento común a partir de un mensaje parseado y un OBX específico.
    Ajusta los paths según tu parseador HL7.
    """
    pid = parsed.get("PID", {}) or {}
    # Identidad (usa patient_id si lo tienes; si no, MRN + DOB)
    patient_id = (pid.get("3.1") or pid.get("3") or "").strip()
    mrn = "" if patient_id else (pid.get("3.1") or "").strip()
    dob = (pid.get("7.1") or pid.get("7") or "").strip()

    # Código y valores
    code = (obx.get("3.1") or obx.get("3") or "").strip()
    alias = code.lower() if code else ""
    value = obx.get("5")
    if value is None:
        value = ""   # EventCommon exige string; convertimos abajo
    unit  = (obx.get("6.1") or obx.get("6") or "").strip()

    # timestamp preferido: OBX-14; fallback MSH-7
    ts_str = (obx.get("14") or parsed.get("MSH", {}).get("7") or "").strip()
    ts = _parse_hl7_ts(ts_str)

    # idempotencia y versión
    idem = _mk_idem(parsed, raw)
    hl7_ver = parsed.get("_hl7_version") or parsed.get("MSH", {}).get("12")

    evt = {
        "schema_version": "v1",
        "patient_id": patient_id or None,
        "mrn": mrn or None,
        "dob": dob or None,
        "source": "hl7",
        "type": "lab",
        "code": alias or code,    # alias si existe, si no el raw_code
        "raw_code": code or None,
        "value": str(value),      # <-- normalizamos a string
        "unit": unit or None,
        "ts": ts,
        "ingest_ts": parsed.get("_ingest_ts") or now_ms(),
        "normalized_ts": now_ms(),
        "idempotency_key": idem,
        "hl7_version": hl7_ver,
    }
    return evt

async def run():
    r = get_redis()
    await ensure_group(r)

    while True:
        try:
            resp = await r.xreadgroup(
                GROUP, CONSUMER,
                streams={STREAM_RAW: ">"},
                count=COUNT, block=BLOCK_MS
            )
            if not resp:
                continue

            processed = 0
            for _stream, entries in resp:
                for msg_id, fields in entries:
                    # 1) Obtener el mensaje crudo
                    #raw_json = fields.get("m") or fields.get("message") or fields.get("raw") or fields.get("raw_message") or ""
                    candidates = ("message", "m", "raw", "raw_message", "payload", "hl7")
                    raw_json = None
                    for k in candidates:
                        if k in fields and fields[k]:
                            raw_json = fields[k]
                            break
                    if not raw_json and fields:
                        # último recurso: toma el primer valor del dict
                        raw_json = next(iter(fields.values()), "")

                    # sigue igual que antes:
                    if raw_json and raw_json.lstrip().startswith("{"):
                        outer = json.loads(raw_json)
                        raw = outer.get("message") or outer.get("raw_message") or outer.get("raw") or raw_json
                    else:
                        raw = raw_json

                    if not raw:
                        raise ValueError("empty_message")
                    try:
                        if raw_json and raw_json.lstrip().startswith("{"):
                            outer = json.loads(raw_json)
                            raw = outer.get("message") or outer.get("raw_message") or outer.get("raw") or raw_json
                        else:
                            raw = raw_json

                        if not raw:
                            raise ValueError("empty_message")

                        # 2) Parsear HL7 tolerante (mezcla v2.3/v2.5, encoding, etc.)
                        parsed = hl7_client.parse_hl7_tolerant(raw)

                        # 3) Extraer todos los OBX y construir eventos
                        obx_list = _extract_obx_list(parsed)
                        if not obx_list:
                            # Sin OBX también puede ser válido (ej. ADT). Para demo, envía a DLQ.
                            raise ValueError("missing_required_fields: OBX")

                        events: List[str] = []
                        for obx in obx_list:
                            evt_dict = _to_event_common_from_obx(parsed, obx, raw)

                            # 4) Validar contrato EventCommon
                            try:
                                evt = EventCommon(**evt_dict)
                            except Exception as ve:
                                # Este OBX falla contrato → se va a DLQ individual
                                reason = "schema_validation_failed"
                                await r.xadd(
                                    STREAM_DLQ,
                                    {
                                        "m": raw_json,
                                        "reason": reason,
                                        "raw_id": msg_id,
                                        "source": "hl7",
                                        "err": str(ve),
                                    },
                                    maxlen=MAXLEN_DLQ, approximate=True
                                )
                                continue  # sigue con el siguiente OBX

                            events.append(evt.json(ensure_ascii=False))

                        if not events:
                            # Ningún OBX válido → DLQ del mensaje
                            raise ValueError("schema_validation_failed: no valid OBX events")

                        # 5) Publicar 1 evento por OBX (y recién entonces ACK)
                        #    Para evitar perder, publicamos secuencialmente y si todo ok, ACK.
                        for ejson in events:
                            await r.xadd(
                                STREAM_NORM, {"e": ejson},
                                maxlen=MAXLEN_NORM, approximate=True
                            )
                        await r.xack(STREAM_RAW, GROUP, msg_id)
                        processed += 1

                    except Exception as e:
                        reason = _reason_from_exception(e)
                        # Publica el mensaje completo a DLQ y ACK (para no bloquear el grupo)
                        await r.xadd(
                            STREAM_DLQ,
                            {
                                "m": raw_json,
                                "reason": reason,
                                "raw_id": msg_id,
                                "source": "hl7",
                                "err": str(e),
                            },
                            maxlen=MAXLEN_DLQ, approximate=True
                        )
                        await r.xack(STREAM_RAW, GROUP, msg_id)

            if processed:
                log.info(f"[normalizer] processed messages={processed}")

        except Exception as e:
            log.exception(f"[normalizer] loop error: {e}")
            await asyncio.sleep(1.0)

if __name__ == "__main__":
    asyncio.run(run())
