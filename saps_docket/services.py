"""
services.py  -  SHARED / PARTNER MODULE (case-management backend)

This file holds the "business logic" of the system. The web pages in app.py
call these functions so that every important action follows the same rules
and is ALWAYS written to the audit trail.

SECTIONS
    1. Audit trail engine                 (log_audit, log_case_view)
    2. Case registration + unique reference
    3. Assignment, progress updates, status lifecycle
    4. E-mail (case reference + OTP)
    5. Citizen OTP engine
    6. Standstill detection + escalation review
    7. Non-registration (refusal) records
    8. Permission helpers
    9. Form validation

Design rule: functions that change data COMMIT ONCE at the end, so a case,
its activity and its audit entry are saved together or not at all.
"""

import csv
import io
import re
import secrets
import smtplib
import ssl
from datetime import datetime, timedelta
from email.message import EmailMessage

from flask import Response, current_app, has_request_context, request, url_for
from sqlalchemy import func

from config import (
    ALLOWED_STATUS_TRANSITIONS,
    CASE_STATUSES,
    CONFIGURABLE_SETTINGS,
    ESCALATION_PENDING,
    ESCALATION_REVIEWED,
    FINDING_CLOSED,
    FINDING_OPEN,
    FINDING_RESPONDED,
    FINDING_SEVERITIES,
    INCIDENT_TYPES,
    MANUAL_ACTIVITY_TYPES,
    NON_REGISTRATION_REASONS,
    REFERENCE_CATEGORY_INCIDENT_TYPE,
    REFERENCE_CATEGORY_NON_REGISTRATION,
    ROLE_ADMIN,
    ROLE_AUDITOR,
    ROLE_OFFICER,
    ROLE_SUPERVISOR,
    STATUS_CLOSED,
    STATUS_OPEN,
    SUPERVISOR_ACTIONS,
)
from models import (
    OTP,
    AuditFinding,
    AuditLog,
    Case,
    CaseActivity,
    Escalation,
    NonRegistration,
    ReferenceData,
    SystemSetting,
    User,
    db,
    utcnow,
)


# =============================================================================
# 1. AUDIT TRAIL ENGINE      USER -> ACTION -> CASE -> TIMESTAMP
# =============================================================================
def client_ip():
    """IP address of the browser making the request (None when running seed code)."""
    if has_request_context():
        return request.remote_addr
    return None


def log_audit(user, action, description, case=None):
    """
    Add one audit entry to the database session.

    user  : the User who did it (None = citizen or the system itself)
    action: short code such as CASE_REGISTERED
    case  : the Case involved (or None)

    NOTE: this only ADDS the entry. The caller commits, so the audit entry is
    saved in the same transaction as the change it describes.
    """
    entry = AuditLog(
        user_id=user.id if user else None,
        case_id=case.id if case else None,
        action=action,
        description=description,
        ip_address=client_ip(),
        timestamp=utcnow(),
    )
    db.session.add(entry)
    return entry


def log_case_view(user, case, action="CASE_VIEWED", description=None):
    """
    Record that someone viewed a case. To stop the audit trail filling up
    with page refreshes, the same person viewing the same case is only
    recorded once every 5 minutes.
    """
    window_start = utcnow() - timedelta(minutes=5)
    query = AuditLog.query.filter(
        AuditLog.case_id == case.id,
        AuditLog.action == action,
        AuditLog.timestamp > window_start,
    )
    if user:
        query = query.filter(AuditLog.user_id == user.id)
    else:
        query = query.filter(AuditLog.user_id.is_(None))
    if query.first() is None:
        log_audit(user, action, description or f"Viewed case {case.case_reference}", case)
        db.session.commit()


# =============================================================================
# 2. CASE REGISTRATION + UNIQUE REFERENCE
# =============================================================================
def build_reference(prefix, record_id, year):
    """
    Build a reference such as SAPS-2026-000001.

    The number comes from the database row id, which is unique and never
    re-used (sqlite_autoincrement), so two cases can never get the same
    reference. The reference column also has a UNIQUE constraint as a safety net.

    NOTE: this pattern (insert, flush to get the id, THEN set the final
    reference) is safe for Case, because Case rows are allowed to be updated.
    It must NOT be used for NonRegistration, because that table is immutable
    (see build_non_registration_reference below) - setting the reference
    after the row already exists would count as an UPDATE and be blocked.
    """
    return f"{prefix}-{year}-{record_id:06d}"


def build_non_registration_reference(year):
    """
    Build a unique 'NR-2026-000001' style reference for an immutable
    NonRegistration row WITHOUT needing the row's own database id first
    (that would require an update afterwards, which is blocked).

    Uses the current count of records this year as the next number, and
    falls back to adding a short random suffix in the rare case that number
    is already taken (e.g. if a record was made concurrently).
    """
    year_prefix = f"NR-{year}-"
    existing = NonRegistration.query.filter(
        NonRegistration.reference.like(f"{year_prefix}%")
    ).count()
    candidate = f"{year_prefix}{existing + 1:06d}"
    while NonRegistration.query.filter_by(reference=candidate).first() is not None:
        candidate = f"{year_prefix}{secrets.token_hex(3).upper()}"
    return candidate


