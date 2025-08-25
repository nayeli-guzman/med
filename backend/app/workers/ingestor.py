# app/workers/ingestor.py
import asyncio
import json
import os
import random
import logging
import httpx                     # <-- IMPORTANTE
import redis.asyncio as redis

from app.clients import hl7_client
from app.core import config

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ingestor")

STREAM_KEY = "hl7:raw"
MAXLEN = int(os.getenv("HL7_STREAM_MAXLEN", "5000"))
BATCH = int(os.getenv("HL7_INGEST_BATCH", "100"))

async def run():
    r = redis.from_url(config.REDIS_URL, decode_responses=True)
    backoff = 1.0
    while True:
        try:
            # ⬇️ SIN limit=
            msgs = await hl7_client.get_hl7_messages()
            if not isinstance(msgs, list):
                msgs = []

            if msgs:
                msgs = msgs[:BATCH]  # ⬅️ slicing local
                for m in msgs:
                    if isinstance(m, str):
                        val = {"message": m}
                    elif isinstance(m, dict):
                        val = {
                            k: (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
                            for k, v in m.items()
                            if k in ("id","message","source","timestamp","raw_message","raw")
                        }
                        if "message" not in val:
                            raw = m.get("raw_message") or m.get("raw") or ""
                            if raw:
                                val["message"] = raw
                    else:
                        val = {"message": str(m)}

                    if "message" not in val or not val["message"]:
                        continue

                    await r.xadd(STREAM_KEY, val, maxlen=MAXLEN, approximate=True)

                backoff = 1.0  # éxito: resetea backoff

            await asyncio.sleep(float(os.getenv("HL7_POLL_INTERVAL", "0.5")))

        except (httpx.ReadTimeout, httpx.ConnectError, httpx.HTTPStatusError, json.JSONDecodeError) as e:
            log.error("[ingestor] loop error: %s", e)
            # backoff exponencial con jitter
            await asyncio.sleep(min(30.0, backoff + random.random()))
            backoff = min(30.0, backoff * 2)

        except Exception as e:
            log.error("[ingestor] unexpected error: %s", e, exc_info=True)
            await asyncio.sleep(1.0)

if __name__ == "__main__":
    asyncio.run(run())
