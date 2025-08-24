# 🧠 Oncology Intelligence Platform (Demo)

Este proyecto es un **prototipo de plataforma de inteligencia oncológica** que integra múltiples fuentes de datos clínicos (FHIR, HL7, OpenFDA) con un **Clinical AI Assistant (LLMs + RAG)** para generar **insights accionables** sobre adherencia al tratamiento y coordinación de cuidados.

> ⚠️ **Nota:** Este proyecto es parte de un desafío técnico. El sistema externo puede devolver datos incompletos, paginados o inconsistentes. El objetivo es mostrar integración robusta y manejo de errores.

---

## 📂 Estructura del proyecto

```
backend/
└── app/
    ├── clients/              # Clientes HTTP para APIs externas
    │   ├── ai_client.py      # Clinical AI Assistant (LLM + RAG)
    │   ├── fda_client.py     # OpenFDA
    │   ├── fhir_client.py    # Servidor FHIR R4
    │   └── hl7_client.py     # Stream HL7 v2.x
    ├── core/
    │   └── config.py         # Configuración + carga de .env
    ├── services/
    │   ├── aggregate.py      # Normalización y agregación de datos
    │   └── filters.py        # Filtros de calidad y utilidades
    ├── main.py               # API principal (FastAPI)
    └── test.py               # Scripts de prueba
```

---

## ⚙️ Configuración

```bash
# Clonar repositorio
git clone <repo-url>
cd backend

# Crear entorno virtual
python3 -m venv .venv
source .venv/bin/activate

# Instalar dependencias
pip install fastapi uvicorn httpx python-dotenv hl7apy fhir.resources
```

Crear archivo `.env` en `backend/` o raíz del repo:

```ini
FHIR_BASE=https://cancer-care-fhir-api.onrender.com
HL7_BASE=https://cancer-care-hl7-stream.onrender.com
FDA_BASE=https://cancer-care-fda-api.onrender.com
AI_BASE=https://cancer-care-ai-api.onrender.com

FHIR_CLIENT_ID=test_client
FHIR_CLIENT_SECRET=test_secret
```

---

## 🚀 Ejecución

```bash
uvicorn app.main:app --reload
```

Abrir en navegador:  
👉 [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

---

## 📡 Endpoints principales

### `GET /patients?count=N`
Lista de pacientes desde FHIR con manejo de tokens y fallback de errores.

### `GET /patients/{patient_id}/insights`
➡️ **Endpoint estrella**: Integra datos de FHIR, HL7, OpenFDA y el Clinical AI Assistant.

Ejemplo de respuesta:

```json
{
  "status": "partial",
  "unavailable_sources": ["HL7", "AI:analyze"],
  "patient": {
    "id": "paciente-0",
    "name": "María López",
    "birthDate": "1983-05-24",
    "gender": "female"
  },
  "structured_summary": {
    "medications": [],
    "abnormal_labs": [
      {"code": "718-7", "name": "Plaquetas", "value": 12.4, "unit": "10*3/uL"}
    ]
  },
  "drug_interactions": [],
  "ai_insights": {
    "status": "degraded",
    "reason": "AI failed: HTTPStatusError"
  },
  "data_quality": {
    "by_resource": {
      "MedicationRequest": {"total": 0, "kept": 0, "wrong_subject": 0},
      "Observation": {"total": 18, "kept": 2, "wrong_subject": 15}
    },
    "notes": [
      "Strict subject filtering applied",
      "Cancelled entries dropped",
      "HL7 matched by PID-3"
    ]
  }
}
```

---

## 🧪 Pruebas rápidas

```bash
# Obtener token manualmente
curl -X POST https://cancer-care-fhir-api.onrender.com/oauth/token   -H "Content-Type: application/x-www-form-urlencoded"   -d "grant_type=client_credentials&client_id=test_client&client_secret=test_secret"

# Llamar a endpoint estrella
curl "http://127.0.0.1:8000/patients/paciente-0/insights" | jq .

# Probar funciones de AI Client
python -m app.test
```

---

## 📝 Estado actual

✔️ Integración básica con FHIR, HL7, FDA y Clinical AI  
✔️ Endpoint unificado `/patients/{id}/insights`  
✔️ Manejo de errores (token, OperationOutcome, HL7 inválidos)  
✔️ Normalización de datos para IA  
✔️ Reporte de calidad de datos en la salida  

🚧 Pendiente / mejoras:
- UI/Frontend (React, Dash, etc.) para mostrar insights  
- Tests automáticos más completos  
- Mejorar scoring y filtrado de observaciones HL7  
- Integración más profunda con Clinical AI (cuando responda)  

---

## 📖 Créditos

Desarrollado como parte de un **desafío técnico de integración en salud**.  
Stack: **Python 3.11+**, **FastAPI**, **httpx**, **hl7apy**, **FHIR Resources**.
