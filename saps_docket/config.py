"""
config.py  -  SHARED FILE (used by everyone)

All settings for the SAPS Case Docket Management System live here.
Secrets (SECRET_KEY, e-mail password) are NEVER typed in this file.
They are read from the .env file, which is not uploaded to GitHub.

UNIVERSITY PROTOTYPE - this is not an official SAPS system.
"""

import os
import secrets
from datetime import timedelta

from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# Read the .env file (if it exists) into environment variables.
load_dotenv(os.path.join(BASE_DIR, ".env"))


def env_bool(name, default=False):
    """Turn an environment variable such as 'True' / '1' / 'yes' into a real boolean."""
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def env_int(name, default):
    """Read an integer from the environment; use the default if it is missing or invalid."""
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# ----------------------------------------------------------------------------
# Constants that are shared by models.py, services.py, app.py and the templates
# ----------------------------------------------------------------------------

# The two formal roles from the project documentation.

ROLE_OFFICER = "OFFICER"
ROLE_SUPERVISOR = "SUPERVISOR"
ROLE_AUDITOR = "AUDITOR"

# The ONLY three case statuses (as required by the documentation).
STATUS_OPEN = "OPEN"
STATUS_UNDER_INVESTIGATION = "UNDER INVESTIGATION"
STATUS_CLOSED = "CLOSED"
CASE_STATUSES = [STATUS_OPEN, STATUS_UNDER_INVESTIGATION, STATUS_CLOSED]

# Which status changes an officer may make.
# (A closed case can only be re-opened by moving it back to UNDER INVESTIGATION.)
ALLOWED_STATUS_TRANSITIONS = {
    STATUS_OPEN: [STATUS_UNDER_INVESTIGATION, STATUS_CLOSED],
    STATUS_UNDER_INVESTIGATION: [STATUS_CLOSED],
    STATUS_CLOSED: [STATUS_UNDER_INVESTIGATION],
}

# Escalation statuses.
ESCALATION_PENDING = "PENDING"
ESCALATION_REVIEWED = "REVIEWED"

# Incident categories shown on the "Register New Case" form.
INCIDENT_TYPES = [
    "Theft",
    "Burglary",
    "Robbery",
    "Assault",
    "Fraud",
    "Vehicle theft / hijacking",
    "Malicious damage to property",
    "Domestic-related incident",
    "Missing person",
    "Other",
]

# Activity types. The first group is chosen by the officer on the update form.
# The second group is written automatically by the system.
MANUAL_ACTIVITY_TYPES = [
    "Statement Captured",
    "CCTV Requested",
    "CCTV Reviewed",
    "Witness Interview Completed",
    "Evidence Submitted",
    "Case File Reviewed",
    "Follow-up Completed",
    "General Progress Update",
]
SYSTEM_ACTIVITY_TYPES = [
    "Case Registered",
    "Officer Assigned",
    "Status Changed",
    "Case Details Corrected",
    "Supervisor Review",
]
ACTIVITY_TYPES = MANUAL_ACTIVITY_TYPES + SYSTEM_ACTIVITY_TYPES

# What the CITIZEN sees for each activity type. The citizen never sees the
# officer's internal description - only these safe, general sentences.
CITIZEN_ACTIVITY_LABELS = {
    "Case Registered": "Your case was registered at the police station.",
    "Officer Assigned": "An officer was assigned to your case.",
    "Statement Captured": "Your statement was recorded.",
    "CCTV Requested": "A follow-up action was recorded on your case.",
    "CCTV Reviewed": "A follow-up action was recorded on your case.",
    "Witness Interview Completed": "A follow-up action was recorded on your case.",
    "Evidence Submitted": "Information was added to your case file.",
    "Case File Reviewed": "Your case file was reviewed.",
    "Follow-up Completed": "A follow-up action was completed on your case.",
    "General Progress Update": "A progress update was recorded on your case.",
    "Status Changed": "The status of your case was updated.",
    "Case Details Corrected": "Details on your case were corrected.",
    "Supervisor Review": "A supervisor reviewed your case.",
}

