-- Automatically generated Schema Definition

CREATE TABLE ai_insights (
    insight_id UUID NOT NULL DEFAULT uuid_generate_v4(),
    run_id UUID NOT NULL,
    feature_name CHARACTER VARYING NOT NULL,
    shap_value NUMERIC NOT NULL,
    rank_position SMALLINT NOT NULL
);

CREATE TABLE domain_scores (
    score_id UUID NOT NULL DEFAULT uuid_generate_v4(),
    run_id UUID NOT NULL,
    cardiovascular NUMERIC NOT NULL,
    metabolic NUMERIC NOT NULL,
    renal NUMERIC NOT NULL
);

CREATE TABLE users (
    user_id UUID NOT NULL DEFAULT uuid_generate_v4(),
    username CHARACTER VARYING NOT NULL,
    email CHARACTER VARYING NOT NULL,
    password_hash TEXT NOT NULL,
    role USER-DEFINED NOT NULL,
    status USER-DEFINED NOT NULL DEFAULT 'active'::account_status,
    mfa_enabled BOOLEAN NOT NULL DEFAULT false,
    failed_logins SMALLINT NOT NULL DEFAULT 0,
    locked_until TIMESTAMP WITH TIME ZONE,
    last_login_at TIMESTAMP WITH TIME ZONE,
    last_login_ip INET,
    last_device CHARACTER VARYING,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

CREATE TABLE doctors (
    doctor_id UUID NOT NULL DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL,
    full_name CHARACTER VARYING NOT NULL,
    title CHARACTER VARYING NOT NULL DEFAULT 'Dr.'::character varying,
    specialisation CHARACTER VARYING NOT NULL,
    qualification CHARACTER VARYING NOT NULL,
    registration_no CHARACTER VARYING NOT NULL,
    department CHARACTER VARYING,
    hospital CHARACTER VARYING,
    phone CHARACTER VARYING,
    work_email CHARACTER VARYING,
    total_patients INTEGER NOT NULL DEFAULT 0,
    total_diagnostics INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

CREATE TABLE patients (
    patient_id UUID NOT NULL DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL,
    full_name CHARACTER VARYING NOT NULL,
    date_of_birth DATE NOT NULL,
    gender USER-DEFINED NOT NULL,
    blood_group USER-DEFINED NOT NULL DEFAULT 'Unknown'::blood_group,
    bmi NUMERIC,
    height_cm NUMERIC,
    weight_kg NUMERIC,
    phone CHARACTER VARYING,
    address TEXT,
    city CHARACTER VARYING,
    state CHARACTER VARYING,
    pincode CHARACTER VARYING,
    smoker BOOLEAN NOT NULL DEFAULT false,
    known_diabetes BOOLEAN NOT NULL DEFAULT false,
    known_hypertension BOOLEAN NOT NULL DEFAULT false,
    known_ckd BOOLEAN NOT NULL DEFAULT false,
    allergies ARRAY,
    notes TEXT,
    current_score NUMERIC,
    current_tier USER-DEFINED,
    last_scan_at TIMESTAMP WITH TIME ZONE,
    primary_doctor_id UUID,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

CREATE TABLE admins (
    admin_id UUID NOT NULL DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL,
    full_name CHARACTER VARYING NOT NULL,
    department CHARACTER VARYING NOT NULL DEFAULT 'IT & Systems'::character varying,
    access_level SMALLINT NOT NULL DEFAULT 1,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

CREATE TABLE diagnostic_runs (
    run_id UUID NOT NULL DEFAULT uuid_generate_v4(),
    patient_id UUID NOT NULL,
    doctor_id UUID,
    health_score NUMERIC NOT NULL,
    risk_tier USER-DEFINED NOT NULL,
    heart_risk_pct NUMERIC NOT NULL DEFAULT 0.00,
    diabetes_risk_pct NUMERIC NOT NULL DEFAULT 0.00,
    kidney_risk_pct NUMERIC NOT NULL DEFAULT 0.00,
    weight_heart NUMERIC NOT NULL DEFAULT 0.400,
    weight_diabetes NUMERIC NOT NULL DEFAULT 0.350,
    weight_kidney NUMERIC NOT NULL DEFAULT 0.250,
    recommended_dept CHARACTER VARYING,
    urgency USER-DEFINED NOT NULL DEFAULT 'Routine'::urgency_level,
    urgency_note TEXT,
    latency_ms NUMERIC,
    model_version CHARACTER VARYING NOT NULL DEFAULT '3.0.1'::character varying,
    trend_status CHARACTER VARYING,
    trend_delta NUMERIC,
    trend_forecast NUMERIC,
    patient_context_desc TEXT,
    doctor_notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

CREATE TABLE clinical_flags (
    flag_id UUID NOT NULL DEFAULT uuid_generate_v4(),
    run_id UUID NOT NULL,
    domain CHARACTER VARYING NOT NULL,
    severity USER-DEFINED NOT NULL,
    message TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

CREATE TABLE patient_vitals (
    vitals_id UUID NOT NULL DEFAULT uuid_generate_v4(),
    run_id UUID NOT NULL,
    patient_id UUID NOT NULL,
    glucose NUMERIC,
    hba1c NUMERIC,
    insulin NUMERIC,
    post_prandial_glucose NUMERIC,
    blood_pressure_sys SMALLINT,
    blood_pressure_dia SMALLINT,
    heart_rate SMALLINT,
    cholesterol_total NUMERIC,
    ldl NUMERIC,
    hdl NUMERIC,
    triglycerides NUMERIC,
    egfr NUMERIC,
    creatinine NUMERIC,
    bun NUMERIC,
    urine_albumin_creatinine NUMERIC,
    bmi NUMERIC,
    height_cm NUMERIC,
    weight_kg NUMERIC,
    skin_thickness NUMERIC,
    temperature NUMERIC,
    spo2 NUMERIC,
    hemoglobin NUMERIC,
    pregnancies SMALLINT,
    recorded_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

CREATE TABLE medications (
    medication_id UUID NOT NULL DEFAULT uuid_generate_v4(),
    name CHARACTER VARYING NOT NULL,
    drug_class CHARACTER VARYING NOT NULL,
    disease_target CHARACTER VARYING NOT NULL,
    dosage_standard CHARACTER VARYING NOT NULL,
    indication TEXT NOT NULL,
    notes TEXT,
    serious_risk TEXT,
    guideline CHARACTER VARYING,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

CREATE TABLE appointments (
    appointment_id UUID NOT NULL DEFAULT uuid_generate_v4(),
    patient_id UUID NOT NULL,
    doctor_id UUID NOT NULL,
    type USER-DEFINED NOT NULL DEFAULT 'consultation'::appointment_type,
    status USER-DEFINED NOT NULL DEFAULT 'booked'::appointment_status,
    scheduled_at TIMESTAMP WITH TIME ZONE NOT NULL,
    duration_min SMALLINT NOT NULL DEFAULT 30,
    location CHARACTER VARYING,
    notes TEXT,
    follow_up_due DATE,
    completed_at TIMESTAMP WITH TIME ZONE,
    cancelled_at TIMESTAMP WITH TIME ZONE,
    cancellation_reason TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

CREATE TABLE patient_medications (
    patient_med_id UUID NOT NULL DEFAULT uuid_generate_v4(),
    patient_id UUID NOT NULL,
    medication_id UUID NOT NULL,
    prescribed_by UUID,
    dosage_actual CHARACTER VARYING NOT NULL,
    frequency CHARACTER VARYING NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE,
    status CHARACTER VARYING NOT NULL DEFAULT 'active'::character varying,
    notes TEXT,
    prescribed_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

CREATE TABLE diagnostic_reports (
    report_id UUID NOT NULL DEFAULT uuid_generate_v4(),
    run_id UUID NOT NULL,
    patient_id UUID NOT NULL,
    doctor_id UUID,
    filename CHARACTER VARYING NOT NULL,
    file_path TEXT NOT NULL,
    file_size_kb INTEGER,
    report_type CHARACTER VARYING NOT NULL DEFAULT 'full_diagnostic'::character varying,
    model_version CHARACTER VARYING NOT NULL DEFAULT '3.0.1'::character varying,
    generated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

CREATE TABLE visit_history (
    visit_id UUID NOT NULL DEFAULT uuid_generate_v4(),
    patient_id UUID NOT NULL,
    run_id UUID,
    doctor_id UUID,
    visit_date DATE NOT NULL DEFAULT CURRENT_DATE,
    visit_type CHARACTER VARYING NOT NULL DEFAULT 'Full Diagnostic'::character varying,
    health_score NUMERIC,
    risk_tier USER-DEFINED,
    glucose NUMERIC,
    hba1c NUMERIC,
    egfr NUMERIC,
    bp_sys SMALLINT,
    bp_dia SMALLINT,
    summary_notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

CREATE TABLE notifications (
    notification_id UUID NOT NULL DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL,
    type USER-DEFINED NOT NULL,
    priority USER-DEFINED NOT NULL DEFAULT 'medium'::notification_priority,
    title CHARACTER VARYING NOT NULL,
    message TEXT NOT NULL,
    source CHARACTER VARYING,
    is_read BOOLEAN NOT NULL DEFAULT false,
    read_at TIMESTAMP WITH TIME ZONE,
    is_dismissed BOOLEAN NOT NULL DEFAULT false,
    dismissed_at TIMESTAMP WITH TIME ZONE,
    action_url TEXT,
    related_patient_id UUID,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

CREATE TABLE audit_logs (
    log_id UUID NOT NULL DEFAULT uuid_generate_v4(),
    user_id UUID,
    username CHARACTER VARYING,
    user_role USER-DEFINED,
    action CHARACTER VARYING NOT NULL,
    resource CHARACTER VARYING,
    status CHARACTER VARYING NOT NULL DEFAULT 'success'::character varying,
    detail TEXT,
    ip_address INET,
    device_info CHARACTER VARYING,
    session_id CHARACTER VARYING,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

CREATE TABLE beds (
    bed_id UUID NOT NULL DEFAULT uuid_generate_v4(),
    bed_number CHARACTER VARYING NOT NULL,
    ward CHARACTER VARYING NOT NULL,
    floor SMALLINT NOT NULL DEFAULT 1,
    status USER-DEFINED NOT NULL DEFAULT 'available'::bed_status,
    occupied_by UUID,
    admitted_at TIMESTAMP WITH TIME ZONE,
    expected_discharge DATE,
    notes TEXT,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

CREATE TABLE equipment (
    equipment_id UUID NOT NULL DEFAULT uuid_generate_v4(),
    name CHARACTER VARYING NOT NULL,
    category CHARACTER VARYING NOT NULL,
    location CHARACTER VARYING,
    status USER-DEFINED NOT NULL DEFAULT 'operational'::equipment_status,
    utilisation_pct SMALLINT NOT NULL DEFAULT 0,
    last_maintenance DATE,
    next_maintenance DATE,
    notes TEXT,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

CREATE TABLE staff_oncall (
    oncall_id UUID NOT NULL DEFAULT uuid_generate_v4(),
    doctor_id UUID NOT NULL,
    shift_date DATE NOT NULL DEFAULT CURRENT_DATE,
    shift_label CHARACTER VARYING NOT NULL,
    shift_start TIME WITHOUT TIME ZONE NOT NULL,
    shift_end TIME WITHOUT TIME ZONE NOT NULL,
    patient_count SMALLINT NOT NULL DEFAULT 0,
    is_available BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE support_engineers (
    se_id UUID NOT NULL DEFAULT uuid_generate_v4(),
    username CHARACTER VARYING NOT NULL,
    email CHARACTER VARYING NOT NULL,
    password_hash TEXT NOT NULL,
    full_name CHARACTER VARYING NOT NULL,
    role CHARACTER VARYING NOT NULL DEFAULT 'standard_se'::character varying,
    department CHARACTER VARYING,
    is_active BOOLEAN NOT NULL DEFAULT true,
    last_login_at TIMESTAMP WITH TIME ZONE,
    last_login_ip INET,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

CREATE TABLE dev_reports (
    report_id UUID NOT NULL DEFAULT uuid_generate_v4(),
    title CHARACTER VARYING NOT NULL,
    description TEXT NOT NULL,
    evidence TEXT,
    priority USER-DEFINED NOT NULL DEFAULT 'medium'::report_priority,
    status USER-DEFINED NOT NULL DEFAULT 'open'::report_status,
    reporter_id UUID NOT NULL,
    assignee_team CHARACTER VARYING NOT NULL DEFAULT 'Backend Team'::character varying,
    tags ARRAY,
    linked_event_id UUID,
    linked_model_event CHARACTER VARYING,
    filed_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    resolved_at TIMESTAMP WITH TIME ZONE
);

CREATE TABLE model_events (
    event_id UUID NOT NULL DEFAULT uuid_generate_v4(),
    run_id UUID,
    patient_id CHARACTER VARYING,
    initiated_by CHARACTER VARYING,
    type USER-DEFINED NOT NULL DEFAULT 'response'::model_event_type,
    status USER-DEFINED NOT NULL DEFAULT 'ok'::model_event_status,
    model_version CHARACTER VARYING NOT NULL DEFAULT '3.0.1'::character varying,
    latency_ms NUMERIC,
    input_payload JSONB,
    output_payload JSONB,
    error_code CHARACTER VARYING,
    error_field CHARACTER VARYING,
    error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

CREATE TABLE refresh_token_store (
    jti TEXT NOT NULL,
    user_id UUID NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

