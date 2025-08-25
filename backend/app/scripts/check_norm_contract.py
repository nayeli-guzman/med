import asyncio, os, json, sys
import redis.asyncio as redis
from app.core import config
from app.models.event_common import EventCommon
STREAM = os.getenv("HL7_NORM_STREAM", "hl7:norm")

async def main():
    r = redis.from_url(config.REDIS_URL, decode_responses=True)
    entries = await r.xrevrange(STREAM, count=int(os.getenv("CHECK_N","50")))
    bad = 0
    for mid, fields in entries:
        try:
            e = fields.get("e"); assert e, "missing_e_field"
            EventCommon(**json.loads(e))
        except Exception as ex:
            bad += 1; print(f"[FAIL] {mid} -> {ex}")
    if bad: print(f"Contract FAILED: {bad}/{len(entries)}"); sys.exit(1)
    print(f"Contract OK: {len(entries)} valid")
if __name__ == "__main__":
    asyncio.run(main())