# Coded reasons for NOT registering a reported crime (Non-Registration Record).
NON_REGISTRATION_REASONS = {
    "NR01": "Matter is a civil dispute, not a criminal offence",
    "NR02": "Incident is outside this station's area - complainant referred",
    "NR03": "Duplicate of a case that is already registered",
    "NR04": "Complainant withdrew the complaint",
    "NR05": "Not enough information available to register a case",
    "NR06": "Other reason - supervisor attention required",
}

# What a supervisor can record when reviewing an escalation.
SUPERVISOR_ACTIONS = [
    "Officer instructed to update the case",
    "Case reassigned to another officer",
    "Case reviewed - progress is acceptable",
    "Referred to station management",
]


class Config:
    """Flask reads every UPPER_CASE attribute of this class as a setting."""

    # ---- Security -----------------------------------------------------
    # If SECRET_KEY is missing from .env we create a random one. That is
    # safe, but everybody gets logged out whenever the server restarts.
    SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
    SECRET_KEY_FROM_ENV = bool(os.environ.get("SECRET_KEY"))

    SESSION_COOKIE_HTTPONLY = True          # JavaScript cannot read the cookie
    SESSION_COOKIE_SAMESITE = "Lax"         # helps against cross-site requests
    SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", False)  # True only when using HTTPS
    SESSION_TIMEOUT_MINUTES = env_int("SESSION_TIMEOUT_MINUTES", 30)
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=SESSION_TIMEOUT_MINUTES)
    WTF_CSRF_TIME_LIMIT = None              # CSRF token lasts as long as the session

    # ---- Database (SQLite for the local prototype) --------------------
    SQLALCHEMY_DATABASE_URI = "sqlite:///" + os.path.join(BASE_DIR, "saps_docket.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # ---- E-mail (case reference + citizen OTP) ------------------------
    MAIL_SERVER = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT = env_int("MAIL_PORT", 587)
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "").strip()
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "").strip()
    MAIL_USE_TLS = env_bool("MAIL_USE_TLS", True)
    MAIL_USE_SSL = env_bool("MAIL_USE_SSL", False)
    MAIL_SENDER = os.environ.get("MAIL_SENDER", "").strip() or os.environ.get("MAIL_USERNAME", "").strip()

    # ---- Citizen OTP rules -------------------------------------------
    OTP_EXPIRY_MINUTES = env_int("OTP_EXPIRY_MINUTES", 10)
    OTP_MAX_ATTEMPTS = env_int("OTP_MAX_ATTEMPTS", 3)
    OTP_RESEND_COOLDOWN_SECONDS = env_int("OTP_RESEND_COOLDOWN_SECONDS", 60)
    CITIZEN_SESSION_MINUTES = env_int("CITIZEN_SESSION_MINUTES", 15)

    # ---- Login protection --------------------------------------------
    LOGIN_MAX_ATTEMPTS = env_int("LOGIN_MAX_ATTEMPTS", 5)
    LOGIN_LOCKOUT_MINUTES = env_int("LOGIN_LOCKOUT_MINUTES", 10)

    # ---- Standstill / escalation (CONFIGURABLE, not hard-coded) --------
    # A case with no activity for this many days is flagged for supervisor attention.
    CASE_STANDSTILL_DAYS = env_int("CASE_STANDSTILL_DAYS", 7)
    # Which statuses are checked. CLOSED cases are never flagged.
    CASE_STANDSTILL_STATUSES = [STATUS_OPEN, STATUS_UNDER_INVESTIGATION]

    # ---- Display -----------------------------------------------------
    # Times are stored in UTC and shown in South African time (UTC+2, no daylight saving).
    DISPLAY_UTC_OFFSET_HOURS = env_int("DISPLAY_UTC_OFFSET_HOURS", 2)
    PAGE_SIZE = env_int("PAGE_SIZE", 10)
    # Show the officer's initial + surname (e.g. "T. Nkosi") on the citizen page.
    SHOW_OFFICER_NAME_TO_CITIZEN = env_bool("SHOW_OFFICER_NAME_TO_CITIZEN", True)

    # ---- Development helpers ------------------------------------------
    DEBUG_MODE = env_bool("FLASK_DEBUG", False)
    SEED_DEMO_DATA = env_bool("SEED_DEMO_DATA", True)
    TEMPLATES_AUTO_RELOAD = True