def register_case(officer, clean):
    """
    Steps 6 - 8 of the registration flow:
        6. generate the unique case reference
        7. save the case
        8. write the audit record "Case registered"
    'clean' is the validated data from validate_case_form().
    """
    now = utcnow()
    case = Case(
        # Temporary unique placeholder - replaced by the real reference just below.
        case_reference="PENDING-" + secrets.token_hex(8),
        complainant_name=clean["complainant_name"],
        complainant_phone=clean["complainant_phone"],
        complainant_email=clean["complainant_email"],
        incident_date=clean["incident_date"],
        incident_location=clean["incident_location"],
        incident_type=clean["incident_type"],
        statement=clean["statement"],
        status=STATUS_OPEN,
        registered_by_id=officer.id,
        created_at=now,
        updated_at=now,
        last_activity_at=now,
    )
    db.session.add(case)
    db.session.flush()  # asks the database for case.id without finishing the transaction
    case.case_reference = build_reference("SAPS", case.id, now.year)

    add_activity(
        case,
        officer,
        "Case Registered",
        "Case registered at the Community Service Centre. Complainant statement captured.",
        commit=False,
    )
    log_audit(
        officer,
        "CASE_REGISTERED",
        f"Case {case.case_reference} registered ({case.incident_type}) by {officer.full_name}.",
        case,
    )
    db.session.commit()
    return case


# =============================================================================
# 3. ASSIGNMENT, PROGRESS UPDATES, STATUS LIFECYCLE
# =============================================================================
def add_activity(case, user, activity_type, description, commit=True):
    """Add one entry to the case timeline and refresh the case's 'last activity' time."""
    now = utcnow()
    db.session.add(
        CaseActivity(
            case_id=case.id,
            officer_id=user.id,
            activity_type=activity_type,
            description=description,
            created_at=now,
        )
    )
    case.last_activity_at = now
    case.updated_at = now
    if commit:
        db.session.commit()


def record_progress_update(case, user, activity_type, description, commit=True):
    """An officer records progress (e.g. 'CCTV Requested'). Audited as ACTIVITY_ADDED."""
    add_activity(case, user, activity_type, description, commit=False)
    log_audit(
        user,
        "ACTIVITY_ADDED",
        f"{activity_type}: {description}",
        case,
    )
    if commit:
        db.session.commit()


def assign_case(case, officer, actor, note="", commit=True):
    """Assign (or re-assign) a case to an officer. Written to CaseActivity AND AuditLog."""
    previous = case.assigned_officer
    case.assigned_officer_id = officer.id

    text = f"Case assigned to {officer.full_name} by {actor.full_name}."
    if previous:
        text = f"Case re-assigned from {previous.full_name} to {officer.full_name} by {actor.full_name}."
    if note:
        text += f" Note: {note}"

    add_activity(case, actor, "Officer Assigned", text, commit=False)
    log_audit(actor, "CASE_ASSIGNED", text, case)
    if commit:
        db.session.commit()


def allowed_next_statuses(case):
    """The statuses this case is allowed to move to from its current status."""
    return ALLOWED_STATUS_TRANSITIONS.get(case.status, [])


def change_status(case, new_status, user, reason, commit=True):
    """Move a case to another lifecycle status (OPEN / UNDER INVESTIGATION / CLOSED)."""
    if new_status not in CASE_STATUSES:
        raise ValueError("Unknown status.")
    if new_status not in allowed_next_statuses(case):
        raise ValueError(f"A case cannot move from {case.status} to {new_status}.")

    old_status = case.status
    case.status = new_status
    case.closed_at = utcnow() if new_status == STATUS_CLOSED else None

    text = f"Status changed from {old_status} to {new_status} by {user.full_name}. Reason: {reason}"
    add_activity(case, user, "Status Changed", text, commit=False)
    log_audit(user, "STATUS_CHANGED", text, case)
    if commit:
        db.session.commit()


def correct_case_details(case, user, clean):
    """
    Correct complainant/incident details (for example a mistyped e-mail address).
    The ORIGINAL STATEMENT is deliberately not editable. Every change is
    written to the timeline and audit trail with the old and new value.
    Returns True if something changed.
    """
    labels = {
        "complainant_name": "Complainant name",
        "complainant_email": "Complainant e-mail",
        "complainant_phone": "Complainant phone",
        "incident_date": "Incident date",
        "incident_location": "Incident location",
        "incident_type": "Incident type",
    }
    changes = []
    for field, label in labels.items():
        old_value = getattr(case, field)
        new_value = clean[field]
        if (old_value or "") != (new_value or ""):
            changes.append(f"{label}: '{old_value or ''}' changed to '{new_value or ''}'")
            setattr(case, field, new_value)

    if not changes:
        return False

    # If the e-mail changed, any OTP already sent to the old address is cancelled.
    OTP.query.filter_by(case_id=case.id, is_used=False).update({"is_used": True})

    text = "Details corrected by " + user.full_name + ". " + "; ".join(changes) + "."
    add_activity(case, user, "Case Details Corrected", text, commit=False)
    log_audit(user, "CASE_DETAILS_EDITED", text, case)
    db.session.commit()
    return True


