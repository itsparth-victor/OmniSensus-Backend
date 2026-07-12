from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List, Any
from datetime import datetime, date
from uuid import UUID
from enum import Enum

# ── ENUMS ─────────────────────────────────────────────────────────────────

class UserRole(str, Enum):
    admin   = "admin"
    doctor  = "doctor"
    patient = "patient"

class RiskTier(str, Enum):
    stable      = "Stable"
    borderline  = "Borderline"
    critical    = "Critical"

class AppointmentType(str, Enum):
    consultation    = "consultation"
    follow_up       = "follow_up"
    critical_review = "critical_review"
    procedure       = "procedure"
    lab_review      = "lab_review"
    annual_physical = "annual_physical"

class AppointmentStatus(str, Enum):
    booked      = "booked"
    confirmed   = "confirmed"
    completed   = "completed"
    cancelled   = "cancelled"
    no_show     = "no_show"

class UrgencyLevel(str, Enum):
    routine     = "Routine"
    semi_urgent = "Semi-Urgent"
    urgent      = "Urgent"
    critical    = "Critical"

# ── BASE ──────────────────────────────────────────────────────────────────

class BaseResponse(BaseModel):
    status: str = "success"

# ── AUTH ──────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str
    role: Optional[str] = None

class LoginResponse(BaseResponse):
    access_token:  str
    refresh_token: str
    role:          str
    name:          str
    user_id:       str
    redirect:      str

class RefreshRequest(BaseModel):
    refresh_token: str

class ForgotPasswordRequest(BaseModel):
    email: EmailStr

# ── USER ──────────────────────────────────────────────────────────────────

class UserOut(BaseModel):
    user_id:       UUID
    username:      str
    email:         str
    role:          str
    status:        str
    mfa_enabled:   bool
    failed_logins: int
    last_login_at: Optional[datetime]
    created_at:    datetime
    full_name:     Optional[str] = None

    class Config:
        from_attributes = True

class UserStatusUpdate(BaseModel):
    status: str

# ── PATIENT ───────────────────────────────────────────────────────────────

class PatientOut(BaseModel):
    patient_id:        UUID
    full_name:         str
    age:               Optional[int]
    gender:            str
    blood_group:       str
    bmi:               Optional[float]
    current_score:     Optional[float]
    current_tier:      Optional[str]
    last_scan_at:      Optional[datetime]
    known_diabetes:    bool
    known_hypertension:bool
    known_ckd:         bool
    smoker:            bool
    doctor_name:       Optional[str]
    account_status:    Optional[str]

    class Config:
        from_attributes = True

class PatientUpdate(BaseModel):
    phone:              Optional[str]
    address:            Optional[str]
    city:               Optional[str]
    state:              Optional[str]
    pincode:            Optional[str]
    notes:              Optional[str]
    smoker:             Optional[bool]
    known_diabetes:     Optional[bool]
    known_hypertension: Optional[bool]
    known_ckd:          Optional[bool]
    bmi:                Optional[float]
    height_cm:          Optional[float]
    weight_kg:          Optional[float]

class PatientListResponse(BaseResponse):
    patients:  List[PatientOut]
    total:     int
    page:      int
    page_size: int
    pages:     int

# ── VITALS ────────────────────────────────────────────────────────────────

class VitalsIn(BaseModel):
    glucose:            Optional[float]
    hba1c:              Optional[float]
    insulin:            Optional[float]
    blood_pressure_sys: Optional[int]
    blood_pressure_dia: Optional[int]
    blood_pressure:     Optional[float]
    heart_rate:         Optional[int]
    cholesterol_total:  Optional[float]
    ldl:                Optional[float]
    hdl:                Optional[float]
    egfr:               Optional[float]
    creatinine:         Optional[float]
    bmi:                Optional[float]
    height_cm:          Optional[float]
    weight_kg:          Optional[float]
    age:                Optional[float]
    spo2:               Optional[float]
    temperature:        Optional[float]
    hemoglobin:         Optional[float]
    pregnancies:        Optional[int]

    def to_dict(self) -> dict:
        return {k: v for k, v in self.model_dump().items() if v is not None}

