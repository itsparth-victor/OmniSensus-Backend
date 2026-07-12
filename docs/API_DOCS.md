# OmniSensus Backend API — Complete Documentation
Version: 3.0.1 | Framework: FastAPI | Database: Neon PostgreSQL

---

## Base URLs
```
Local:      http://localhost:5000/api/v1
<!-- Production: https://omnisensus-backend.onrender.com/api/v1 -->
```

## Authentication
All endpoints except `/auth/login`, `/auth/refresh`, `/api/v1/health` require a Bearer JWT token.

```
Header: Authorization: Bearer <access_token>
```

---

## AUTH ENDPOINTS

### POST /auth/login
Login and get JWT tokens.
```json
Request:
{ "username": "<demo_username>", "password": "<demo_password>", "role": "doctor" }

Response:
{
  "status": "success",
  "access_token": "<jwt_access_token>",
  "refresh_token": "<jwt_refresh_token>",
  "role": "doctor",
  "name": "<display_name>",
  "user_id": "<user_id>",
  "redirect": "doctor.html"
}
```

### POST /auth/refresh
Get new access token using refresh token.
```json
Request:  { "refresh_token": "<jwt_refresh_token>" }
Response: { "status": "success", "access_token": "<jwt_access_token>" }
```

### POST /auth/logout
Logout and log audit entry.
```
Header: Authorization: Bearer <token>
Response: { "status": "success", "message": "Logged out." }
```

### POST /auth/forgot-password
Request password reset email.
```json
Request:  { "email": "user@example.com" }
Response: { "status": "success", "message": "..." }
```

---

## PATIENT ENDPOINTS

### GET /patients
List all patients. Doctor sees only their patients. Admin sees all.
```
Query params:
  tier      → Critical | Borderline | Stable
  search    → name or patient_id string
  page      → default 1
  page_size → default 10, max 50

Response:
{
  "status": "success",
  "patients": [...],
  "total": 100,
  "page": 1,
  "page_size": 10,
  "pages": 10
}
```

### GET /patients/me
Patient views their own profile.
```json
Response: { "status": "success", "patient": { ...full profile... } }
```

### GET /patients/{patient_id}
Get single patient by UUID.
```json
Response: { "status": "success", "patient": { ...full profile... } }
```

### GET /patients/{patient_id}/history
Get visit history for longitudinal timeline.
```json
Response:
{
  "status": "success",
  "visits": [
    {
      "visit_id": "...",
      "visit_date": "2026-03-15",
      "health_score": 61,
      "risk_tier": "Borderline",
      "glucose": 148,
      "hba1c": 6.1,
      "doctor_name": "Dr. Rajesh Sharma"
    }
  ],
  "count": 5
}
```

### GET /patients/{patient_id}/vitals
Get latest vitals snapshot.
```json
Response: { "status": "success", "vitals": { "glucose": 148, "hba1c": 6.1, ... } }
```

### GET /patients/{patient_id}/reports
Get all generated PDF reports.
```json
Response: { "status": "success", "reports": [...], "count": 3 }
```

### GET /patients/{patient_id}/medications
Get active medications.
```json
Response:
{
  "status": "success",
  "medications": [
    {
      "name": "Metformin 500mg",
      "drug_class": "Biguanide",
      "dosage_actual": "500mg",
      "frequency": "BD",
      "start_date": "2026-01-01",
      "prescribed_by_name": "Dr. Rajesh Sharma"
    }
  ]
}
```

---

## DIAGNOSTIC ENDPOINTS