# =============================================================================
# 4. E-MAIL  (case reference + OTP)     -  uses Python's built-in smtplib
# =============================================================================
def send_email(to_address, subject, body):
    """
    Send a plain-text e-mail.

    Returns (success, mode):
        (True,  "smtp")     the e-mail was handed to the mail server
        (False, "console")  e-mail is not configured, so it was printed to the
                            PyCharm console instead (development mode only)
        (False, "error")    the mail server refused or could not be reached

    Credentials come from the .env file - they are never written in the code.
    """
    cfg = current_app.config

    if not cfg["MAIL_USERNAME"] or not cfg["MAIL_PASSWORD"]:
        print("\n" + "=" * 70)
        print("E-MAIL NOT CONFIGURED - DEVELOPMENT MODE. Message printed here instead:")
        print(f"To:      {to_address}")
        print(f"Subject: {subject}")
        print("-" * 70)
        print(body)
        print("=" * 70 + "\n")
        return False, "console"

    message = EmailMessage()
    message["From"] = cfg["MAIL_SENDER"] or cfg["MAIL_USERNAME"]
    message["To"] = to_address
    message["Subject"] = subject
    message.set_content(body)

    try:
        if cfg["MAIL_USE_SSL"]:
            server = smtplib.SMTP_SSL(
                cfg["MAIL_SERVER"], cfg["MAIL_PORT"], timeout=15, context=ssl.create_default_context()
            )
        else:
            server = smtplib.SMTP(cfg["MAIL_SERVER"], cfg["MAIL_PORT"], timeout=15)
            server.ehlo()
            if cfg["MAIL_USE_TLS"]:
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
        server.login(cfg["MAIL_USERNAME"], cfg["MAIL_PASSWORD"])
        server.send_message(message)
        server.quit()
        return True, "smtp"
    except Exception as error:  # noqa - we never show technical details to the user
        current_app.logger.error("E-mail could not be sent: %s", error)
        return False, "error"


def email_case_reference(case, user):
    """
    Step 9: e-mail the case reference to the complainant.
    The result is written to the audit trail either way.
    Returns (success, mode).
    """
    portal_url = url_for("citizen_portal", _external=True)
    subject = f"Your case reference: {case.case_reference}"
    body = (
        f"Dear {case.complainant_name},\n\n"
        f"Your report was registered at the police station.\n\n"
        f"    Case reference: {case.case_reference}\n"
        f"    Incident type:  {case.incident_type}\n"
        f"    Registered on:  {case.created_at.strftime('%d %B %Y')}\n\n"
        f"Please keep this reference safe. To check the status of your case, open:\n"
        f"    {portal_url}\n"
        f"Enter the reference above. A one-time PIN (OTP) will then be e-mailed to this address.\n\n"
        f"Regards,\n"
        f"SAPS Case Docket Management System (university prototype - not an official SAPS system)\n"
    )
    success, mode = send_email(case.complainant_email, subject, body)

    if success:
        log_audit(user, "REFERENCE_EMAILED", f"Case reference emailed to {case.complainant_email}.", case)
    elif mode == "console":
        log_audit(
            user,
            "REFERENCE_EMAIL_CONSOLE",
            f"E-mail not configured; reference for {case.complainant_email} printed to server console.",
            case,
        )
    else:
        log_audit(user, "REFERENCE_EMAIL_FAILED", f"Could not e-mail reference to {case.complainant_email}.", case)
    db.session.commit()
    return success, mode


def email_otp(case, code):
    """E-mail the OTP to the address stored on the case. Returns (success, mode)."""
    minutes = current_app.config["OTP_EXPIRY_MINUTES"]
    subject = "Your one-time PIN (OTP) for case tracking"
    body = (
        f"Dear {case.complainant_name},\n\n"
        f"Your one-time PIN to view case {case.case_reference} is:\n\n"
        f"    {code}\n\n"
        f"It expires in {minutes} minutes and can only be used once.\n"
        f"If you did not ask for this PIN, please ignore this e-mail. Never share the PIN with anyone.\n\n"
        f"SAPS Case Docket Management System (university prototype - not an official SAPS system)\n"
    )
    return send_email(case.complainant_email, subject, body)


# =============================================================================
# 5. CITIZEN OTP ENGINE
# =============================================================================
def issue_otp(case):
    """
    Create a new OTP for the case.

    Returns (code, status):
        (code, "created")   a new OTP exists; 'code' is the plain 6 digits to e-mail
        (None, "cooldown")  an OTP was requested very recently, so no new one is made

    Only a HASH of the code is stored in the database.
    """
    cooldown = current_app.config["OTP_RESEND_COOLDOWN_SECONDS"]
    latest = (
        OTP.query.filter_by(case_id=case.id)
        .order_by(OTP.created_at.desc(), OTP.id.desc())
        .first()
    )
    if latest and (utcnow() - latest.created_at).total_seconds() < cooldown:
        return None, "cooldown"

    # Cancel any older, still-unused OTPs for this case.
    OTP.query.filter_by(case_id=case.id, is_used=False).update({"is_used": True})

    code = f"{secrets.randbelow(1_000_000):06d}"  # cryptographically secure 6 digits
    otp = OTP(
        case_id=case.id,
        email=case.complainant_email,  # ALWAYS the e-mail stored on the case
        created_at=utcnow(),
        expires_at=utcnow() + timedelta(minutes=current_app.config["OTP_EXPIRY_MINUTES"]),
    )
    otp.set_code(code)
    db.session.add(otp)
    db.session.commit()
    return code, "created"


