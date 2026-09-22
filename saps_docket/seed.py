"""
seed.py  -  SHARED FILE (demo data)

Creates DEMO users and a few DEMO cases the first time the application starts,
so the system can be demonstrated immediately.

!!! DEMO CREDENTIALS ONLY - never use these passwords in a real system !!!

    Officer     username: officer1      password: password123
    Officer     username: officer2      password: password123
    Officer     username: officer3      password: password123
    Supervisor  username: supervisor1   password: password123

The demo cases use example.com e-mail addresses. To test the OTP with a REAL
inbox, register a new case yourself using your own e-mail address.
"""

from datetime import timedelta

from config import (
    ROLE_OFFICER,
    ROLE_SUPERVISOR,
    STATUS_CLOSED,
    STATUS_OPEN,
    STATUS_UNDER_INVESTIGATION,
)
from models import AuditLog, Case, CaseActivity, NonRegistration, User, db, utcnow
from services import build_non_registration_reference, build_reference, run_standstill_check

DEMO_PASSWORD = "password123"


def seed_users():
    """Create the demo users (only if there are no users yet)."""
    if User.query.count() > 0:
        return

    people = [
        ("Thandi Mokoena", "officer1", "officer1@example.com", ROLE_OFFICER),
        ("Sipho Dlamini", "officer2", "officer2@example.com", ROLE_OFFICER),
        ("Lerato Naidoo", "officer3", "officer3@example.com", ROLE_OFFICER),
        ("Captain Pieter Venter", "supervisor1", "supervisor1@example.com", ROLE_SUPERVISOR),
    ]
    for full_name, username, email, role in people:
        user = User(full_name=full_name, username=username, email=email, role=role, is_active=True)
        user.set_password(DEMO_PASSWORD)  # stored as a hash, never as plain text
        db.session.add(user)
    db.session.commit()


def _make_case(days_ago, registered_by, complainant, incident, status, assigned_to=None):
    """
    Build one demo case with a back-dated history.
    (Audit rows cannot be edited afterwards, so the dates are set when they are created.)
    """
    created = utcnow() - timedelta(days=days_ago)
    case = Case(
        case_reference="PENDING-" + complainant["name"].replace(" ", ""),
        complainant_name=complainant["name"],
        complainant_email=complainant["email"],
        complainant_phone=complainant["phone"],
        incident_date=(created - timedelta(days=1)).date(),
        incident_location=incident["location"],
        incident_type=incident["type"],
        statement=incident["statement"],
        status=STATUS_OPEN,
        registered_by_id=registered_by.id,
        created_at=created,
        updated_at=created,
        last_activity_at=created,
    )
    db.session.add(case)
    db.session.flush()
    case.case_reference = build_reference("SAPS", case.id, created.year)

    def activity(when, officer, kind, text):
        db.session.add(
            CaseActivity(case_id=case.id, officer_id=officer.id, activity_type=kind,
                         description=text, created_at=when)
        )
        case.last_activity_at = when
        case.updated_at = when

    def audit(when, officer, action, text):
        db.session.add(
            AuditLog(user_id=officer.id, case_id=case.id, action=action,
                     description=text, timestamp=when)
        )

    activity(created, registered_by, "Case Registered",
             "Case registered at the Community Service Centre. Complainant statement captured.")
    audit(created, registered_by, "CASE_REGISTERED",
          f"Case {case.case_reference} registered ({case.incident_type}) by {registered_by.full_name}.")
    return case, activity, audit


