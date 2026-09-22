"""
models.py  -  SHARED FILE (database design used by both team members)

Every table in the database is described here as a Python class.

TABLES
    users               -> User
    cases               -> Case
    case_activities     -> CaseActivity
    audit_logs          -> AuditLog          (append-only / immutable)
    otps                -> OTP
    escalations         -> Escalation
    non_registrations   -> NonRegistration   (append-only / immutable)

The audit trail idea:   USER -> ACTION -> CASE -> TIMESTAMP
"""

from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from werkzeug.security import check_password_hash, generate_password_hash

from config import (
    CASE_STATUSES,
    ESCALATION_PENDING,
    NON_REGISTRATION_REASONS,
    ROLE_OFFICER,
    ROLE_SUPERVISOR,
    STATUS_CLOSED,
)

db = SQLAlchemy()


def utcnow():
    """Current time in UTC (stored without timezone info so SQLite is happy)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ImmutableRecordError(Exception):
    """Raised if any code tries to change or delete a record that must be permanent."""


# =============================================================================
# USER  (CSC Officer or CSC Supervisor)
# =============================================================================
class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    username = db.Column(db.String(50), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)   # never the real password
    role = db.Column(db.String(20), nullable=False)             # OFFICER or SUPERVISOR
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    last_login_at = db.Column(db.DateTime, nullable=True)

    # Cases this user registered / cases assigned to this user.
    registered_cases = db.relationship(
        "Case", foreign_keys="Case.registered_by_id", back_populates="registered_by"
    )
    assigned_cases = db.relationship(
        "Case", foreign_keys="Case.assigned_officer_id", back_populates="assigned_officer"
    )

    def set_password(self, plain_password):
        # pbkdf2 is available on every Python installation.
        self.password_hash = generate_password_hash(plain_password, method="pbkdf2:sha256")

    def check_password(self, plain_password):
        return check_password_hash(self.password_hash, plain_password)

    @property
    def is_officer(self):
        return self.role == ROLE_OFFICER

    @property
    def is_supervisor(self):
        return self.role == ROLE_SUPERVISOR

    @property
    def role_label(self):
        return "CSC Officer" if self.is_officer else "CSC Supervisor"

    def __repr__(self):
        return f"<User {self.username} ({self.role})>"


# =============================================================================
# CASE  (the digital docket)
# =============================================================================
class Case(db.Model):
    __tablename__ = "cases"
    # sqlite_autoincrement: an id number is never re-used, so a case reference
    # can never accidentally be issued twice.
    __table_args__ = {"sqlite_autoincrement": True}

    id = db.Column(db.Integer, primary_key=True)
    case_reference = db.Column(db.String(30), unique=True, nullable=False, index=True)

    complainant_name = db.Column(db.String(120), nullable=False)
    complainant_phone = db.Column(db.String(30), nullable=True)
    complainant_email = db.Column(db.String(120), nullable=False)

    incident_date = db.Column(db.Date, nullable=False)
    incident_location = db.Column(db.String(255), nullable=False)
    incident_type = db.Column(db.String(60), nullable=False)
    statement = db.Column(db.Text, nullable=False)

    status = db.Column(db.String(30), nullable=False, default=CASE_STATUSES[0], index=True)

    registered_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    assigned_officer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    last_activity_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    closed_at = db.Column(db.DateTime, nullable=True)

    registered_by = db.relationship(
        "User", foreign_keys=[registered_by_id], back_populates="registered_cases"
    )
    assigned_officer = db.relationship(
        "User", foreign_keys=[assigned_officer_id], back_populates="assigned_cases"
    )
    activities = db.relationship(
        "CaseActivity",
        back_populates="case",
        order_by="[CaseActivity.created_at.desc(), CaseActivity.id.desc()]",
    )
    audit_logs = db.relationship(
        "AuditLog",
        back_populates="case",
        order_by="[AuditLog.timestamp.desc(), AuditLog.id.desc()]",
    )
    otps = db.relationship("OTP", back_populates="case")
    escalations = db.relationship(
        "Escalation",
        back_populates="case",
        order_by="[Escalation.created_at.desc(), Escalation.id.desc()]",
    )

    @property
    def is_closed(self):
        return self.status == STATUS_CLOSED

    @property
    def pending_escalation(self):
        """The escalation waiting for a supervisor, or None."""
        for escalation in self.escalations:
            if escalation.status == ESCALATION_PENDING:
                return escalation
        return None

    @property
    def is_escalated(self):
        return self.pending_escalation is not None

    @property
    def current_activity(self):
        """The most recent CaseActivity (this is the 'CURRENT ACTIVITY' shown next to the status)."""
        return self.activities[0] if self.activities else None

    def __repr__(self):
        return f"<Case {self.case_reference} ({self.status})>"


# =============================================================================
# CASE ACTIVITY  (progress updates - the timeline of a case)
# =============================================================================
class CaseActivity(db.Model):
    __tablename__ = "case_activities"

    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(db.Integer, db.ForeignKey("cases.id"), nullable=False, index=True)
    officer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    activity_type = db.Column(db.String(60), nullable=False)
    description = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    case = db.relationship("Case", back_populates="activities")
    officer = db.relationship("User")


# =============================================================================
# AUDIT LOG  (append-only: USER -> ACTION -> CASE -> TIMESTAMP)
# =============================================================================
class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    # user_id is empty when the actor is a citizen (OTP) or the system itself.
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    case_id = db.Column(db.Integer, db.ForeignKey("cases.id"), nullable=True, index=True)
    action = db.Column(db.String(50), nullable=False, index=True)
    description = db.Column(db.Text, nullable=False)
    ip_address = db.Column(db.String(45), nullable=True)
    timestamp = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    user = db.relationship("User")
    case = db.relationship("Case", back_populates="audit_logs")

    @property
    def actor_name(self):
        if self.user:
            return f"{self.user.full_name} ({self.user.role_label})"
        if self.action.startswith("OTP") or self.action.startswith("CITIZEN"):
            return "Citizen (OTP portal)"
        return "System"


# =============================================================================
# OTP  (one-time PIN e-mailed to the citizen)
# =============================================================================
class OTP(db.Model):
    __tablename__ = "otps"

    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(db.Integer, db.ForeignKey("cases.id"), nullable=False, index=True)
    email = db.Column(db.String(120), nullable=False)      # the e-mail stored on the case
    code_hash = db.Column(db.String(255), nullable=False)  # the code itself is NEVER stored
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    is_used = db.Column(db.Boolean, nullable=False, default=False)
    attempts = db.Column(db.Integer, nullable=False, default=0)

    case = db.relationship("Case", back_populates="otps")

    def set_code(self, plain_code):
        self.code_hash = generate_password_hash(plain_code, method="pbkdf2:sha256")

    def check_code(self, plain_code):
        return check_password_hash(self.code_hash, plain_code)

    @property
    def is_expired(self):
        return utcnow() > self.expires_at


# =============================================================================
# ESCALATION  (a case that needs supervisor attention)
# =============================================================================
class Escalation(db.Model):
    __tablename__ = "escalations"

    id = db.Column(db.Integer, primary_key=True)
    case_id = db.Column(db.Integer, db.ForeignKey("cases.id"), nullable=False, index=True)
    reason = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    status = db.Column(db.String(20), nullable=False, default=ESCALATION_PENDING, index=True)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    supervisor_action = db.Column(db.String(120), nullable=True)
    supervisor_notes = db.Column(db.Text, nullable=True)

    case = db.relationship("Case", back_populates="escalations")
    reviewed_by = db.relationship("User")


# =============================================================================
# NON-REGISTRATION RECORD  (a reported crime that was NOT registered)
# =============================================================================
# Why this extra table exists: the project documentation says a reported crime
# must never simply disappear. Every refusal needs a coded reason, a written
# justification, the officer's identity and a timestamp - and supervisors must be
# able to monitor these records. The audit log alone cannot hold that structure.
class NonRegistration(db.Model):
    __tablename__ = "non_registrations"
    __table_args__ = {"sqlite_autoincrement": True}

    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(30), unique=True, nullable=False, index=True)
    officer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    complainant_name = db.Column(db.String(120), nullable=False)
    complainant_contact = db.Column(db.String(120), nullable=True)
    incident_summary = db.Column(db.Text, nullable=False)
    reason_code = db.Column(db.String(10), nullable=False)
    justification = db.Column(db.Text, nullable=False)
    related_case_id = db.Column(db.Integer, db.ForeignKey("cases.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    officer = db.relationship("User")
    related_case = db.relationship("Case")

    @property
    def reason_label(self):
        return NON_REGISTRATION_REASONS.get(self.reason_code, self.reason_code)


# =============================================================================
# IMMUTABILITY PROTECTION
# =============================================================================
# Layer 1 (Python): SQLAlchemy refuses to UPDATE or DELETE these records.
@event.listens_for(AuditLog, "before_update")
@event.listens_for(AuditLog, "before_delete")
@event.listens_for(NonRegistration, "before_update")
@event.listens_for(NonRegistration, "before_delete")
def block_changes(mapper, connection, target):
    raise ImmutableRecordError(
        f"{target.__class__.__name__} records are permanent and cannot be changed or deleted."
    )


# Layer 2 (database): SQLite triggers refuse changes even if someone edits the
# database directly with a tool such as DB Browser for SQLite.
IMMUTABILITY_TRIGGERS = [
    """CREATE TRIGGER IF NOT EXISTS audit_logs_no_update BEFORE UPDATE ON audit_logs
       BEGIN SELECT RAISE(ABORT, 'audit_logs is immutable: updates are not allowed'); END;""",
    """CREATE TRIGGER IF NOT EXISTS audit_logs_no_delete BEFORE DELETE ON audit_logs
       BEGIN SELECT RAISE(ABORT, 'audit_logs is immutable: deletes are not allowed'); END;""",
    """CREATE TRIGGER IF NOT EXISTS non_registrations_no_update BEFORE UPDATE ON non_registrations
       BEGIN SELECT RAISE(ABORT, 'non_registrations is immutable: updates are not allowed'); END;""",
    """CREATE TRIGGER IF NOT EXISTS non_registrations_no_delete BEFORE DELETE ON non_registrations
       BEGIN SELECT RAISE(ABORT, 'non_registrations is immutable: deletes are not allowed'); END;""",
]