def verify_otp(case, entered_code):
    """
    Check the OTP a citizen typed in.

    Returns (result, attempts_left) where result is one of:
        "ok"       correct - the OTP is now used up
        "invalid"  wrong code, attempts remain
        "locked"   too many wrong attempts - a new OTP must be requested
        "expired"  the OTP was too old
        "none"     no usable OTP exists (never requested, already used, or replaced)
    """
    max_attempts = current_app.config["OTP_MAX_ATTEMPTS"]
    otp = (
        OTP.query.filter_by(case_id=case.id, is_used=False)
        .order_by(OTP.created_at.desc(), OTP.id.desc())
        .first()
    )
    if otp is None:
        return "none", 0

    if otp.is_expired:
        otp.is_used = True
        db.session.commit()
        return "expired", 0

    if otp.attempts >= max_attempts:
        otp.is_used = True
        db.session.commit()
        return "locked", 0

    otp.attempts += 1
    if otp.check_code(entered_code):
        otp.is_used = True  # single use
        db.session.commit()
        return "ok", 0

    if otp.attempts >= max_attempts:
        otp.is_used = True
        db.session.commit()
        return "locked", 0

    db.session.commit()
    return "invalid", max_attempts - otp.attempts


# =============================================================================
# 6. STANDSTILL DETECTION + ESCALATION REVIEW
# =============================================================================
def run_standstill_check():
    """
    Find cases that have had NO activity for CASE_STANDSTILL_DAYS and create an
    Escalation (attention record) for each one.

    - A stagnant case is NEVER closed automatically.
    - A case that already has a PENDING escalation is not flagged twice.
    - When a supervisor reviews an escalation, that review counts as activity,
      so the clock restarts and the case "continues".

    Returns the list of new Escalation objects.
    """
    days = current_app.config["CASE_STANDSTILL_DAYS"]
    statuses = current_app.config["CASE_STANDSTILL_STATUSES"]
    cutoff = utcnow() - timedelta(days=days)

    stale_cases = Case.query.filter(
        Case.status.in_(statuses),
        Case.last_activity_at < cutoff,
    ).all()

    created = []
    for case in stale_cases:
        if case.pending_escalation is not None:
            continue
        idle_days = (utcnow() - case.last_activity_at).days
        reason = (
            f"Possible standstill: no activity recorded for {idle_days} day(s) "
            f"(threshold: {days} day(s)). Case status: {case.status}."
        )
        escalation = Escalation(case_id=case.id, reason=reason, status=ESCALATION_PENDING)
        db.session.add(escalation)
        log_audit(None, "ESCALATION_CREATED", reason, case)
        created.append(escalation)

    if created:
        db.session.commit()
    return created


def review_escalation(escalation, supervisor, action, notes, reassign_officer=None):
    """A supervisor reviews an escalation. The action is recorded in the audit trail."""
    case = escalation.case
    escalation.status = ESCALATION_REVIEWED
    escalation.reviewed_by_id = supervisor.id
    escalation.reviewed_at = utcnow()
    escalation.supervisor_action = action
    escalation.supervisor_notes = notes

    text = f"Escalation reviewed by {supervisor.full_name}. Action: {action}. Notes: {notes}"

    if reassign_officer is not None and reassign_officer.id != case.assigned_officer_id:
        previous = case.assigned_officer
        case.assigned_officer_id = reassign_officer.id
        text += (
            f" Case re-assigned from {previous.full_name} to {reassign_officer.full_name}."
            if previous
            else f" Case assigned to {reassign_officer.full_name}."
        )

    # The review is activity on the case, so it also restarts the standstill clock.
    add_activity(case, supervisor, "Supervisor Review", text, commit=False)
    log_audit(supervisor, "ESCALATION_REVIEWED", text, case)
    db.session.commit()


# =============================================================================
# 7. NON-REGISTRATION (REFUSAL) RECORDS
# =============================================================================
def record_non_registration(officer, clean):
    """Save a Non-Registration Record. It can never be edited or deleted afterwards."""
    now = utcnow()
    # The reference is generated BEFORE the row is created (see the note on
    # build_non_registration_reference) so this record only ever needs ONE
    # insert and is never updated afterwards.
    record = NonRegistration(
        reference=build_non_registration_reference(now.year),
        officer_id=officer.id,
        complainant_name=clean["complainant_name"],
        complainant_contact=clean["complainant_contact"],
        incident_summary=clean["incident_summary"],
        reason_code=clean["reason_code"],
        justification=clean["justification"],
        related_case_id=clean["related_case"].id if clean["related_case"] else None,
        created_at=now,
    )
    db.session.add(record)
    db.session.flush()

    log_audit(
        officer,
        "NON_REGISTRATION_RECORDED",
        f"Report NOT registered ({record.reference}). Reason {record.reason_code}: "
        f"{NON_REGISTRATION_REASONS[record.reason_code]}. Justification: {record.justification}",
        clean["related_case"],
    )
    db.session.commit()
    return record