### POST /diagnostics
Run a full diagnostic. Calls ML API, saves all results to DB.
```json
Request:
{
  "patient_id": "P-00421",
  "vitals": {
    "glucose": 148,
    "hba1c": 6.1,
    "bmi": 33.6,
    "blood_pressure_sys": 128,
    "blood_pressure_dia": 82,
    "egfr": 78,
    "age": 42
  },
  "patient_info": {
    "age": 42,
    "sex": "F",
    "diabetes": "no",
    "hypertension": "no",
    "smoker": "no"
  },
  "history": [72, 68, 64, 61]
}

Response:
{
  "status": "success",
  "health_score": 61.0,
  "risk_tier": "Borderline",
  "domain_scores": { "cardiovascular": 68, "metabolic": 52, "renal": 76 },
  "clinical_flags": [...],
  "flag_count": 2,
  "trend_analysis": { "status": "Stable", "delta": -0.5 },
  "triage": { "department": "Endocrinology", "urgency": "Routine" },
  "ai_insights": { "glucose": 0.42, "bmi": 0.31, "hba1c": 0.18 },
  "medications": {...},
  "patient_context": "42-year-old female, obese (BMI 33.6)",
  "latency_ms": 1240.5,
  "run_id": "uuid-here",
  "model_version": "3.0.1"
}
```

### GET /diagnostics/{run_id}
Get full detail of a stored diagnostic run.
```json
Response: { "status": "success", "run": {...}, "flags": [...], "insights": [...], "vitals": {...} }
```

### GET /diagnostics/patient/{patient_id}/latest
Get most recent diagnostic run for a patient.
```json
Response: { "status": "success", "run": {...}, "flags": [...], "insights": [...] }
```

### POST /diagnostics/patient/{patient_id}/readmission-risk
Calculate readmission risk from stored data.
```json
Response:
{
  "status": "success",
  "readmission_risk_pct": 62.5,
  "risk_level": "Moderate",
  "predicted_timeframe": "Within 30 days",
  "recommendations": ["Schedule follow-up within 48-72 hours."],
  "contributing_factors": ["Declining trend (delta: -3.5)"],
  "based_on_runs": 4
}
```

---

## APPOINTMENT ENDPOINTS

### GET /appointments
List appointments. Role-aware: doctor sees own, patient sees own, admin sees all.
```json
Response: { "status": "success", "appointments": [...], "count": 5 }
```

### POST /appointments
Book a new appointment.
```json
Request:
{
  "patient_id": "uuid",
  "doctor_id": "uuid",
  "type": "follow_up",
  "scheduled_at": "2026-04-01T10:00:00Z",
  "duration_min": 30,
  "notes": "Post-diagnostic review"
}

Response: { "status": "success", "appointment_id": "uuid", "message": "Appointment booked." }
```

### PUT /appointments/{appt_id}/status
Update appointment status.
```json
Request:  { "status": "confirmed" }
Values:   booked | confirmed | completed | cancelled | no_show
Response: { "status": "success", "message": "Status updated." }
```

---

## NOTIFICATION ENDPOINTS

### GET /notifications
Get notifications for current user.
```
Query: limit → default 20, max 50

Response:
{
  "status": "success",
  "notifications": [...],
  "unread_count": 3,
  "total": 12
}
```

### PUT /notifications/{notif_id}/read
Mark single notification as read.
```json
Response: { "status": "success", "message": "Marked as read." }
```

### PUT /notifications/read-all
Mark all notifications as read.
```json
Response: { "status": "success", "message": "All notifications marked as read." }
```

---

## ADMIN ENDPOINTS

### GET /admin/analytics
Platform-wide KPIs and statistics.
```json
Response:
{
  "status": "success",
  "platform": {
    "total_patients": 247,
    "mean_score": 64.2,
    "critical_count": 24,
    "borderline_count": 61,
    "stable_count": 162,
    "scanned_this_week": 38
  },
  "critical_patients": [...],
  "model_stats": {...}
}
```

### GET /admin/users
List all portal users with pagination.
```
Query: role, status, page, page_size
Response: { "status": "success", "users": [...], "total": 50, "page": 1 }
```

### PUT /admin/users/{user_id}/status
Activate, suspend, or deactivate a user.
```json
Request:  { "status": "suspended" }
Response: { "status": "success", "message": "User status updated to suspended." }
```

