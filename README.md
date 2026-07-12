# OmniSensus Backend (API)

This repository contains the **Python backend API** for OmniSensus — responsible for authentication, survey management, persistence, and integration with the AI/ML analytics layer.

---

## Project Context (Parent Hub)

Main documentation hub:
https://github.com/parthvadodariya-616/research_documentations

Related repos:
- Frontend: https://github.com/parthvadodariya-616/omnisensus-website
- ML/Analytics: https://github.com/parthvadodariya-616/OmniSensus-ML_model

---

## Responsibilities

- User authentication and authorization
- Survey lifecycle management (create, edit, publish, collect responses)
- Data validation and secure storage
- API endpoints for frontend consumption
- Triggering / orchestrating analytics runs in the ML module

---

## Setup (Typical)

> Update these steps based on whether you use Flask/Django/FastAPI.

Create venv and install dependencies:
```bash
python -m venv .venv
# Activate your venv, then:
pip install -r requirements.txt
```

Run the server (example):
```bash
python app.py
# or
python manage.py runserver
# or
uvicorn app:app --reload
```

---

## Configuration

Common configs:
- database connection string (PostgreSQL/MongoDB/SQLite)
- JWT/secret keys for auth
- CORS configuration for frontend
- ML service/module endpoint configuration

---

## License

See LICENSE in the repository (if present).