# =============================================================================
# 8. PERMISSION HELPERS
# =============================================================================
def officer_can_access_case(user, case):
    """An officer may open a case they registered or that is assigned to them."""
    return user.is_officer and (
        case.registered_by_id == user.id or case.assigned_officer_id == user.id
    )


def citizen_officer_name(officer):
    """A citizen-safe version of the officer's name, e.g. 'T. Mokoena'."""
    if officer is None:
        return None
    if not current_app.config["SHOW_OFFICER_NAME_TO_CITIZEN"]:
        return "An officer has been assigned"
    parts = officer.full_name.split()
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0][0]}. {parts[-1]}"


def local_today():
    """Today's date in South African time (used to stop future incident dates)."""
    return (utcnow() + timedelta(hours=current_app.config["DISPLAY_UTC_OFFSET_HOURS"])).date()


# =============================================================================
# 9. FORM VALIDATION   (the server checks everything - never trust the browser)
# =============================================================================
EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
PHONE_PATTERN = re.compile(r"^\+?[0-9 ()\-]{9,20}$")


def validate_case_form(form, include_statement=True):
    """Validate the Register Case form (and the Edit Details form). Returns (clean, errors)."""
    errors = []
    clean = {}

    name = form.get("complainant_name", "").strip()
    if len(name) < 2 or len(name) > 120:
        errors.append("Complainant full name is required (2 to 120 characters).")
    clean["complainant_name"] = name

    email = form.get("complainant_email", "").strip().lower()
    if not EMAIL_PATTERN.match(email) or len(email) > 120:
        errors.append("A valid complainant e-mail address is required - it is used for the OTP.")
    clean["complainant_email"] = email

    phone = form.get("complainant_phone", "").strip()
    if phone and not PHONE_PATTERN.match(phone):
        errors.append("Contact number may only contain digits, spaces, +, - and brackets (9 to 20 characters).")
    clean["complainant_phone"] = phone or None

    date_text = form.get("incident_date", "").strip()
    incident_date = None
    try:
        incident_date = datetime.strptime(date_text, "%Y-%m-%d").date()
        if incident_date > local_today():
            errors.append("Incident date cannot be in the future.")
        elif incident_date.year < 1990:
            errors.append("Incident date is too far in the past.")
    except ValueError:
        errors.append("Please enter a valid incident date.")
    clean["incident_date"] = incident_date

    location = form.get("incident_location", "").strip()
    if len(location) < 3 or len(location) > 255:
        errors.append("Incident location is required (3 to 255 characters).")
    clean["incident_location"] = location

    incident_type = form.get("incident_type", "").strip()
    if incident_type not in get_incident_types():
        errors.append("Please choose an incident type from the list.")
    clean["incident_type"] = incident_type

    if include_statement:
        statement = form.get("statement", "").strip()
        if len(statement) < 20:
            errors.append("The statement / details of the report must be at least 20 characters.")
        elif len(statement) > 5000:
            errors.append("The statement may not be longer than 5000 characters.")
        clean["statement"] = statement

    return clean, errors


def validate_progress_form(case, form):
    """Validate the Add Progress Update form. Returns (clean, errors)."""
    errors = []
    clean = {}

    activity_type = form.get("activity_type", "").strip()
    if activity_type not in MANUAL_ACTIVITY_TYPES:
        errors.append("Please choose an activity type from the list.")
    clean["activity_type"] = activity_type

    description = form.get("description", "").strip()
    if len(description) < 5:
        errors.append("Please describe the activity (at least 5 characters).")
    elif len(description) > 2000:
        errors.append("The description may not be longer than 2000 characters.")
    clean["description"] = description

    new_status = form.get("new_status", "").strip()
    if new_status:
        if new_status not in allowed_next_statuses(case):
            errors.append(f"A case that is {case.status} cannot be changed to {new_status}.")
    elif case.is_closed:
        errors.append("This case is CLOSED. Re-open it (set status to UNDER INVESTIGATION) to add an update.")
    clean["new_status"] = new_status or None

    return clean, errors


def validate_non_registration_form(form):
    """Validate the Non-Registration form. A blank refusal is NOT allowed."""
    errors = []
    clean = {}

    name = form.get("complainant_name", "").strip()
    if len(name) < 2 or len(name) > 120:
        errors.append("The name of the person who reported the matter is required.")
    clean["complainant_name"] = name

    contact = form.get("complainant_contact", "").strip()
    if len(contact) > 120:
        errors.append("Contact details may not be longer than 120 characters.")
    clean["complainant_contact"] = contact or None

    summary = form.get("incident_summary", "").strip()
    if len(summary) < 10:
        errors.append("Please summarise what was reported (at least 10 characters).")
    elif len(summary) > 2000:
        errors.append("The summary may not be longer than 2000 characters.")
    clean["incident_summary"] = summary

    reason_code = form.get("reason_code", "").strip()
    if reason_code not in get_non_registration_reasons():
        errors.append("You must choose a coded reason for not registering the report.")
    clean["reason_code"] = reason_code

    justification = form.get("justification", "").strip()
    if len(justification) < 30:
        errors.append("A written justification of at least 30 characters is required.")
    elif len(justification) > 3000:
        errors.append("The justification may not be longer than 3000 characters.")
    clean["justification"] = justification

    related_reference = form.get("related_case_reference", "").strip().upper()
    related_case = None
    if related_reference:
        related_case = Case.query.filter_by(case_reference=related_reference).first()
        if related_case is None:
            errors.append(f"No case with the reference {related_reference} exists.")
    elif reason_code == "NR03":
        errors.append("For a duplicate report, enter the reference of the existing case.")
    clean["related_case"] = related_case

    return clean, errors


