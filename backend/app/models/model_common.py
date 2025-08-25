# app/models/event_common.py
from pydantic import BaseModel, Field, root_validator
from typing import Optional, Literal
import time

class EventCommon(BaseModel):
    schema_version: Literal["v1"] = "v1"

    # Identidad (al menos uno de estos: patient_id  O  (mrn y dob))
    patient_id: Optional[str] = None
    mrn: Optional[str] = None
    dob: Optional[str] = None  

    # Origen y contenido clínico
    source: Literal["hl7","fhir","wearable"] = "hl7"
    type: Literal["lab","vital","pro"] = "lab"
    code: str
    raw_code: Optional[str] = None
    value: str
    unit: Optional[str] = None

    # Tiempos (ms epoch)
    ts: int
    ingest_ts: int = Field(default_factory=lambda: int(time.time()*1000))
    normalized_ts: int = Field(default_factory=lambda: int(time.time()*1000))

    # Trazabilidad
    idempotency_key: str
    hl7_version: Optional[str] = None

    @root_validator
    def _identity_rule(cls, values):
        pid, mrn, dob = values.get("patient_id"), values.get("mrn"), values.get("dob")
        if not pid and not (mrn and dob):
            raise ValueError("identity_missing: provide patient_id OR (mrn AND dob)")
        if not values.get("code"):
            raise ValueError("missing_code")
        if not isinstance(values.get("ts"), int):
            raise ValueError("ts_must_be_int_epoch_ms")
        return values
