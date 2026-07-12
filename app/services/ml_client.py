import httpx
import logging
from app.config import settings

logger = logging.getLogger("OmniSensus.MLClient")

ML_HEADERS = {
    "Content-Type": "application/json",
    "X-API-Key":    settings.ML_API_KEY,
}


async def call_ml(method: str, path: str, body: dict = None) -> dict:
    url = settings.ML_API_URL + "/api/v1" + path
    async with httpx.AsyncClient(timeout=30.0) as client:
        if method.upper() == "GET":
            r = await client.get(url, headers=ML_HEADERS)
        else:
            r = await client.post(url, json=body or {}, headers=ML_HEADERS)
        r.raise_for_status()
        return r.json()


async def ml_diagnose(patient_id: str, vitals: dict,
                      patient_info: dict, history: list) -> dict:
    payload = {
        "patient_id":   patient_id,
        "vitals":       vitals,
        "patient_info": patient_info,
        "history":      history,
    }

    try:
        return await call_ml("POST", "/diagnose/contextual", payload)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status is not None and status >= 500:
            logger.warning(
                "Contextual diagnose failed with %s; retrying with basic diagnose",
                status,
            )
            fallback = await call_ml("POST", "/diagnose", payload)
            if isinstance(fallback, dict):
                fallback["ml_fallback_mode"] = "basic_diagnose"
            return fallback
        raise


async def ml_chat(prompt: str, session_id: str, patient_id: str = None,
                  requester_role: str = "patient",
                  vitals: dict = None, patient_info: dict = None,
                  history: list = None, ml_result: dict = None) -> dict:
    return await call_ml("POST", "/chat", {
        "prompt":       prompt,
        "session_id":   session_id,
        "patient_id":   patient_id,
        "requester_role": requester_role,
        "vitals":       vitals       or {},
        "patient_info": patient_info or {},
        "history":      history      or [],
        "ml_result":    ml_result    or {},
    })


async def ml_ask(question: str, patient_id: str = None,
                 requester_role: str = "patient",
                 vitals: dict = None, patient_info: dict = None,
                 history: list = None, ml_result: dict = None) -> dict:
    response = await call_ml("POST", "/ask", {
        "question":     question,
        "patient_id":   patient_id,
        "requester_role": requester_role,
        "vitals":       vitals       or {},
        "patient_info": patient_info or {},
        "history":      history      or [],
        "ml_result":    ml_result    or {},
    })
    logger.info(f"ML API /ask response: {response}")
    return response


async def ml_report(patient_id: str, patient_info: dict,
                    risk_data: dict, insights: dict,
                    doctor_name: str,
                    run_id: str = None,
                    doctor_id: str = None) -> dict:
    return await call_ml("POST", "/report/generate", {
        "patient_id":   patient_id,
        "patient_info": patient_info,
        "risk_data":    risk_data,
        "insights":     insights,
        "doctor_name":  doctor_name,
        "run_id":       run_id,
        "doctor_id":    doctor_id,
    })


async def ml_eda() -> dict:
    return await call_ml("GET", "/eda")


async def ml_risk_factors() -> dict:
    return await call_ml("GET", "/eda/risk-factors")


async def ml_readmission(patient_id: str, vitals: dict,
                         patient_info: dict) -> dict:
    return await call_ml("POST", f"/patients/{patient_id}/readmission-risk", {
        "vitals":       vitals,
        "patient_info": patient_info,
    })


async def ml_health() -> dict:
    return await call_ml("GET", "/health")


async def ml_model_status() -> dict:
    return await call_ml("GET", "/model/status")