def validate_escalation_review_form(form, officer_ids):
    """Validate the supervisor's escalation review. Returns (clean, errors)."""
    errors = []
    clean = {}

    action = form.get("supervisor_action", "").strip()
    if action not in SUPERVISOR_ACTIONS:
        errors.append("Please choose the supervisory action that was taken.")
    clean["supervisor_action"] = action

    notes = form.get("supervisor_notes", "").strip()
    if len(notes) < 10:
        errors.append("Supervisor notes are required (at least 10 characters).")
    elif len(notes) > 2000:
        errors.append("Supervisor notes may not be longer than 2000 characters.")
    clean["supervisor_notes"] = notes

    reassign_to = form.get("reassign_to", "").strip()
    clean["reassign_to"] = None
    if reassign_to:
        if reassign_to.isdigit() and int(reassign_to) in officer_ids:
            clean["reassign_to"] = int(reassign_to)
        else:
            errors.append("The officer chosen for re-assignment is not valid.")

    return clean, errors


# =============================================================================
# 10. SYSTEM ADMINISTRATION - USER MANAGEMENT
#     (Create users, disable users, reset passwords, change user roles)
# =============================================================================
USER_ROLES = [ROLE_OFFICER, ROLE_SUPERVISOR, ROLE_AUDITOR, ROLE_ADMIN]

# Characters chosen to avoid look-alikes (0/O, 1/l/I) when a temporary
# password has to be read out loud or copied from a screen.
_TEMP_PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789"


def generate_temp_password(length=10):
    """A random temporary password that always satisfies the complexity rule (letters + numbers)."""
    while True:
        candidate = "".join(secrets.choice(_TEMP_PASSWORD_ALPHABET) for _ in range(length))
        if re.search(r"[A-Za-z]", candidate) and re.search(r"\d", candidate):
            return candidate


def validate_user_form(form, editing_user_id=None):
    """Validate the admin's Create/Edit User form. Returns (clean, errors)."""
    errors = []
    clean = {}

    full_name = form.get("full_name", "").strip()
    if len(full_name) < 2 or len(full_name) > 120:
        errors.append("Full name is required (2 to 120 characters).")
    clean["full_name"] = full_name

    username = form.get("username", "").strip().lower()
    if not re.match(r"^[a-z0-9._\-]{3,50}$", username):
        errors.append("Username must be 3 to 50 characters (letters, numbers, dots, dashes or underscores only).")
    else:
        existing = User.query.filter(func.lower(User.username) == username).first()
        if existing and existing.id != editing_user_id:
            errors.append(f"The username '{username}' is already in use.")
    clean["username"] = username

    email = form.get("email", "").strip().lower()
    if not EMAIL_PATTERN.match(email) or len(email) > 120:
        errors.append("A valid e-mail address is required.")
    else:
        existing = User.query.filter(func.lower(User.email) == email).first()
        if existing and existing.id != editing_user_id:
            errors.append(f"The e-mail address '{email}' is already in use.")
    clean["email"] = email

    role = form.get("role", "").strip().upper()
    if role not in USER_ROLES:
        errors.append("Please choose a valid role for this account.")
    clean["role"] = role

    return clean, errors


def create_user(actor, clean):
    """
    Admin creates a new user account with a random temporary password.
    Returns (user, temp_password) - the plain password is only ever available
    here, at creation time, so the admin can hand it to the new user.
    """
    temp_password = generate_temp_password()
    user = User(
        full_name=clean["full_name"],
        username=clean["username"],
        email=clean["email"],
        role=clean["role"],
        is_active=True,
    )
    user.set_password(temp_password)
    db.session.add(user)
    db.session.flush()  # so user.id exists for the audit entry
    log_audit(
        actor, "USER_CREATED",
        f"{actor.full_name} created the account '{user.username}' ({user.role_label}).",
    )
    db.session.commit()
    return user, temp_password


def set_user_active(user, active, actor):
    """Enable or disable a user account. A disabled user cannot log in."""
    user.is_active = active
    action = "USER_ENABLED" if active else "USER_DISABLED"
    verb = "enabled" if active else "disabled"
    log_audit(actor, action, f"{actor.full_name} {verb} the account '{user.username}'.")
    db.session.commit()


def change_user_role(user, new_role, actor):
    """Change a user's role (e.g. promote an Officer to Supervisor)."""
    old_role = user.role
    user.role = new_role
    log_audit(
        actor, "USER_ROLE_CHANGED",
        f"{actor.full_name} changed the role of '{user.username}' from {old_role} to {new_role}.",
    )
    db.session.commit()


def reset_user_password(user, actor):
    """
    Reset a user's password to a new random temporary password.
    Returns the plain password (shown to the admin ONCE - it is never stored
    or logged anywhere; only its hash is kept).
    """
    temp_password = generate_temp_password()
    user.set_password(temp_password)
    log_audit(actor, "PASSWORD_RESET_BY_ADMIN", f"{actor.full_name} reset the password for '{user.username}'.")
    db.session.commit()
    return temp_password


