# app/clients/hl7_client.py
import os, httpx
from hl7apy.parser import parse_message

from app.core import config

async def get_hl7_messages():
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{config.HL7_BASE}/hl7/messages")
        r.raise_for_status()
        return r.json()


def _iter_segments(msg, name: str):
    """Recorre recursivamente grupos/segmentos y devuelve todos los segmentos con .name == name"""
    found = []
    stack = [msg]
    while stack:
        node = stack.pop()
        # children puede ser iterable; lo convertimos a lista con getattr por seguridad
        children = list(getattr(node, "children", []))
        for ch in children:
            # algunos children serán grupos, otros segmentos
            if getattr(ch, "name", None) == name:
                found.append(ch)
            # seguir bajando
            stack.append(ch)
    return found

def parse_hl7(raw: str):
    # Forzamos versión y desactivamos validación estricta (evita errores por variantes)
    msg = parse_message(raw, find_groups=False, validation_level=None)

    # PID-3: Patient Identifier List
    patient_identifier = None
    pid = getattr(msg, "PID", None)
    if pid:
        try:
            # cx_1 = ID (ajusta si tu feed usa otra subposición)
            patient_identifier = pid.pid_3[0].cx_1.to_er7()
        except Exception:
            patient_identifier = None

    observations = []
    # Buscar OBX en cualquier nivel (bajo OBR/OBSERVATION/ORDER_OBSERVATION, etc.)
    obx_segments = _iter_segments(msg, "OBX")

    for obx in obx_segments:
        # OBX-3: CE = (identifier, text, coding system)
        try:
            code = obx.obx_3.ce_1.to_er7()
            name = obx.obx_3.ce_2.to_er7()
        except Exception:
            code, name = None, None

        # OBX-5: valor (puede ser NM/TX/etc.)
        try:
            value = obx.obx_5.to_er7()
        except Exception:
            value = None

        # OBX-6: unidades (CE)
        try:
            unit = obx.obx_6.ce_2.to_er7() or obx.obx_6.ce_1.to_er7()
        except Exception:
            unit = None

        # OBX-14: fecha/hora observación
        try:
            ts = obx.obx_14.to_er7()
        except Exception:
            ts = None

        # OBX-8: flag anormal
        try:
            flag = obx.obx_8.to_er7()
        except Exception:
            flag = None

        observations.append({
            "code": code, "name": name, "value": value, "unit": unit,
            "effective_dt": ts, "flag": flag, "source": "HL7"
        })

    return {"patient_identifier": patient_identifier, "observations": observations}