def seed_demo_cases():
    """Create demo cases (only if there are no cases yet)."""
    if Case.query.count() > 0:
        return

    officer1 = User.query.filter_by(username="officer1").first()
    officer2 = User.query.filter_by(username="officer2").first()
    officer3 = User.query.filter_by(username="officer3").first()
    if not (officer1 and officer2 and officer3):
        return
    now = utcnow()

    # ---- Case 1: UNDER INVESTIGATION but stagnant -> will be escalated ------
    case, activity, audit = _make_case(
        12, officer1,
        {"name": "Nomsa Khumalo", "email": "nomsa.khumalo@example.com", "phone": "082 555 0101"},
        {"type": "Burglary", "location": "14 Marine Parade, Durban",
         "statement": "I came home from work and found the back window broken and my laptop and television missing."},
        STATUS_UNDER_INVESTIGATION,
    )
    when = now - timedelta(days=12)
    case.assigned_officer_id = officer2.id
    text = f"Case assigned to {officer2.full_name} by {officer1.full_name}."
    activity(when + timedelta(minutes=10), officer1, "Officer Assigned", text)
    audit(when + timedelta(minutes=10), officer1, "CASE_ASSIGNED", text)
    text = f"Status changed from OPEN to UNDER INVESTIGATION by {officer2.full_name}. Reason: Investigation started."
    activity(now - timedelta(days=11), officer2, "Status Changed", text)
    audit(now - timedelta(days=11), officer2, "STATUS_CHANGED", text)
    case.status = STATUS_UNDER_INVESTIGATION
    text = "CCTV Requested: Requested footage from the shop opposite the house."
    activity(now - timedelta(days=10), officer2, "CCTV Requested", "Requested footage from the shop opposite the house.")
    audit(now - timedelta(days=10), officer2, "ACTIVITY_ADDED", text)

    # ---- Case 2: OPEN and not yet assigned ---------------------------------
    case, activity, audit = _make_case(
        1, officer1,
        {"name": "Ayesha Patel", "email": "ayesha.patel@example.com", "phone": "071 555 0142"},
        {"type": "Theft", "location": "Workshop Shopping Centre, Durban CBD",
         "statement": "My handbag containing my purse and phone was stolen from my trolley while I was shopping."},
        STATUS_OPEN,
    )

    # ---- Case 3: UNDER INVESTIGATION, recently updated ---------------------
    case, activity, audit = _make_case(
        4, officer2,
        {"name": "Johan Botha", "email": "johan.botha@example.com", "phone": "083 555 0177"},
        {"type": "Assault", "location": "Corner of West Street and Smith Street",
         "statement": "I was pushed and punched by an unknown man outside a restaurant and needed medical treatment."},
        STATUS_UNDER_INVESTIGATION,
    )
    case.assigned_officer_id = officer1.id
    text = f"Case assigned to {officer1.full_name} by {officer2.full_name}."
    activity(now - timedelta(days=4) + timedelta(minutes=5), officer2, "Officer Assigned", text)
    audit(now - timedelta(days=4) + timedelta(minutes=5), officer2, "CASE_ASSIGNED", text)
    text = f"Status changed from OPEN to UNDER INVESTIGATION by {officer1.full_name}. Reason: Complainant interviewed."
    activity(now - timedelta(days=3), officer1, "Status Changed", text)
    audit(now - timedelta(days=3), officer1, "STATUS_CHANGED", text)
    case.status = STATUS_UNDER_INVESTIGATION
    activity(now - timedelta(days=1), officer1, "Witness Interview Completed",
             "One witness from the restaurant has given a statement.")
    audit(now - timedelta(days=1), officer1, "ACTIVITY_ADDED",
          "Witness Interview Completed: One witness from the restaurant has given a statement.")

    # ---- Case 4: CLOSED ----------------------------------------------------
    case, activity, audit = _make_case(
        9, officer1,
        {"name": "Themba Zulu", "email": "themba.zulu@example.com", "phone": "084 555 0190"},
        {"type": "Vehicle theft / hijacking", "location": "Umlazi, Section V",
         "statement": "My car was taken from outside my house overnight. It was recovered two days later."},
        STATUS_OPEN,
    )
    case.assigned_officer_id = officer1.id
    text = f"Case assigned to {officer1.full_name} by {officer1.full_name}."
    activity(now - timedelta(days=9) + timedelta(minutes=5), officer1, "Officer Assigned", text)
    audit(now - timedelta(days=9) + timedelta(minutes=5), officer1, "CASE_ASSIGNED", text)
    activity(now - timedelta(days=5), officer1, "Follow-up Completed",
             "Vehicle recovered and returned to the owner.")
    audit(now - timedelta(days=5), officer1, "ACTIVITY_ADDED",
          "Follow-up Completed: Vehicle recovered and returned to the owner.")
    text = f"Status changed from OPEN to CLOSED by {officer1.full_name}. Reason: Vehicle recovered, complainant satisfied."
    activity(now - timedelta(days=3), officer1, "Status Changed", text)
    audit(now - timedelta(days=3), officer1, "STATUS_CHANGED", text)
    case.status = STATUS_CLOSED
    case.closed_at = now - timedelta(days=3)

    # ---- One demo Non-Registration Record -----------------------------------
    record_created_at = now - timedelta(days=2)
    record = NonRegistration(
        reference=build_non_registration_reference(record_created_at.year),
        officer_id=officer2.id,
        complainant_name="Kevin Moodley",
        complainant_contact="kevin.moodley@example.com",
        incident_summary="Reported a dispute with a neighbour about a boundary wall.",
        reason_code="NR01",
        justification="The matter is a civil dispute about property boundaries. The complainant was "
                      "advised to approach the municipality and an attorney. No criminal offence was reported.",
        created_at=record_created_at,
    )
    db.session.add(record)
    db.session.flush()
    db.session.add(
        AuditLog(user_id=officer2.id, case_id=None, action="NON_REGISTRATION_RECORDED",
                 description=f"Report NOT registered ({record.reference}). Reason NR01. "
                             f"Justification recorded by {officer2.full_name}.",
                 timestamp=now - timedelta(days=2))
    )

    db.session.commit()

    # Case 1 has been quiet for 10 days, so the standstill check flags it.
    run_standstill_check()


def seed_database():
    seed_users()
    seed_demo_cases()