# =============================================================================
# 11. SYSTEM ADMINISTRATION - SYSTEM CONFIGURATION
# =============================================================================
def get_setting(key, default=None, cast=str):
    """Read one admin-configured override from the database (or `default` if never set)."""
    row = db.session.get(SystemSetting, key)
    if row is None:
        return default
    try:
        if cast is bool:
            return row.value.strip().lower() in ("1", "true", "yes", "on")
        return cast(row.value)
    except (TypeError, ValueError):
        return default


def set_setting(key, value, actor=None):
    """Create or update one admin-configured override."""
    row = db.session.get(SystemSetting, key)
    if row is None:
        row = SystemSetting(key=key, value=str(value))
        db.session.add(row)
    else:
        row.value = str(value)
    row.updated_at = utcnow()
    row.updated_by_id = actor.id if actor else None
    return row


def apply_dynamic_settings(app):
    """
    Copy every admin-configured override on top of the .env-based Config values.
    Called once per request (see app.py) so the whole application - standstill
    detection, OTP rules, login lockout, pagination, and so on - always uses
    the latest value without every call site having to know where it came from.
    """
    for key, meta in CONFIGURABLE_SETTINGS.items():
        cast = bool if meta["type"] == "bool" else int
        value = get_setting(key, default=None, cast=cast)
        if value is not None:
            app.config[key] = value


def validate_configuration_form(form):
    """Validate the admin's System Configuration form. Returns (clean, errors)."""
    errors = []
    clean = {}
    for key, meta in CONFIGURABLE_SETTINGS.items():
        if meta["type"] == "bool":
            clean[key] = form.get(key) is not None  # checkbox: present = checked
            continue
        raw = form.get(key, "").strip()
        try:
            value = int(raw)
            if value < meta["min"] or value > meta["max"]:
                errors.append(f"{meta['label']} must be between {meta['min']} and {meta['max']}.")
            else:
                clean[key] = value
        except ValueError:
            errors.append(f"{meta['label']} must be a whole number.")
    return clean, errors


# =============================================================================
# 12. SYSTEM ADMINISTRATION - REFERENCE DATA
# =============================================================================
def get_incident_types():
    """Active incident types, from the database if an admin has configured any, else the built-in defaults."""
    rows = (
        ReferenceData.query.filter_by(category=REFERENCE_CATEGORY_INCIDENT_TYPE, is_active=True)
        .order_by(ReferenceData.sort_order, ReferenceData.label)
        .all()
    )
    return [r.label for r in rows] if rows else list(INCIDENT_TYPES)


def get_non_registration_reasons():
    """Active coded non-registration reasons, as an ordered {code: label} dict."""
    rows = (
        ReferenceData.query.filter_by(category=REFERENCE_CATEGORY_NON_REGISTRATION, is_active=True)
        .order_by(ReferenceData.sort_order, ReferenceData.label)
        .all()
    )
    return {r.code: r.label for r in rows} if rows else dict(NON_REGISTRATION_REASONS)


def seed_reference_data():
    """Populate reference_data from the built-in defaults, once, so the admin has something to edit."""
    if ReferenceData.query.count() > 0:
        return
    for i, label in enumerate(INCIDENT_TYPES):
        db.session.add(ReferenceData(category=REFERENCE_CATEGORY_INCIDENT_TYPE, label=label,
                                      is_active=True, sort_order=i))
    for i, (code, label) in enumerate(NON_REGISTRATION_REASONS.items()):
        db.session.add(ReferenceData(category=REFERENCE_CATEGORY_NON_REGISTRATION, code=code,
                                      label=label, is_active=True, sort_order=i))
    db.session.commit()


def add_reference_data(category, code, label, actor):
    """Add one reference-data item (a new incident type or coded reason)."""
    item = ReferenceData(category=category, code=code or None, label=label, is_active=True,
                          created_by_id=actor.id if actor else None)
    db.session.add(item)
    db.session.flush()
    log_audit(actor, "REFERENCE_DATA_ADDED", f"{actor.full_name} added '{label}' to {category}.")
    db.session.commit()
    return item


def toggle_reference_data(item, actor):
    """Activate/deactivate a reference-data item. Deactivating hides it from new forms without deleting history."""
    item.is_active = not item.is_active
    verb = "reactivated" if item.is_active else "deactivated"
    log_audit(actor, "REFERENCE_DATA_UPDATED", f"{actor.full_name} {verb} '{item.label}' ({item.category}).")
    db.session.commit()


# =============================================================================
# 13. AUDIT FINDING WORKFLOW  (AUDITOR -> SUPERVISOR -> AUDITOR)
# =============================================================================
def validate_finding_form(form):
    """Validate the auditor's 'Flag for Review' form. Returns (clean, errors)."""
    errors = []
    clean = {}

    title = form.get("title", "").strip()
    if len(title) < 5 or len(title) > 150:
        errors.append("Finding title is required (5 to 150 characters).")
    clean["title"] = title

    severity = form.get("severity", "").strip().title()
    if severity not in FINDING_SEVERITIES:
        errors.append("Please choose a severity.")
    clean["severity"] = severity

    description = form.get("description", "").strip()
    if len(description) < 20:
        errors.append("Please describe the finding in at least 20 characters.")
    elif len(description) > 3000:
        errors.append("The description may not be longer than 3000 characters.")
    clean["description"] = description

    return clean, errors