### GET /admin/audit
Paginated audit log.
```
Query: page, page_size, action, status
Response: { "status": "success", "logs": [...], "total": 500, "page": 1 }
```

### GET /admin/resources/beds
Bed occupancy for resource allocation page.
```json
Response:
{
  "status": "success",
  "beds": [...],
  "summary": { "total": 120, "occupied": 84, "available": 28, "icu": 8 }
}
```

### GET /admin/model/performance
ML model performance metrics.
```json
Response: { "status": "success", "recent_failures": [...], "daily_rates": [...] }
```

### GET /admin/eda
Population-level EDA statistics from ML.
```json
Response: { "status": "success", "population_summary": {...}, "patient_store_eda": {...} }
```

### GET /admin/eda/risk-factors
Top risk factors across all patients.
```json
Response: { "status": "success", "risk_factors": {...}, "distributions": {...} }
```

---

## ML PROXY ENDPOINTS
These forward requests to the ML API with authentication.

### POST /ml/chat
Send a chat message to Pharma-Bot.
```json
Request:
{
  "prompt": "What does my health score mean?",
  "session_id": "P-00421-session",
  "patient_id": "P-00421"
}
Response: { "status": "success", "response": "...", "mode": "llm", "intent": "score_meaning" }
```

### POST /ml/chat/clear
Clear chat session memory.
```json
Request:  { "session_id": "P-00421-session" }
Response: { "status": "success", "message": "Session cleared." }
```

### POST /ml/ask
One-shot Q&A without session memory.
```json
Request:  { "question": "What is HbA1c?", "patient_id": "P-00421" }
Response: { "status": "success", "answer": "...", "intent": "hba1c" }
```

### POST /ml/report/generate
Generate a PDF clinical report.
```json
Request:
{
  "patient_id": "P-00421",
  "patient_info": { "name": "Ananya Kumar", "age": 42 },
  "doctor_name": "Dr. R. Sharma"
}
Response: { "status": "success", "filename": "report_P-00421_xxx.pdf", "download_url": "..." }
```

### GET /ml/medications
Get full medication catalogue.

### GET /ml/medications/{disease}
Get medications for heart | diabetes | kidney.

### POST /ml/triage
Get department triage recommendation.
```json
Request:  { "text": "chest pain", "health_score": 35 }
Response: { "status": "success", "department": "Cardiology", "urgency": "Urgent" }
```

---

## HEALTH CHECK

### GET /health
```json
Response:
{
  "status": "success",
  "service": "OmniSensus Backend API",
  "version": "3.0.1",
  "env": "production",
  "db_connected": true,
  "timestamp": "2026-03-16T12:00:00Z"
}
```

---

## ERROR RESPONSES

All errors follow this format:
```json
{ "status": "error", "message": "Description of what went wrong." }
```

| Code | Meaning |
|------|---------|
| 400  | Bad request — missing or invalid fields |
| 401  | Not authenticated — missing or expired token |
| 403  | Forbidden — wrong role or account suspended |
| 404  | Resource not found |
| 422  | Validation error — vitals out of range |
| 503  | ML API unavailable |
| 500  | Internal server error |

---

## DEMO CREDENTIALS

| Role    | Username   | Password     |
|---------|------------|--------------|
| Admin   | <admin_username>  | <admin_password>    |
| Doctor  | <doctor_username>  | <doctor_password>    |
| Patient | <patient_username> | <patient_password>   |

---

## LOCAL SETUP

```bash
# 1. Create virtual environment
python -m venv venv
venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create .env file (copy from .env.example)
cp .env.example .env

# 4. Run the server
python main.py
# Server starts at http://localhost:5000

# 5. View interactive docs
# Open http://localhost:5000/docs in browser
```

---

## INTERACTIVE DOCS
FastAPI auto-generates interactive documentation:

```
Swagger UI: http://localhost:5000/docs
ReDoc:      http://localhost:5000/redoc
```

You can test every endpoint directly from the browser with the Swagger UI.
No external tool needed.