# ── DIAGNOSTIC ────────────────────────────────────────────────────────────

class PatientInfoIn(BaseModel):
    age:          Optional[float]
    sex:          Optional[str]
    diabetes:     Optional[str]
    hypertension: Optional[str]
    smoker:       Optional[str]
    ckd:          Optional[str]

class DiagnoseRequest(BaseModel):
    patient_id:   str
    vitals:       VitalsIn
    patient_info: Optional[PatientInfoIn] = None
    history:      Optional[List[float]]   = None

class DiagnoseResponse(BaseResponse):
    health_score:     float
    risk_tier:        str
    domain_scores:    dict
    raw_risks:        dict
    clinical_flags:   List[dict]
    flag_count:       int
    trend_analysis:   dict
    triage:           dict
    ai_insights:      dict
    medications:      dict
    patient_context:  Optional[str]
    adaptive_weights: Optional[dict]
    latency_ms:       float
    patient_id:       str
    model_version:    str
    run_id:           Optional[str]
    timestamp:        str

# ── APPOINTMENTS ─────────────────────────────────────────────────────────

class AppointmentCreate(BaseModel):
    patient_id:   UUID
    doctor_id:    UUID
    type:         AppointmentType = AppointmentType.consultation
    scheduled_at: datetime
    duration_min: int = 30
    notes:        Optional[str]
    follow_up_due:Optional[date]

class AppointmentStatusUpdate(BaseModel):
    status: AppointmentStatus
    reason: Optional[str]

class AppointmentOut(BaseModel):
    appointment_id: UUID
    patient_id:     UUID
    doctor_id:      UUID
    type:           str
    status:         str
    scheduled_at:   datetime
    duration_min:   int
    notes:          Optional[str]
    patient_name:   Optional[str]
    doctor_name:    Optional[str]

    class Config:
        from_attributes = True

# ── NOTIFICATIONS ─────────────────────────────────────────────────────────

class NotificationOut(BaseModel):
    notification_id: UUID
    type:            str
    priority:        str
    title:           str
    message:         str
    source:          Optional[str]
    is_read:         bool
    created_at:      datetime

    class Config:
        from_attributes = True

class NotificationsResponse(BaseResponse):
    notifications: List[NotificationOut]
    unread_count:  int
    total:         int

# ── CHAT ──────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    prompt:      Optional[str]
    question:    Optional[str]
    session_id:  str = "default"
    patient_id:  Optional[str]
    vitals:      Optional[dict]
    patient_info:Optional[dict]
    history:     Optional[List[float]]
    ml_result:   Optional[dict]

class AskRequest(BaseModel):
    question:    str
    patient_id:  Optional[str]
    vitals:      Optional[dict]
    patient_info:Optional[dict]
    history:     Optional[List[float]]
    ml_result:   Optional[dict]

# ── REPORT ────────────────────────────────────────────────────────────────

class ReportGenerateRequest(BaseModel):
    patient_id:  str
    patient_info:Optional[dict]
    risk_data:   Optional[dict]
    insights:    Optional[dict]
    doctor_name: str = "Dr. R. Sharma"

# ── ADMIN ─────────────────────────────────────────────────────────────────

class AdminAnalyticsResponse(BaseResponse):
    platform:          dict
    model_stats:       List[dict]
    critical_patients: List[dict]

class AuditLogOut(BaseModel):
    log_id:     UUID
    username:   Optional[str]
    user_role:  Optional[str]
    action:     str
    resource:   Optional[str]
    status:     str
    detail:     Optional[str]
    ip_address: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True

# ── GENERIC ──────────────────────────────────────────────────────────────

class MessageResponse(BaseResponse):
    message: str

class HealthResponse(BaseModel):
    status:      str
    service:     str
    version:     str
    db_connected:bool
    timestamp:   str