def build_finding_reference(record_id, year):
    return f"AF-{year}-{record_id:05d}"


def create_finding(case, auditor, clean):
    """AUDITOR flags a case for review, creating a new open finding."""
    now = utcnow()
    finding = AuditFinding(
        case_id=case.id,
        reference="PENDING-" + secrets.token_hex(8),
        title=clean["title"],
        description=clean["description"],
        severity=clean["severity"],
        status=FINDING_OPEN,
        created_by_id=auditor.id,
        created_at=now,
    )
    db.session.add(finding)
    db.session.flush()  # so finding.id exists for the reference
    finding.reference = build_finding_reference(finding.id, now.year)
    log_audit(
        auditor, "AUDIT_FINDING_CREATED",
        f"{auditor.full_name} flagged case {case.case_reference} for review "
        f"({finding.reference}, severity {finding.severity}): {clean['title']}",
        case,
    )
    db.session.commit()
    return finding


def respond_to_finding(finding, supervisor, response_text):
    """SUPERVISOR responds to / investigates an open finding."""
    finding.response_text = response_text
    finding.responded_by_id = supervisor.id
    finding.responded_at = utcnow()
    finding.status = FINDING_RESPONDED
    log_audit(
        supervisor, "AUDIT_FINDING_RESPONDED",
        f"{supervisor.full_name} responded to finding {finding.reference} on case {finding.case.case_reference}.",
        finding.case,
    )
    db.session.commit()


def close_finding(finding, auditor, review_notes):
    """AUDITOR reviews the supervisor's response and closes the finding."""
    finding.review_notes = review_notes
    finding.reviewed_by_id = auditor.id
    finding.closed_at = utcnow()
    finding.status = FINDING_CLOSED
    log_audit(
        auditor, "AUDIT_FINDING_CLOSED",
        f"{auditor.full_name} reviewed the response and closed finding {finding.reference}.",
        finding.case,
    )
    db.session.commit()


def reopen_finding(finding, auditor, review_notes):
    """AUDITOR reviews the response but is not satisfied - sends it back for a further response."""
    finding.review_notes = review_notes
    finding.reviewed_by_id = auditor.id
    finding.responded_at = None
    finding.status = FINDING_OPEN
    log_audit(
        auditor, "AUDIT_FINDING_REOPENED",
        f"{auditor.full_name} requested a further response for finding {finding.reference}: {review_notes}",
        finding.case,
    )
    db.session.commit()


# =============================================================================
# 14. CSV EXPORTS  (auditor 'Export Report', admin audit-trail export)
# =============================================================================
def build_case_report_csv(case):
    """A full, read-only case report (details + timeline + audit trail) as a CSV download."""
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["Case report", case.case_reference])
    writer.writerow(["Generated", utcnow().isoformat() + "Z"])
    writer.writerow([])
    writer.writerow(["Field", "Value"])
    writer.writerow(["Case reference", case.case_reference])
    writer.writerow(["Status", case.status])
    writer.writerow(["Complainant", case.complainant_name])
    writer.writerow(["Complainant e-mail", case.complainant_email])
    writer.writerow(["Complainant phone", case.complainant_phone or ""])
    writer.writerow(["Incident type", case.incident_type])
    writer.writerow(["Incident date", case.incident_date.isoformat()])
    writer.writerow(["Incident location", case.incident_location])
    writer.writerow(["Registered by", case.registered_by.full_name if case.registered_by else ""])
    writer.writerow(["Assigned officer", case.assigned_officer.full_name if case.assigned_officer else ""])
    writer.writerow(["Registered at", case.created_at.isoformat()])
    writer.writerow(["Last activity", case.last_activity_at.isoformat()])
    if case.closed_at:
        writer.writerow(["Closed at", case.closed_at.isoformat()])

    writer.writerow([])
    writer.writerow(["Timeline (oldest first)"])
    writer.writerow(["When", "Activity type", "Description", "Recorded by"])
    for a in reversed(case.activities):
        writer.writerow([a.created_at.isoformat(), a.activity_type, a.description,
                          a.officer.full_name if a.officer else ""])

    writer.writerow([])
    writer.writerow(["Audit trail (oldest first)"])
    writer.writerow(["When", "Actor", "Action", "Description"])
    for e in reversed(case.audit_logs):
        writer.writerow([e.timestamp.isoformat(), e.actor_name, e.action, e.description])

    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = f"attachment; filename={case.case_reference}-report.csv"
    return response


def build_audit_log_csv(entries):
    """A CSV export of a set of audit-trail entries (used by System Maintenance)."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["When", "Actor", "Action", "Case", "IP address", "Description"])
    for e in entries:
        writer.writerow([
            e.timestamp.isoformat(), e.actor_name, e.action,
            e.case.case_reference if e.case else "", e.ip_address or "", e.description,
        ])
    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=audit-trail-export.csv"
    return response

