"""
app.py  -  MAIN FILE (run this file to start the system)

    MODULE A (the student's portion)   sections 3, 4, 5, 6, 7
        landing page, login, officer pages, supervisor pages, citizen portal
    SHARED / SETUP                     sections 1, 2, 8
        app configuration, decorators, error pages, database start-up
    PARTNER BACKEND                    services.py
        the business logic these routes call (registration, assignment,
        audit trail, OTP, standstill/escalation)

HOW A REQUEST WORKS (for the presentation):
    Browser -> route in app.py -> function in services.py -> models.py/database
            -> render_template(...) -> HTML page shown to the user

UNIVERSITY PROTOTYPE - not an official SAPS system.
"""

import re
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_wtf.csrf import CSRFError, CSRFProtect
from sqlalchemy import and_, func, or_

import services as svc
from config import (
    CASE_STATUSES,
    CITIZEN_ACTIVITY_LABELS,
    CONFIGURABLE_SETTINGS,
    ESCALATION_PENDING,
    ESCALATION_REVIEWED,
    FINDING_CLOSED,
    FINDING_OPEN,
    FINDING_RESPONDED,
    FINDING_SEVERITIES,
    FINDING_STATUSES,
    INCIDENT_TYPES,
    MANUAL_ACTIVITY_TYPES,
    NON_REGISTRATION_REASONS,
    REFERENCE_CATEGORIES,
    REFERENCE_CATEGORY_INCIDENT_TYPE,
    REFERENCE_CATEGORY_NON_REGISTRATION,
    ROLE_ADMIN,
    ROLE_AUDITOR,
    ROLE_OFFICER,
    ROLE_SUPERVISOR,
    STATUS_CLOSED,
    STATUS_OPEN,
    STATUS_UNDER_INVESTIGATION,
    SUPERVISOR_ACTIONS,
    Config,
)
from models import (
    IMMUTABILITY_TRIGGERS,
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
from seed import seed_database
"""
app.py  -  MAIN FILE (run this file to start the system)

    MODULE A (the student's portion)   sections 3, 4, 5, 6, 7
        landing page, login, officer pages, supervisor pages, citizen portal
    SHARED / SETUP                     sections 1, 2, 8
        app configuration, decorators, error pages, database start-up
    PARTNER BACKEND                    services.py
        the business logic these routes call (registration, assignment,
        audit trail, OTP, standstill/escalation)

HOW A REQUEST WORKS (for the presentation):
    Browser -> route in app.py -> function in services.py -> models.py/database
            -> render_template(...) -> HTML page shown to the user

UNIVERSITY PROTOTYPE - not an official SAPS system.
"""

import re
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_wtf.csrf import CSRFError, CSRFProtect
from sqlalchemy import and_, func, or_

import services as svc
from config import (
    CASE_STATUSES,
    CITIZEN_ACTIVITY_LABELS,
    CONFIGURABLE_SETTINGS,
    ESCALATION_PENDING,
    ESCALATION_REVIEWED,
    FINDING_CLOSED,
    FINDING_OPEN,
    FINDING_RESPONDED,
    FINDING_SEVERITIES,
    FINDING_STATUSES,
    INCIDENT_TYPES,
    MANUAL_ACTIVITY_TYPES,
    NON_REGISTRATION_REASONS,
    REFERENCE_CATEGORIES,
    REFERENCE_CATEGORY_INCIDENT_TYPE,
    REFERENCE_CATEGORY_NON_REGISTRATION,
    ROLE_ADMIN,
    ROLE_AUDITOR,
    ROLE_OFFICER,
    ROLE_SUPERVISOR,
    STATUS_CLOSED,
    STATUS_OPEN,
    STATUS_UNDER_INVESTIGATION,
    SUPERVISOR_ACTIONS,
    Config,
)
from models import (
    IMMUTABILITY_TRIGGERS,
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
from seed import seed_database


# =============================================================================
# 1. APP SETUP  (SHARED)
# =============================================================================
app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)
csrf = CSRFProtect(app)  # every POST form must contain the hidden csrf_token field

if not app.config["SECRET_KEY_FROM_ENV"]:
    print("WARNING: SECRET_KEY is not set in .env - using a temporary random key "
          "(everyone is logged out when the server restarts).")

REFERENCE_PATTERN = re.compile(r"^SAPS-\d{4}-\d{6}$")
FAILED_LOGINS = {}  # username -> list of failed-attempt times (kept in memory; prototype only)


# ---- Jinja filters: show times in South African time --------------------------
def to_local(value):
    if isinstance(value, datetime):
        return value + timedelta(hours=app.config["DISPLAY_UTC_OFFSET_HOURS"])
    return value


@app.template_filter("dt")
def filter_datetime(value):
    """15 Sep 2026, 10:32"""
    return to_local(value).strftime("%d %b %Y, %H:%M") if value else "-"


@app.template_filter("longdate")
def filter_longdate(value):
    """15 September 2026"""
    return to_local(value).strftime("%d %B %Y") if value else "-"


@app.template_filter("shortdate")
def filter_shortdate(value):
    """15 Sep"""
    return to_local(value).strftime("%d %b") if value else "-"


@app.template_filter("ago")
def filter_ago(value):
    """'today', '1 day ago', '12 days ago'"""
    if not value:
        return "-"
    days = (utcnow() - value).days
    if days <= 0:
        return "today"
    return "1 day ago" if days == 1 else f"{days} days ago"


# =============================================================================
# 2. AUTHENTICATION HELPERS + DECORATORS  (SHARED)
# =============================================================================
def current_user():
    """The logged-in User (or None). Looked up once per request."""
    if "user" not in g:
        user = None
        user_id = session.get("user_id")
        if user_id:
            user = db.session.get(User, user_id)
            if user is not None and not user.is_active:
                user = None
            if user is None:
                session.pop("user_id", None)
        g.user = user
    return g.user


def home_for(user):
    """Where each role lands after login."""
    if user is None:
        return url_for("login")
    if user.is_supervisor:
        return url_for("supervisor_dashboard")
    if user.is_auditor:
        return url_for("auditor_dashboard")
    if user.is_admin:
        return url_for("admin_dashboard")
    return url_for("officer_dashboard")


OWN_AREA_PREFIX = {
    ROLE_OFFICER: "/officer",
    ROLE_SUPERVISOR: "/supervisor",
    ROLE_AUDITOR: "/auditor",
    ROLE_ADMIN: "/admin",
}


def login_required(view):
    """Only logged-in users may open the page."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def role_required(*roles):
    """Only users with one of the given roles may open the page. Usage: @role_required("OFFICER")"""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if user is None:
                flash("Please log in to continue.", "warning")
                return redirect(url_for("login", next=request.path))
            if user.role not in roles:
                svc.log_audit(user, "ACCESS_DENIED", f"Tried to open {request.path} without permission.")
                db.session.commit()
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def is_locked_out(identifier):
    """Too many wrong passwords in a short time -> temporary lock."""
    window = timedelta(minutes=app.config["LOGIN_LOCKOUT_MINUTES"])
    recent = [t for t in FAILED_LOGINS.get(identifier, []) if utcnow() - t < window]
    FAILED_LOGINS[identifier] = recent
    return len(recent) >= app.config["LOGIN_MAX_ATTEMPTS"]


@app.before_request
def load_dynamic_settings():
    """Copy any admin-configured overrides (System Configuration page) onto app.config."""
    svc.apply_dynamic_settings(app)


@app.context_processor
def inject_template_variables():
    """Variables that every template can use."""
    return {
        "current_user": current_user(),
        "CASE_STATUSES": CASE_STATUSES,
        "STATUS_OPEN": STATUS_OPEN,
        "STATUS_UNDER_INVESTIGATION": STATUS_UNDER_INVESTIGATION,
        "STATUS_CLOSED": STATUS_CLOSED,
        "home_url": home_for(current_user()),
        "standstill_days": app.config["CASE_STANDSTILL_DAYS"],
    }





# =============================================================================
# 3. PUBLIC ROUTES  (MODULE A: landing page, login, logout)
# =============================================================================






@app.route("/")
def index():
    return render_template("index.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    user = current_user()
    if user:
        return redirect(home_for(user))

    if request.method == "POST":
        identifier = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")

        if not identifier or not password:
            flash("Enter your username (or e-mail) and your password.", "danger")
            return render_template("login.html", username=identifier)

        if is_locked_out(identifier):
            svc.log_audit(None, "LOGIN_LOCKED", f"Login blocked for '{identifier}' after too many failed attempts.")
            db.session.commit()
            flash(
                f"Too many failed attempts. Please wait {app.config['LOGIN_LOCKOUT_MINUTES']} minutes and try again.",
                "danger",
            )
            return render_template("login.html", username=identifier)

        candidate = User.query.filter(
            or_(func.lower(User.username) == identifier, func.lower(User.email) == identifier)
        ).first()

        if candidate and candidate.is_active and candidate.check_password(password):
            FAILED_LOGINS.pop(identifier, None)
            session.clear()                 # start a fresh session (prevents session fixation)
            session["user_id"] = candidate.id
            session.permanent = True        # expires after SESSION_TIMEOUT_MINUTES of inactivity
            candidate.last_login_at = utcnow()
            svc.log_audit(candidate, "LOGIN_SUCCESS", f"{candidate.full_name} logged in.")
            db.session.commit()
            flash(f"Login successful. Welcome, {candidate.full_name}.", "success")

            # Only follow the ?next= link if it stays inside this user's own area.
            target = request.args.get("next", "")
            own_area = OWN_AREA_PREFIX.get(candidate.role, "")
            if own_area and target.startswith(own_area) and not target.startswith("//"):
                return redirect(target)
            return redirect(home_for(candidate))

        FAILED_LOGINS.setdefault(identifier, []).append(utcnow())
        svc.log_audit(
            candidate,
            "LOGIN_FAILED",
            f"Failed login attempt for '{identifier}'.",
        )
        db.session.commit()
        flash("Incorrect username or password.", "danger")
        return render_template("login.html", username=identifier)

    return render_template("login.html", username="")


@app.route("/logout", methods=["GET", "POST"])
def logout():
    user = current_user()
    if user:
        svc.log_audit(user, "LOGOUT", f"{user.full_name} logged out.")
        db.session.commit()
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


# =============================================================================
# 4. CITIZEN PORTAL  (MODULE A: case reference + e-mail OTP)
# =============================================================================
def get_verified_citizen_case():
    """The Case the citizen proved access to with an OTP, or None (also None when the session expired)."""
    case_id = session.get("citizen_case_id")
    stamp = session.get("citizen_verified_at")
    if not case_id or not stamp:
        return None
    try:
        verified_at = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if utcnow() - verified_at > timedelta(minutes=app.config["CITIZEN_SESSION_MINUTES"]):
        clear_citizen_session()
        flash("Your viewing session expired. Please request a new OTP.", "warning")
        return None
    return db.session.get(Case, case_id)


def clear_citizen_session():
    for key in ("citizen_case_id", "citizen_verified_at", "citizen_pending_ref"):
        session.pop(key, None)


@app.route("/citizen")
def citizen_portal():
    return render_template("citizen/portal.html", verified=get_verified_citizen_case() is not None)


@app.route("/citizen/request-otp", methods=["GET", "POST"])
def citizen_request_otp():
    if request.method == "GET":
        return redirect(url_for("citizen_portal"))

    reference = request.form.get("case_reference", "").strip().upper()
    if not REFERENCE_PATTERN.match(reference):
        flash("Please enter your case reference exactly as e-mailed to you, e.g. SAPS-2026-000001.", "danger")
        return redirect(url_for("citizen_portal"))

    case = Case.query.filter_by(case_reference=reference).first()
    if case is not None:
        # The OTP is ALWAYS sent to the e-mail stored on the case - the citizen cannot choose it.
        code, status = svc.issue_otp(case)
        if status == "created":
            sent, mode = svc.email_otp(case, code)
            svc.log_audit(None, "OTP_REQUESTED", f"OTP requested for case {case.case_reference} (delivery: {mode}).", case)
            if mode == "console":
                flash("Development mode: e-mail is not configured, so the OTP was printed in the server console.", "info")
        else:
            svc.log_audit(None, "OTP_REQUEST_THROTTLED", "OTP requested again too soon - no new OTP created.", case)
        db.session.commit()

    # The same message is shown whether or not the reference exists, so nobody can
    # use this page to find out which case references are real.
    session["citizen_pending_ref"] = reference
    flash("OTP sent. If the case reference is correct, a one-time PIN has been e-mailed to the address "
          "registered on the case.", "success")
    return redirect(url_for("citizen_verify_otp"))


@app.route("/citizen/verify-otp", methods=["GET", "POST"])
def citizen_verify_otp():
    reference = session.get("citizen_pending_ref")
    if not reference:
        flash("Please enter your case reference first.", "warning")
        return redirect(url_for("citizen_portal"))

    if request.method == "POST":
        entered = request.form.get("otp", "").strip()
        if not re.match(r"^\d{6}$", entered):
            flash("The OTP is 6 digits. Please check the e-mail and try again.", "danger")
            return render_template("citizen/verify_otp.html", reference=reference)

        case = Case.query.filter_by(case_reference=reference).first()
        result, attempts_left = svc.verify_otp(case, entered) if case else ("none", 0)

        if result == "ok":
            session.pop("citizen_pending_ref", None)
            session["citizen_case_id"] = case.id
            session["citizen_verified_at"] = utcnow().isoformat()
            svc.log_audit(None, "OTP_VERIFIED", "Citizen verified OTP successfully.", case)
            db.session.commit()
            flash("OTP verified. You can now see the status of your case.", "success")
            return redirect(url_for("citizen_status"))

        if case:
            svc.log_audit(None, "OTP_FAILED", f"OTP verification failed ({result}).", case)
            db.session.commit()

        if result == "invalid":
            flash(f"Invalid OTP. You have {attempts_left} attempt(s) left.", "danger")
        elif result == "expired":
            flash("Expired OTP. Please request a new one.", "danger")
        elif result == "locked":
            flash("Too many wrong attempts. This OTP is now cancelled - please request a new one.", "danger")
        else:
            flash("Invalid OTP. It may have been used already or replaced - please request a new one.", "danger")
        return render_template("citizen/verify_otp.html", reference=reference)

    return render_template("citizen/verify_otp.html", reference=reference)


@app.route("/citizen/status")
def citizen_status():
    case = get_verified_citizen_case()
    if case is None:
        flash("Please verify with an OTP to view your case.", "warning")
        return redirect(url_for("citizen_portal"))

    svc.log_case_view(None, case, "CITIZEN_STATUS_VIEWED", "Citizen viewed the case status after OTP verification.")

    # Only these citizen-safe values go to the template. No audit log,
    # no officer descriptions, no statement, no other citizens' data.
    timeline = [
        {
            "when": activity.created_at,
            "text": CITIZEN_ACTIVITY_LABELS.get(activity.activity_type, "A progress update was recorded on your case."),
        }
        for activity in case.activities
    ]
    info = {
        "reference": case.case_reference,
        "status": case.status,
        "registered": case.created_at,
        "last_update": case.last_activity_at,
        "incident_type": case.incident_type,
        "incident_date": case.incident_date,
        "officer_name": svc.citizen_officer_name(case.assigned_officer),
        "current_activity": timeline[0]["text"] if timeline else None,
        "timeline": timeline,
    }
    return render_template("citizen/status.html", info=info)


@app.route("/citizen/logout", methods=["GET", "POST"])
def citizen_logout():
    clear_citizen_session()
    flash("You have left the case tracking portal.", "info")
    return redirect(url_for("citizen_portal"))


# =============================================================================
# 5. OFFICER ROUTES  (MODULE A: dashboard, lists, detail, update pages)
# =============================================================================
def involved_clause(user):
    """SQL condition: cases the officer registered OR is assigned to."""
    return or_(Case.registered_by_id == user.id, Case.assigned_officer_id == user.id)


def pending_escalation_clause():
    return Case.escalations.any(Escalation.status == ESCALATION_PENDING)


def apply_case_filters(query):
    """Shared by the officer and supervisor case lists: status filter + search box."""
    status = request.args.get("status", "")
    if status in CASE_STATUSES:
        query = query.filter(Case.status == status)
    search = request.args.get("q", "").strip()
    if search:
        like = f"%{search}%"
        query = query.filter(
            or_(
                Case.case_reference.ilike(like),
                Case.complainant_name.ilike(like),
                Case.incident_location.ilike(like),
            )
        )
    return query


def get_case_for_officer(case_id):
    """Load a case and make sure this officer is allowed to open it."""
    user = current_user()
    case = db.get_or_404(Case, case_id)
    if not svc.officer_can_access_case(user, case):
        svc.log_audit(user, "ACCESS_DENIED", f"Tried to open case {case.case_reference} without permission.", case)
        db.session.commit()
        abort(403)
    return case


def flash_form_errors(errors):
    for message in errors:
        flash(message, "danger")


@app.route("/officer/dashboard")
@login_required
@role_required(ROLE_OFFICER)
def officer_dashboard():
    user = current_user()
    svc.run_standstill_check()  # keeps the "requiring attention" numbers up to date
    involved = involved_clause(user)

    stats = {
        "registered": Case.query.filter_by(registered_by_id=user.id).count(),
        "assigned": Case.query.filter_by(assigned_officer_id=user.id).count(),
        "open": Case.query.filter(involved, Case.status == STATUS_OPEN).count(),
        "investigating": Case.query.filter(involved, Case.status == STATUS_UNDER_INVESTIGATION).count(),
        "closed": Case.query.filter(involved, Case.status == STATUS_CLOSED).count(),
        "attention": Case.query.filter(involved, pending_escalation_clause()).count(),
    }
    recent = (
        CaseActivity.query.join(Case, CaseActivity.case_id == Case.id)
        .filter(involved)
        .order_by(CaseActivity.created_at.desc(), CaseActivity.id.desc())
        .limit(8)
        .all()
    )
    attention_cases = (
        Case.query.filter(involved, pending_escalation_clause())
        .order_by(Case.last_activity_at.asc())
        .limit(5)
        .all()
    )
    return render_template("officer/dashboard.html", stats=stats, recent=recent, attention_cases=attention_cases)


@app.route("/officer/cases")
@login_required
@role_required(ROLE_OFFICER)
def officer_cases():
    user = current_user()
    view = request.args.get("view", "all")
    if view == "registered":
        query = Case.query.filter(Case.registered_by_id == user.id)
        title = "Cases I registered"
    elif view == "assigned":
        query = Case.query.filter(Case.assigned_officer_id == user.id)
        title = "Cases assigned to me"
    elif view == "attention":
        query = Case.query.filter(involved_clause(user), pending_escalation_clause())
        title = "Cases requiring attention"
    else:
        view = "all"
        query = Case.query.filter(involved_clause(user))
        title = "My cases"

    query = apply_case_filters(query)
    page = request.args.get("page", 1, type=int)
    pager = query.order_by(Case.last_activity_at.desc(), Case.id.desc()).paginate(
        page=page, per_page=app.config["PAGE_SIZE"], error_out=False
    )
    filters = {k: v for k, v in (("view", view), ("status", request.args.get("status", "")),
                                 ("q", request.args.get("q", ""))) if v}
    return render_template("officer/cases.html", pager=pager, view=view, title=title, filters=filters,
                           status_filter=request.args.get("status", ""), search=request.args.get("q", ""))


@app.route("/officer/cases/register", methods=["GET", "POST"])
@login_required
@role_required(ROLE_OFFICER)
def officer_register_case():
    user = current_user()
    if request.method == "POST":
        clean, errors = svc.validate_case_form(request.form, include_statement=True)
        if errors:
            flash_form_errors(errors)
            return render_template("officer/register_case.html", form=request.form,
                                   incident_types=svc.get_incident_types(), today=svc.local_today().isoformat())

        case = svc.register_case(user, clean)
        flash(f"Case registered successfully. Case reference: {case.case_reference}", "success")

        sent, mode = svc.email_case_reference(case, user)
        if sent:
            flash(f"Case reference emailed to {case.complainant_email}.", "success")
        elif mode == "console":
            flash("E-mail is not configured (development mode): the reference e-mail was printed in the server console.", "warning")
        else:
            flash("The case was saved, but the reference e-mail could not be sent. "
                  "Give the reference to the complainant and use 'Resend reference' later.", "warning")
        flash("You can now assign the case to an officer below.", "info")
        return redirect(url_for("officer_case_detail", case_id=case.id))

    return render_template("officer/register_case.html", form={}, incident_types=svc.get_incident_types(),
                           today=svc.local_today().isoformat())


@app.route("/officer/case/<int:case_id>")
@login_required
@role_required(ROLE_OFFICER)
def officer_case_detail(case_id):
    user = current_user()
    case = get_case_for_officer(case_id)
    svc.log_case_view(user, case)
    officers = User.query.filter_by(role=ROLE_OFFICER, is_active=True).order_by(User.full_name).all()
    return render_template(
        "officer/case_detail.html",
        case=case,
        officers=officers,
        audit_entries=case.audit_logs[:60],
        next_statuses=svc.allowed_next_statuses(case),
    )


@app.route("/officer/case/<int:case_id>/update", methods=["GET", "POST"])
@login_required
@role_required(ROLE_OFFICER)
def officer_case_update(case_id):
    user = current_user()
    case = get_case_for_officer(case_id)

    if request.method == "POST":
        clean, errors = svc.validate_progress_form(case, request.form)
        if errors:
            flash_form_errors(errors)
        else:
            svc.record_progress_update(case, user, clean["activity_type"], clean["description"], commit=False)
            if clean["new_status"]:
                svc.change_status(case, clean["new_status"], user, clean["description"], commit=False)
            db.session.commit()
            flash("Progress update recorded successfully.", "success")
            if clean["new_status"]:
                flash(f"Status changed to {clean['new_status']}.", "success")
            return redirect(url_for("officer_case_detail", case_id=case.id))

    return render_template(
        "officer/update_case.html",
        case=case,
        form=request.form if request.method == "POST" else {},
        activity_types=MANUAL_ACTIVITY_TYPES,
        next_statuses=svc.allowed_next_statuses(case),
    )


@app.route("/officer/case/<int:case_id>/assign", methods=["POST"])
@login_required
@role_required(ROLE_OFFICER)
def officer_case_assign(case_id):
    user = current_user()
    case = get_case_for_officer(case_id)

    if case.is_closed:
        flash("A closed case cannot be assigned. Re-open it first.", "danger")
        return redirect(url_for("officer_case_detail", case_id=case.id))

    officer_id = request.form.get("officer_id", "").strip()
    note = request.form.get("note", "").strip()
    officer = None
    if officer_id.isdigit():
        officer = User.query.filter_by(id=int(officer_id), role=ROLE_OFFICER, is_active=True).first()

    if officer is None:
        flash("Please choose an officer to assign the case to.", "danger")
    elif len(note) > 500:
        flash("The assignment note may not be longer than 500 characters.", "danger")
    elif officer.id == case.assigned_officer_id:
        flash(f"The case is already assigned to {officer.full_name}.", "info")
    else:
        svc.assign_case(case, officer, user, note)
        flash(f"Assignment successful. Case assigned to {officer.full_name}.", "success")
    return redirect(url_for("officer_case_detail", case_id=case.id))


@app.route("/officer/case/<int:case_id>/edit", methods=["GET", "POST"])
@login_required
@role_required(ROLE_OFFICER)
def officer_case_edit(case_id):
    user = current_user()
    case = get_case_for_officer(case_id)
    if case.is_closed:
        flash("A closed case cannot be edited. Re-open it first.", "danger")
        return redirect(url_for("officer_case_detail", case_id=case.id))

    if request.method == "POST":
        clean, errors = svc.validate_case_form(request.form, include_statement=False)
        if errors:
            flash_form_errors(errors)
            return render_template("officer/edit_case.html", case=case, form=request.form,
                                   incident_types=svc.get_incident_types(), today=svc.local_today().isoformat())
        if svc.correct_case_details(case, user, clean):
            flash("Case details updated. The change (old and new value) was written to the audit trail.", "success")
        else:
            flash("No changes were made.", "info")
        return redirect(url_for("officer_case_detail", case_id=case.id))

    form = {
        "complainant_name": case.complainant_name,
        "complainant_email": case.complainant_email,
        "complainant_phone": case.complainant_phone or "",
        "incident_date": case.incident_date.isoformat(),
        "incident_location": case.incident_location,
        "incident_type": case.incident_type,
    }
    return render_template("officer/edit_case.html", case=case, form=form, incident_types=svc.get_incident_types(),
                           today=svc.local_today().isoformat())


@app.route("/officer/case/<int:case_id>/resend-reference", methods=["POST"])
@login_required
@role_required(ROLE_OFFICER)
def officer_case_resend(case_id):
    user = current_user()
    case = get_case_for_officer(case_id)
    sent, mode = svc.email_case_reference(case, user)
    if sent:
        flash(f"Case reference emailed to {case.complainant_email}.", "success")
    elif mode == "console":
        flash("E-mail is not configured (development mode): the message was printed in the server console.", "warning")
    else:
        flash("The e-mail could not be sent. Please check the mail settings in .env.", "danger")
    return redirect(url_for("officer_case_detail", case_id=case.id))


@app.route("/officer/profile", methods=["GET", "POST"])
@login_required
@role_required(ROLE_OFFICER)
def officer_profile():
    user = current_user()

    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if not user.check_password(current_password):
            flash("Your current password is incorrect.", "danger")
        elif len(new_password) < 8 or not re.search(r"[A-Za-z]", new_password) or not re.search(r"\d", new_password):
            flash("The new password must be at least 8 characters and contain letters and numbers.", "danger")
        elif new_password != confirm:
            flash("The new password and the confirmation do not match.", "danger")
        elif new_password == current_password:
            flash("The new password must be different from the current one.", "danger")
        else:
            user.set_password(new_password)
            svc.log_audit(user, "PASSWORD_CHANGED", f"{user.full_name} changed their password.")
            db.session.commit()
            flash("Password changed successfully.", "success")
            return redirect(url_for("officer_profile"))

    my_actions = (
        AuditLog.query.filter_by(user_id=user.id)
        .order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
        .limit(15)
        .all()
    )
    counts = {
        "registered": Case.query.filter_by(registered_by_id=user.id).count(),
        "assigned": Case.query.filter_by(assigned_officer_id=user.id).count(),
        "actions": AuditLog.query.filter_by(user_id=user.id).count(),
    }
    return render_template("officer/profile.html", my_actions=my_actions, counts=counts)


@app.route("/officer/non-registrations")
@login_required
@role_required(ROLE_OFFICER)
def officer_refusals():
    user = current_user()
    page = request.args.get("page", 1, type=int)
    pager = (
        NonRegistration.query.filter_by(officer_id=user.id)
        .order_by(NonRegistration.created_at.desc(), NonRegistration.id.desc())
        .paginate(page=page, per_page=app.config["PAGE_SIZE"], error_out=False)
    )
    return render_template("officer/refusals.html", pager=pager)


@app.route("/officer/non-registrations/new", methods=["GET", "POST"])
@login_required
@role_required(ROLE_OFFICER)
def officer_refusal_new():
    user = current_user()
    if request.method == "POST":
        clean, errors = svc.validate_non_registration_form(request.form)
        if errors:
            flash_form_errors(errors)
            return render_template("officer/refusal_form.html", form=request.form, reasons=svc.get_non_registration_reasons())
        record = svc.record_non_registration(user, clean)
        flash(f"Non-registration record {record.reference} saved. It cannot be edited or deleted.", "success")
        return redirect(url_for("officer_refusals"))
    return render_template("officer/refusal_form.html", form={}, reasons=svc.get_non_registration_reasons())


# =============================================================================
# 6. SUPERVISOR ROUTES  (MODULE A: oversight pages)
# =============================================================================
@app.route("/supervisor/dashboard")
@login_required
@role_required(ROLE_SUPERVISOR)
def supervisor_dashboard():
    svc.run_standstill_check()

    stats = {
        "total": Case.query.count(),
        "open": Case.query.filter_by(status=STATUS_OPEN).count(),
        "investigating": Case.query.filter_by(status=STATUS_UNDER_INVESTIGATION).count(),
        "closed": Case.query.filter_by(status=STATUS_CLOSED).count(),
        "attention": Escalation.query.filter_by(status=ESCALATION_PENDING).count(),
        "refusals": NonRegistration.query.count(),
        "findings_pending": AuditFinding.query.filter_by(status=FINDING_OPEN).count(),
    }
    pending = (
        Escalation.query.filter_by(status=ESCALATION_PENDING)
        .order_by(Escalation.created_at.desc())
        .limit(5)
        .all()
    )
    recent_audit = AuditLog.query.order_by(AuditLog.timestamp.desc(), AuditLog.id.desc()).limit(10).all()
    workload = (
        db.session.query(User, func.count(Case.id))
        .outerjoin(Case, and_(Case.assigned_officer_id == User.id, Case.status != STATUS_CLOSED))
        .filter(User.role == ROLE_OFFICER)
        .group_by(User.id)
        .order_by(User.full_name)
        .all()
    )
    return render_template("supervisor/dashboard.html", stats=stats, pending=pending,
                           recent_audit=recent_audit, workload=workload)


@app.route("/supervisor/cases")
@login_required
@role_required(ROLE_SUPERVISOR)
def supervisor_cases():
    svc.run_standstill_check()
    query = Case.query
    attention_only = request.args.get("attention", "") == "1"
    if attention_only:
        query = query.filter(pending_escalation_clause())
    query = apply_case_filters(query)

    page = request.args.get("page", 1, type=int)
    pager = query.order_by(Case.last_activity_at.desc(), Case.id.desc()).paginate(
        page=page, per_page=app.config["PAGE_SIZE"], error_out=False
    )
    filters = {k: v for k, v in (("status", request.args.get("status", "")),
                                 ("q", request.args.get("q", "")),
                                 ("attention", "1" if attention_only else "")) if v}
    return render_template("supervisor/cases.html", pager=pager, filters=filters,
                           status_filter=request.args.get("status", ""),
                           search=request.args.get("q", ""), attention_only=attention_only)


@app.route("/supervisor/case/<int:case_id>")
@login_required
@role_required(ROLE_SUPERVISOR)
def supervisor_case_detail(case_id):
    user = current_user()
    case = db.get_or_404(Case, case_id)
    svc.log_case_view(user, case)
    officers = User.query.filter_by(role=ROLE_OFFICER, is_active=True).order_by(User.full_name).all()
    return render_template(
        "supervisor/case_detail.html",
        case=case,
        officers=officers,
        audit_entries=case.audit_logs[:100],
        supervisor_actions=SUPERVISOR_ACTIONS,
        form={},
    )


@app.route("/supervisor/escalations")
@login_required
@role_required(ROLE_SUPERVISOR)
def supervisor_escalations():
    svc.run_standstill_check()
    status = request.args.get("status", ESCALATION_PENDING)
    query = Escalation.query
    if status in (ESCALATION_PENDING, ESCALATION_REVIEWED):
        query = query.filter_by(status=status)
    else:
        status = "ALL"
    page = request.args.get("page", 1, type=int)
    pager = query.order_by(Escalation.created_at.desc(), Escalation.id.desc()).paginate(
        page=page, per_page=app.config["PAGE_SIZE"], error_out=False
    )
    return render_template("supervisor/escalations.html", pager=pager, status=status,
                           filters={"status": status},
                           pending_count=Escalation.query.filter_by(status=ESCALATION_PENDING).count())


@app.route("/supervisor/escalations/scan", methods=["POST"])
@login_required
@role_required(ROLE_SUPERVISOR)
def supervisor_escalation_scan():
    created = svc.run_standstill_check()
    if created:
        flash(f"Standstill check complete: {len(created)} case(s) flagged for attention.", "warning")
    else:
        flash(f"Standstill check complete: no new cases found "
              f"(threshold: {app.config['CASE_STANDSTILL_DAYS']} day(s) without activity).", "info")
    return redirect(url_for("supervisor_escalations"))


@app.route("/supervisor/escalation/<int:escalation_id>/review", methods=["POST"])
@login_required
@role_required(ROLE_SUPERVISOR)
def supervisor_escalation_review(escalation_id):
    user = current_user()
    escalation = db.get_or_404(Escalation, escalation_id)
    case = escalation.case

    if escalation.status != ESCALATION_PENDING:
        flash("This escalation has already been reviewed.", "info")
        return redirect(url_for("supervisor_case_detail", case_id=case.id))

    officers = User.query.filter_by(role=ROLE_OFFICER, is_active=True).order_by(User.full_name).all()
    clean, errors = svc.validate_escalation_review_form(request.form, {o.id for o in officers})
    if errors:
        flash_form_errors(errors)
        return render_template(
            "supervisor/case_detail.html",
            case=case, officers=officers, audit_entries=case.audit_logs[:100],
            supervisor_actions=SUPERVISOR_ACTIONS, form=request.form,
        )

    reassign_officer = db.session.get(User, clean["reassign_to"]) if clean["reassign_to"] else None
    svc.review_escalation(escalation, user, clean["supervisor_action"], clean["supervisor_notes"], reassign_officer)
    flash("Supervisor review recorded in the audit trail. The case continues.", "success")
    return redirect(url_for("supervisor_case_detail", case_id=case.id))


@app.route("/supervisor/audit")
@login_required
@role_required(ROLE_SUPERVISOR)
def supervisor_audit():
    query = AuditLog.query.outerjoin(Case, AuditLog.case_id == Case.id)

    reference = request.args.get("q", "").strip()
    user_filter = request.args.get("user", "").strip()
    action_filter = request.args.get("action", "").strip()
    if reference:
        query = query.filter(Case.case_reference.ilike(f"%{reference}%"))
    if user_filter.isdigit():
        query = query.filter(AuditLog.user_id == int(user_filter))
    if action_filter:
        query = query.filter(AuditLog.action == action_filter)

    page = request.args.get("page", 1, type=int)
    pager = query.order_by(AuditLog.timestamp.desc(), AuditLog.id.desc()).paginate(
        page=page, per_page=20, error_out=False
    )
    users = User.query.order_by(User.full_name).all()
    actions = [row[0] for row in db.session.query(AuditLog.action).distinct().order_by(AuditLog.action).all()]
    filters = {k: v for k, v in (("q", reference), ("user", user_filter), ("action", action_filter)) if v}
    return render_template("supervisor/audit.html", pager=pager, users=users, actions=actions, filters=filters,
                           reference=reference, user_filter=user_filter, action_filter=action_filter)


@app.route("/supervisor/non-registrations")
@login_required
@role_required(ROLE_SUPERVISOR)
def supervisor_refusals():
    page = request.args.get("page", 1, type=int)
    pager = NonRegistration.query.order_by(
        NonRegistration.created_at.desc(), NonRegistration.id.desc()
    ).paginate(page=page, per_page=app.config["PAGE_SIZE"], error_out=False)
    return render_template("supervisor/refusals.html", pager=pager)


@app.route("/supervisor/findings")
@login_required
@role_required(ROLE_SUPERVISOR)
def supervisor_findings():
    status = request.args.get("status", FINDING_OPEN)
    query = AuditFinding.query
    if status in FINDING_STATUSES:
        query = query.filter_by(status=status)
    else:
        status = "ALL"
    page = request.args.get("page", 1, type=int)
    pager = query.order_by(AuditFinding.created_at.desc(), AuditFinding.id.desc()).paginate(
        page=page, per_page=app.config["PAGE_SIZE"], error_out=False
    )
    return render_template("supervisor/findings.html", pager=pager, status=status,
                           pending_count=AuditFinding.query.filter_by(status=FINDING_OPEN).count())


@app.route("/supervisor/findings/<int:finding_id>")
@login_required
@role_required(ROLE_SUPERVISOR)
def supervisor_finding_detail(finding_id):
    finding = db.get_or_404(AuditFinding, finding_id)
    return render_template("supervisor/finding_detail.html", finding=finding, form={})


@app.route("/supervisor/findings/<int:finding_id>/respond", methods=["POST"])
@login_required
@role_required(ROLE_SUPERVISOR)
def supervisor_finding_respond(finding_id):
    """SUPERVISOR responds to / investigates an audit finding raised by an auditor."""
    user = current_user()
    finding = db.get_or_404(AuditFinding, finding_id)

    if finding.status != FINDING_OPEN:
        flash("This finding does not currently require a response.", "info")
        return redirect(url_for("supervisor_finding_detail", finding_id=finding.id))

    response_text = request.form.get("response_text", "").strip()
    if len(response_text) < 10:
        flash("Please describe the investigation or action taken (at least 10 characters).", "danger")
        return render_template("supervisor/finding_detail.html", finding=finding, form=request.form)

    svc.respond_to_finding(finding, user, response_text)
    flash("Response submitted to the auditor for review.", "success")
    return redirect(url_for("supervisor_finding_detail", finding_id=finding.id))



#=============================================================================
# 6B. INTERNAL AUDITOR / IPID OFFICER ROUTES  (MODULE A: read-only oversight)
# =============================================================================

@app.route("/auditor/dashboard")
@login_required
@role_required(ROLE_AUDITOR)
def auditor_dashboard():
    stats = {
        "total": Case.query.count(),
        "open": Case.query.filter_by(status=STATUS_OPEN).count(),
        "investigating": Case.query.filter_by(status=STATUS_UNDER_INVESTIGATION).count(),
        "closed": Case.query.filter_by(status=STATUS_CLOSED).count(),
        "escalated": Escalation.query.filter_by(status=ESCALATION_PENDING).count(),
        "refusals": NonRegistration.query.count(),
        "audit_entries": AuditLog.query.count(),
        "findings_to_review": AuditFinding.query.filter_by(status=FINDING_RESPONDED).count(),
    }
    recent_audit = AuditLog.query.order_by(AuditLog.timestamp.desc(), AuditLog.id.desc()).limit(10).all()
    recent_refusals = (
        NonRegistration.query.order_by(NonRegistration.created_at.desc(), NonRegistration.id.desc())
        .limit(5)
        .all()
    )
    access_denied_count = AuditLog.query.filter_by(action="ACCESS_DENIED").count()
    return render_template(
        "auditor/dashboard.html",
        stats=stats,
        recent_audit=recent_audit,
        recent_refusals=recent_refusals,
        access_denied_count=access_denied_count,
    )


@app.route("/auditor/cases")
@login_required
@role_required(ROLE_AUDITOR)
def auditor_cases():
    query = apply_case_filters(Case.query)
    page = request.args.get("page", 1, type=int)
    pager = query.order_by(Case.last_activity_at.desc(), Case.id.desc()).paginate(
        page=page, per_page=app.config["PAGE_SIZE"], error_out=False
    )
    filters = {k: v for k, v in (("status", request.args.get("status", "")),
                                 ("q", request.args.get("q", ""))) if v}
    return render_template("auditor/cases.html", pager=pager, filters=filters,
                           status_filter=request.args.get("status", ""),
                           search=request.args.get("q", ""))


@app.route("/auditor/case/<int:case_id>")
@login_required
@role_required(ROLE_AUDITOR)
def auditor_case_detail(case_id):
    user = current_user()
    case = db.get_or_404(Case, case_id)
    svc.log_case_view(user, case, "CASE_VIEWED", f"Auditor reviewed case {case.case_reference}.")
    return render_template("auditor/case_details.html", case=case, audit_entries=case.audit_logs[:200])


@app.route("/auditor/audit")
@login_required
@role_required(ROLE_AUDITOR)
def auditor_audit():
    query = AuditLog.query.outerjoin(Case, AuditLog.case_id == Case.id)

    reference = request.args.get("q", "").strip()
    user_filter = request.args.get("user", "").strip()
    action_filter = request.args.get("action", "").strip()
    if reference:
        query = query.filter(Case.case_reference.ilike(f"%{reference}%"))
    if user_filter.isdigit():
        query = query.filter(AuditLog.user_id == int(user_filter))
    if action_filter:
        query = query.filter(AuditLog.action == action_filter)

    page = request.args.get("page", 1, type=int)
    pager = query.order_by(AuditLog.timestamp.desc(), AuditLog.id.desc()).paginate(
        page=page, per_page=20, error_out=False
    )
    users = User.query.order_by(User.full_name).all()
    actions = [row[0] for row in db.session.query(AuditLog.action).distinct().order_by(AuditLog.action).all()]
    filters = {k: v for k, v in (("q", reference), ("user", user_filter), ("action", action_filter)) if v}
    return render_template("auditor/audit.html", pager=pager, users=users, actions=actions, filters=filters,
                           reference=reference, user_filter=user_filter, action_filter=action_filter)


@app.route("/auditor/non-registrations")
@login_required
@role_required(ROLE_AUDITOR)
def auditor_refusals():
    page = request.args.get("page", 1, type=int)
    pager = NonRegistration.query.order_by(
        NonRegistration.created_at.desc(), NonRegistration.id.desc()
    ).paginate(page=page, per_page=app.config["PAGE_SIZE"], error_out=False)
    return render_template("auditor/refusals.html", pager=pager)


@app.route("/auditor/case/<int:case_id>/export")
@login_required
@role_required(ROLE_AUDITOR)
def auditor_case_export(case_id):
    """Export Report - a downloadable CSV of the case details, timeline and audit trail."""
    user = current_user()
    case = db.get_or_404(Case, case_id)
    svc.log_audit(user, "CASE_REPORT_EXPORTED", f"Exported a report for case {case.case_reference}.", case)
    db.session.commit()
    return svc.build_case_report_csv(case)


@app.route("/auditor/case/<int:case_id>/flag", methods=["GET", "POST"])
@login_required
@role_required(ROLE_AUDITOR)
def auditor_flag_case(case_id):
    """Flag for Review - the auditor creates an audit finding against a case."""
    user = current_user()
    case = db.get_or_404(Case, case_id)

    if request.method == "POST":
        clean, errors = svc.validate_finding_form(request.form)
        if errors:
            flash_form_errors(errors)
            return render_template("auditor/flag_case.html", case=case, form=request.form,
                                   severities=FINDING_SEVERITIES)
        finding = svc.create_finding(case, user, clean)
        flash(f"Finding {finding.reference} raised and sent to supervisors for a response.", "success")
        return redirect(url_for("auditor_finding_detail", finding_id=finding.id))

    return render_template("auditor/flag_case.html", case=case, form={}, severities=FINDING_SEVERITIES)


@app.route("/auditor/findings")
@login_required
@role_required(ROLE_AUDITOR)
def auditor_findings():
    status = request.args.get("status", "")
    query = AuditFinding.query
    if status in FINDING_STATUSES:
        query = query.filter_by(status=status)
    else:
        status = ""
    page = request.args.get("page", 1, type=int)
    pager = query.order_by(AuditFinding.created_at.desc(), AuditFinding.id.desc()).paginate(
        page=page, per_page=app.config["PAGE_SIZE"], error_out=False
    )
    return render_template("auditor/findings.html", pager=pager, status=status,
                           open_count=AuditFinding.query.filter_by(status=FINDING_OPEN).count(),
                           responded_count=AuditFinding.query.filter_by(status=FINDING_RESPONDED).count())


@app.route("/auditor/findings/<int:finding_id>")
@login_required
@role_required(ROLE_AUDITOR)
def auditor_finding_detail(finding_id):
    finding = db.get_or_404(AuditFinding, finding_id)
    return render_template("auditor/finding_detail.html", finding=finding)


@app.route("/auditor/findings/<int:finding_id>/review", methods=["POST"])
@login_required
@role_required(ROLE_AUDITOR)
def auditor_finding_review(finding_id):
    """The auditor reviews the supervisor's response: close the finding, or send it back."""
    user = current_user()
    finding = db.get_or_404(AuditFinding, finding_id)

    if finding.status != FINDING_RESPONDED:
        flash("This finding is not currently awaiting review.", "info")
        return redirect(url_for("auditor_finding_detail", finding_id=finding.id))

    decision = request.form.get("decision", "").strip()
    notes = request.form.get("review_notes", "").strip()
    if len(notes) < 5:
        flash("Please add review notes (at least 5 characters) before recording a decision.", "danger")
    elif decision == "close":
        svc.close_finding(finding, user, notes)
        flash(f"Finding {finding.reference} closed.", "success")
    elif decision == "reopen":
        svc.reopen_finding(finding, user, notes)
        flash(f"Finding {finding.reference} sent back to the supervisor for a further response.", "warning")
    else:
        flash("Please choose whether to close the finding or request a further response.", "danger")
    return redirect(url_for("auditor_finding_detail", finding_id=finding.id))


# =============================================================================
# 6C. SYSTEM ADMINISTRATOR ROUTES  (MODULE A: users, configuration, reference
#     data, system maintenance)
# =============================================================================
@app.route("/admin/dashboard")
@login_required
@role_required(ROLE_ADMIN)
def admin_dashboard():
    stats = {
        "total_users": User.query.count(),
        "active_users": User.query.filter_by(is_active=True).count(),
        "disabled_users": User.query.filter_by(is_active=False).count(),
        "officers": User.query.filter_by(role=ROLE_OFFICER).count(),
        "supervisors": User.query.filter_by(role=ROLE_SUPERVISOR).count(),
        "auditors": User.query.filter_by(role=ROLE_AUDITOR).count(),
        "admins": User.query.filter_by(role=ROLE_ADMIN).count(),
        "total_cases": Case.query.count(),
        "open_findings": AuditFinding.query.filter_by(status=FINDING_OPEN).count(),
        "audit_entries": AuditLog.query.count(),
    }
    recent_users = User.query.order_by(User.created_at.desc()).limit(5).all()
    recent_audit = AuditLog.query.order_by(AuditLog.timestamp.desc(), AuditLog.id.desc()).limit(10).all()
    return render_template("admin/dashboard.html", stats=stats, recent_users=recent_users, recent_audit=recent_audit)


@app.route("/admin/users")
@login_required
@role_required(ROLE_ADMIN)
def admin_users():
    query = User.query
    role_filter = request.args.get("role", "")
    if role_filter in (ROLE_OFFICER, ROLE_SUPERVISOR, ROLE_AUDITOR, ROLE_ADMIN):
        query = query.filter_by(role=role_filter)
    status_filter = request.args.get("status", "")
    if status_filter == "active":
        query = query.filter_by(is_active=True)
    elif status_filter == "disabled":
        query = query.filter_by(is_active=False)
    search = request.args.get("q", "").strip()
    if search:
        like = f"%{search}%"
        query = query.filter(or_(User.full_name.ilike(like), User.username.ilike(like), User.email.ilike(like)))

    page = request.args.get("page", 1, type=int)
    pager = query.order_by(User.full_name).paginate(page=page, per_page=app.config["PAGE_SIZE"], error_out=False)
    filters = {k: v for k, v in (("role", role_filter), ("status", status_filter), ("q", search)) if v}
    return render_template("admin/users.html", pager=pager, filters=filters,
                           role_filter=role_filter, status_filter=status_filter, search=search)


@app.route("/admin/users/new", methods=["GET", "POST"])
@login_required
@role_required(ROLE_ADMIN)
def admin_user_new():
    if request.method == "POST":
        clean, errors = svc.validate_user_form(request.form)
        if errors:
            flash_form_errors(errors)
            return render_template("admin/user_form.html", form=request.form, roles=svc.USER_ROLES)
        user, temp_password = svc.create_user(current_user(), clean)
        flash(f"Account '{user.username}' created. Temporary password: {temp_password} "
              f"(copy this now - it will not be shown again; ask the user to change it after logging in).", "success")
        return redirect(url_for("admin_user_detail", user_id=user.id))
    return render_template("admin/user_form.html", form={}, roles=svc.USER_ROLES)


@app.route("/admin/users/<int:user_id>")
@login_required
@role_required(ROLE_ADMIN)
def admin_user_detail(user_id):
    user = db.get_or_404(User, user_id)
    actions = (
        AuditLog.query.filter_by(user_id=user.id)
        .order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
        .limit(30)
        .all()
    )
    return render_template("admin/user_detail.html", user=user, actions=actions, roles=svc.USER_ROLES)


@app.route("/admin/users/<int:user_id>/toggle-active", methods=["POST"])
@login_required
@role_required(ROLE_ADMIN)
def admin_user_toggle_active(user_id):
    actor = current_user()
    user = db.get_or_404(User, user_id)
    if user.id == actor.id:
        flash("You cannot disable your own account.", "danger")
    else:
        svc.set_user_active(user, not user.is_active, actor)
        flash(f"Account '{user.username}' {'enabled' if user.is_active else 'disabled'}.", "success")
    return redirect(url_for("admin_user_detail", user_id=user.id))


@app.route("/admin/users/<int:user_id>/role", methods=["POST"])
@login_required
@role_required(ROLE_ADMIN)
def admin_user_role(user_id):
    actor = current_user()
    user = db.get_or_404(User, user_id)
    new_role = request.form.get("role", "").strip().upper()
    if new_role not in (ROLE_OFFICER, ROLE_SUPERVISOR, ROLE_AUDITOR, ROLE_ADMIN):
        flash("Please choose a valid role.", "danger")
    elif user.id == actor.id:
        flash("You cannot change your own role.", "danger")
    elif new_role == user.role:
        flash("No change - the user already has that role.", "info")
    else:
        svc.change_user_role(user, new_role, actor)
        flash(f"Role for '{user.username}' changed to {new_role}.", "success")
    return redirect(url_for("admin_user_detail", user_id=user.id))


@app.route("/admin/users/<int:user_id>/reset-password", methods=["POST"])
@login_required
@role_required(ROLE_ADMIN)
def admin_user_reset_password(user_id):
    actor = current_user()
    user = db.get_or_404(User, user_id)
    temp_password = svc.reset_user_password(user, actor)
    flash(f"Password reset for '{user.username}'. Temporary password: {temp_password} "
          f"(copy this now - it will not be shown again; ask the user to change it after logging in).", "success")
    return redirect(url_for("admin_user_detail", user_id=user.id))


@app.route("/admin/configuration", methods=["GET", "POST"])
@login_required
@role_required(ROLE_ADMIN)
def admin_configuration():
    if request.method == "POST":
        clean, errors = svc.validate_configuration_form(request.form)
        if errors:
            flash_form_errors(errors)
        else:
            actor = current_user()
            for key, value in clean.items():
                svc.set_setting(key, value, actor)
            svc.log_audit(actor, "SYSTEM_CONFIG_UPDATED", f"{actor.full_name} updated the system configuration.")
            db.session.commit()
            flash("System configuration updated.", "success")
            return redirect(url_for("admin_configuration"))

    values = {}
    for key, meta in CONFIGURABLE_SETTINGS.items():
        cast = bool if meta["type"] == "bool" else int
        values[key] = svc.get_setting(key, app.config.get(key), cast=cast)
    return render_template("admin/configuration.html", settings=CONFIGURABLE_SETTINGS, values=values)


@app.route("/admin/reference-data")
@login_required
@role_required(ROLE_ADMIN)
def admin_reference_data():
    category = request.args.get("category", REFERENCE_CATEGORY_INCIDENT_TYPE)
    if category not in REFERENCE_CATEGORIES:
        category = REFERENCE_CATEGORY_INCIDENT_TYPE
    items = (
        ReferenceData.query.filter_by(category=category)
        .order_by(ReferenceData.sort_order, ReferenceData.label)
        .all()
    )
    return render_template("admin/reference_data.html", items=items, category=category,
                           categories=REFERENCE_CATEGORIES,
                           needs_code=(category == REFERENCE_CATEGORY_NON_REGISTRATION))


@app.route("/admin/reference-data/new", methods=["POST"])
@login_required
@role_required(ROLE_ADMIN)
def admin_reference_data_new():
    actor = current_user()
    category = request.form.get("category", "").strip()
    label = request.form.get("label", "").strip()
    code = request.form.get("code", "").strip().upper() or None

    if category not in REFERENCE_CATEGORIES:
        flash("Please choose a valid reference-data category.", "danger")
    elif len(label) < 2 or len(label) > 150:
        flash("The label must be 2 to 150 characters.", "danger")
    elif category == REFERENCE_CATEGORY_NON_REGISTRATION and not code:
        flash("A short code (e.g. NR07) is required for a non-registration reason.", "danger")
    else:
        svc.add_reference_data(category, code, label, actor)
        flash("Reference data item added.", "success")
    return redirect(url_for("admin_reference_data", category=category))


@app.route("/admin/reference-data/<int:item_id>/toggle", methods=["POST"])
@login_required
@role_required(ROLE_ADMIN)
def admin_reference_data_toggle(item_id):
    item = db.get_or_404(ReferenceData, item_id)
    svc.toggle_reference_data(item, current_user())
    flash(f"'{item.label}' {'reactivated' if item.is_active else 'deactivated'}.", "success")
    return redirect(url_for("admin_reference_data", category=item.category))


@app.route("/admin/maintenance")
@login_required
@role_required(ROLE_ADMIN)
def admin_maintenance():
    stats = {
        "users": User.query.count(),
        "cases": Case.query.count(),
        "activities": CaseActivity.query.count(),
        "audit_logs": AuditLog.query.count(),
        "escalations": Escalation.query.count(),
        "non_registrations": NonRegistration.query.count(),
        "findings": AuditFinding.query.count(),
        "active_lockouts": len([k for k, v in FAILED_LOGINS.items() if v]),
    }
    return render_template("admin/maintenance.html", stats=stats,
                           standstill_days=app.config["CASE_STANDSTILL_DAYS"])


@app.route("/admin/maintenance/standstill-check", methods=["POST"])
@login_required
@role_required(ROLE_ADMIN)
def admin_run_standstill():
    actor = current_user()
    created = svc.run_standstill_check()
    svc.log_audit(actor, "MAINTENANCE_STANDSTILL_CHECK",
                  f"{actor.full_name} ran the standstill check manually: {len(created)} new escalation(s).")
    db.session.commit()
    flash(f"Standstill check complete: {len(created)} new escalation(s) created.", "info")
    return redirect(url_for("admin_maintenance"))


@app.route("/admin/maintenance/clear-lockouts", methods=["POST"])
@login_required
@role_required(ROLE_ADMIN)
def admin_clear_lockouts():
    actor = current_user()
    count = len(FAILED_LOGINS)
    FAILED_LOGINS.clear()
    svc.log_audit(actor, "MAINTENANCE_LOCKOUTS_CLEARED",
                  f"{actor.full_name} cleared {count} login-lockout record(s).")
    db.session.commit()
    flash("Login lockouts cleared.", "success")
    return redirect(url_for("admin_maintenance"))


@app.route("/admin/maintenance/export-audit")
@login_required
@role_required(ROLE_ADMIN)
def admin_export_audit():
    actor = current_user()
    entries = AuditLog.query.order_by(AuditLog.timestamp.desc(), AuditLog.id.desc()).all()
    svc.log_audit(actor, "AUDIT_TRAIL_EXPORTED", f"{actor.full_name} exported the full audit trail ({len(entries)} entries).")
    db.session.commit()
    return svc.build_audit_log_csv(entries)


# =============================================================================
# 7. ERROR PAGES  (friendly messages - no technical details for normal users)
# =============================================================================
def render_error(code, title, message):
    return render_template("error.html", code=code, title=title, message=message), code


@app.errorhandler(403)
def error_403(error):
    return render_error(403, "Access denied",
                        "You do not have permission to open this page. The attempt has been recorded.")


@app.errorhandler(404)
def error_404(error):
    return render_error(404, "Page not found", "We could not find the page you asked for.")


@app.errorhandler(405)
def error_405(error):
    return render_error(405, "Action not allowed", "That action is not allowed on this page.")


@app.errorhandler(CSRFError)
def error_csrf(error):
    return render_error(400, "Form expired",
                        "Your form session expired or was not valid. Please go back, refresh the page and try again.")


@app.errorhandler(500)
def error_500(error):
    db.session.rollback()
    return render_error(500, "Something went wrong",
                        "An unexpected error occurred. Nothing was lost - please try again or contact your supervisor.")


# =============================================================================
# 8. DATABASE START-UP  (SHARED)
# =============================================================================
def init_database():
    """Create the tables (if they do not exist), the audit-protection triggers and the demo data."""
    with app.app_context():
        db.create_all()
        connection = db.session.connection()
        for statement in IMMUTABILITY_TRIGGERS:
            connection.exec_driver_sql(statement)
        db.session.commit()
        svc.seed_reference_data()  # incident types / non-registration reasons - not gated by SEED_DEMO_DATA
        if app.config["SEED_DEMO_DATA"]:
            seed_database()


@app.cli.command("init-db")
def init_db_command():
    """Terminal command:  flask --app app init-db"""
    init_database()
    print("Database ready (saps_docket.db).")


@app.cli.command("check-standstill")
def check_standstill_command():
    """Terminal command:  flask --app app check-standstill"""
    created = svc.run_standstill_check()
    print(f"{len(created)} new escalation(s) created.")


init_database()  # runs whenever the app starts, so no manual database step is needed

if __name__ == "__main__":
    app.run(debug=app.config["DEBUG_MODE"], host="127.0.0.1", port=5000)


# =============================================================================
# 1. APP SETUP  (SHARED)
# =============================================================================
app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)
csrf = CSRFProtect(app)  # every POST form must contain the hidden csrf_token field

if not app.config["SECRET_KEY_FROM_ENV"]:
    print("WARNING: SECRET_KEY is not set in .env - using a temporary random key "
          "(everyone is logged out when the server restarts).")

REFERENCE_PATTERN = re.compile(r"^SAPS-\d{4}-\d{6}$")
FAILED_LOGINS = {}  # username -> list of failed-attempt times (kept in memory; prototype only)


# ---- Jinja filters: show times in South African time --------------------------
def to_local(value):
    if isinstance(value, datetime):
        return value + timedelta(hours=app.config["DISPLAY_UTC_OFFSET_HOURS"])
    return value


@app.template_filter("dt")
def filter_datetime(value):
    """15 Sep 2026, 10:32"""
    return to_local(value).strftime("%d %b %Y, %H:%M") if value else "-"


@app.template_filter("longdate")
def filter_longdate(value):
    """15 September 2026"""
    return to_local(value).strftime("%d %B %Y") if value else "-"


@app.template_filter("shortdate")
def filter_shortdate(value):
    """15 Sep"""
    return to_local(value).strftime("%d %b") if value else "-"


@app.template_filter("ago")
def filter_ago(value):
    """'today', '1 day ago', '12 days ago'"""
    if not value:
        return "-"
    days = (utcnow() - value).days
    if days <= 0:
        return "today"
    return "1 day ago" if days == 1 else f"{days} days ago"


# =============================================================================
# 2. AUTHENTICATION HELPERS + DECORATORS  (SHARED)
# =============================================================================
def current_user():
    """The logged-in User (or None). Looked up once per request."""
    if "user" not in g:
        user = None
        user_id = session.get("user_id")
        if user_id:
            user = db.session.get(User, user_id)
            if user is not None and not user.is_active:
                user = None
            if user is None:
                session.pop("user_id", None)
        g.user = user
    return g.user


def home_for(user):
    """Where each role lands after login."""
    if user is None:
        return url_for("login")
    if user.is_supervisor:
        return url_for("supervisor_dashboard")
    if user.is_auditor:
        return url_for("auditor_dashboard")
    if user.is_admin:
        return url_for("admin_dashboard")
    return url_for("officer_dashboard")


OWN_AREA_PREFIX = {
    ROLE_OFFICER: "/officer",
    ROLE_SUPERVISOR: "/supervisor",
    ROLE_AUDITOR: "/auditor",
    ROLE_ADMIN: "/admin",
}


def login_required(view):
    """Only logged-in users may open the page."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def role_required(*roles):
    """Only users with one of the given roles may open the page. Usage: @role_required("OFFICER")"""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if user is None:
                flash("Please log in to continue.", "warning")
                return redirect(url_for("login", next=request.path))
            if user.role not in roles:
                svc.log_audit(user, "ACCESS_DENIED", f"Tried to open {request.path} without permission.")
                db.session.commit()
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def is_locked_out(identifier):
    """Too many wrong passwords in a short time -> temporary lock."""
    window = timedelta(minutes=app.config["LOGIN_LOCKOUT_MINUTES"])
    recent = [t for t in FAILED_LOGINS.get(identifier, []) if utcnow() - t < window]
    FAILED_LOGINS[identifier] = recent
    return len(recent) >= app.config["LOGIN_MAX_ATTEMPTS"]


@app.before_request
def load_dynamic_settings():
    """Copy any admin-configured overrides (System Configuration page) onto app.config."""
    svc.apply_dynamic_settings(app)


@app.context_processor
def inject_template_variables():
    """Variables that every template can use."""
    return {
        "current_user": current_user(),
        "CASE_STATUSES": CASE_STATUSES,
        "STATUS_OPEN": STATUS_OPEN,
        "STATUS_UNDER_INVESTIGATION": STATUS_UNDER_INVESTIGATION,
        "STATUS_CLOSED": STATUS_CLOSED,
        "home_url": home_for(current_user()),
        "standstill_days": app.config["CASE_STANDSTILL_DAYS"],
    }





# =============================================================================
# 3. PUBLIC ROUTES  (MODULE A: landing page, login, logout)
# =============================================================================






@app.route("/")
def index():
    return render_template("index.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    user = current_user()
    if user:
        return redirect(home_for(user))

    if request.method == "POST":
        identifier = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")

        if not identifier or not password:
            flash("Enter your username (or e-mail) and your password.", "danger")
            return render_template("login.html", username=identifier)

        if is_locked_out(identifier):
            svc.log_audit(None, "LOGIN_LOCKED", f"Login blocked for '{identifier}' after too many failed attempts.")
            db.session.commit()
            flash(
                f"Too many failed attempts. Please wait {app.config['LOGIN_LOCKOUT_MINUTES']} minutes and try again.",
                "danger",
            )
            return render_template("login.html", username=identifier)

        candidate = User.query.filter(
            or_(func.lower(User.username) == identifier, func.lower(User.email) == identifier)
        ).first()

        if candidate and candidate.is_active and candidate.check_password(password):
            FAILED_LOGINS.pop(identifier, None)
            session.clear()                 # start a fresh session (prevents session fixation)
            session["user_id"] = candidate.id
            session.permanent = True        # expires after SESSION_TIMEOUT_MINUTES of inactivity
            candidate.last_login_at = utcnow()
            svc.log_audit(candidate, "LOGIN_SUCCESS", f"{candidate.full_name} logged in.")
            db.session.commit()
            flash(f"Login successful. Welcome, {candidate.full_name}.", "success")

            # Only follow the ?next= link if it stays inside this user's own area.
            target = request.args.get("next", "")
            own_area = OWN_AREA_PREFIX.get(candidate.role, "")
            if own_area and target.startswith(own_area) and not target.startswith("//"):
                return redirect(target)
            return redirect(home_for(candidate))

        FAILED_LOGINS.setdefault(identifier, []).append(utcnow())
        svc.log_audit(
            candidate,
            "LOGIN_FAILED",
            f"Failed login attempt for '{identifier}'.",
        )
        db.session.commit()
        flash("Incorrect username or password.", "danger")
        return render_template("login.html", username=identifier)

    return render_template("login.html", username="")


@app.route("/logout", methods=["GET", "POST"])
def logout():
    user = current_user()
    if user:
        svc.log_audit(user, "LOGOUT", f"{user.full_name} logged out.")
        db.session.commit()
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


# =============================================================================
# 4. CITIZEN PORTAL  (MODULE A: case reference + e-mail OTP)
# =============================================================================
def get_verified_citizen_case():
    """The Case the citizen proved access to with an OTP, or None (also None when the session expired)."""
    case_id = session.get("citizen_case_id")
    stamp = session.get("citizen_verified_at")
    if not case_id or not stamp:
        return None
    try:
        verified_at = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if utcnow() - verified_at > timedelta(minutes=app.config["CITIZEN_SESSION_MINUTES"]):
        clear_citizen_session()
        flash("Your viewing session expired. Please request a new OTP.", "warning")
        return None
    return db.session.get(Case, case_id)


def clear_citizen_session():
    for key in ("citizen_case_id", "citizen_verified_at", "citizen_pending_ref"):
        session.pop(key, None)


@app.route("/citizen")
def citizen_portal():
    return render_template("citizen/portal.html", verified=get_verified_citizen_case() is not None)


@app.route("/citizen/request-otp", methods=["GET", "POST"])
def citizen_request_otp():
    if request.method == "GET":
        return redirect(url_for("citizen_portal"))

    reference = request.form.get("case_reference", "").strip().upper()
    if not REFERENCE_PATTERN.match(reference):
        flash("Please enter your case reference exactly as e-mailed to you, e.g. SAPS-2026-000001.", "danger")
        return redirect(url_for("citizen_portal"))

    case = Case.query.filter_by(case_reference=reference).first()
    if case is not None:
        # The OTP is ALWAYS sent to the e-mail stored on the case - the citizen cannot choose it.
        code, status = svc.issue_otp(case)
        if status == "created":
            sent, mode = svc.email_otp(case, code)
            svc.log_audit(None, "OTP_REQUESTED", f"OTP requested for case {case.case_reference} (delivery: {mode}).", case)
            if mode == "console":
                flash("Development mode: e-mail is not configured, so the OTP was printed in the server console.", "info")
        else:
            svc.log_audit(None, "OTP_REQUEST_THROTTLED", "OTP requested again too soon - no new OTP created.", case)
        db.session.commit()

    # The same message is shown whether or not the reference exists, so nobody can
    # use this page to find out which case references are real.
    session["citizen_pending_ref"] = reference
    flash("OTP sent. If the case reference is correct, a one-time PIN has been e-mailed to the address "
          "registered on the case.", "success")
    return redirect(url_for("citizen_verify_otp"))


@app.route("/citizen/verify-otp", methods=["GET", "POST"])
def citizen_verify_otp():
    reference = session.get("citizen_pending_ref")
    if not reference:
        flash("Please enter your case reference first.", "warning")
        return redirect(url_for("citizen_portal"))

    if request.method == "POST":
        entered = request.form.get("otp", "").strip()
        if not re.match(r"^\d{6}$", entered):
            flash("The OTP is 6 digits. Please check the e-mail and try again.", "danger")
            return render_template("citizen/verify_otp.html", reference=reference)

        case = Case.query.filter_by(case_reference=reference).first()
        result, attempts_left = svc.verify_otp(case, entered) if case else ("none", 0)

        if result == "ok":
            session.pop("citizen_pending_ref", None)
            session["citizen_case_id"] = case.id
            session["citizen_verified_at"] = utcnow().isoformat()
            svc.log_audit(None, "OTP_VERIFIED", "Citizen verified OTP successfully.", case)
            db.session.commit()
            flash("OTP verified. You can now see the status of your case.", "success")
            return redirect(url_for("citizen_status"))

        if case:
            svc.log_audit(None, "OTP_FAILED", f"OTP verification failed ({result}).", case)
            db.session.commit()

        if result == "invalid":
            flash(f"Invalid OTP. You have {attempts_left} attempt(s) left.", "danger")
        elif result == "expired":
            flash("Expired OTP. Please request a new one.", "danger")
        elif result == "locked":
            flash("Too many wrong attempts. This OTP is now cancelled - please request a new one.", "danger")
        else:
            flash("Invalid OTP. It may have been used already or replaced - please request a new one.", "danger")
        return render_template("citizen/verify_otp.html", reference=reference)

    return render_template("citizen/verify_otp.html", reference=reference)


@app.route("/citizen/status")
def citizen_status():
    case = get_verified_citizen_case()
    if case is None:
        flash("Please verify with an OTP to view your case.", "warning")
        return redirect(url_for("citizen_portal"))

    svc.log_case_view(None, case, "CITIZEN_STATUS_VIEWED", "Citizen viewed the case status after OTP verification.")

    # Only these citizen-safe values go to the template. No audit log,
    # no officer descriptions, no statement, no other citizens' data.
    timeline = [
        {
            "when": activity.created_at,
            "text": CITIZEN_ACTIVITY_LABELS.get(activity.activity_type, "A progress update was recorded on your case."),
        }
        for activity in case.activities
    ]
    info = {
        "reference": case.case_reference,
        "status": case.status,
        "registered": case.created_at,
        "last_update": case.last_activity_at,
        "incident_type": case.incident_type,
        "incident_date": case.incident_date,
        "officer_name": svc.citizen_officer_name(case.assigned_officer),
        "current_activity": timeline[0]["text"] if timeline else None,
        "timeline": timeline,
    }
    return render_template("citizen/status.html", info=info)


@app.route("/citizen/logout", methods=["GET", "POST"])
def citizen_logout():
    clear_citizen_session()
    flash("You have left the case tracking portal.", "info")
    return redirect(url_for("citizen_portal"))


# =============================================================================
# 5. OFFICER ROUTES  (MODULE A: dashboard, lists, detail, update pages)
# =============================================================================
def involved_clause(user):
    """SQL condition: cases the officer registered OR is assigned to."""
    return or_(Case.registered_by_id == user.id, Case.assigned_officer_id == user.id)


def pending_escalation_clause():
    return Case.escalations.any(Escalation.status == ESCALATION_PENDING)


def apply_case_filters(query):
    """Shared by the officer and supervisor case lists: status filter + search box."""
    status = request.args.get("status", "")
    if status in CASE_STATUSES:
        query = query.filter(Case.status == status)
    search = request.args.get("q", "").strip()
    if search:
        like = f"%{search}%"
        query = query.filter(
            or_(
                Case.case_reference.ilike(like),
                Case.complainant_name.ilike(like),
                Case.incident_location.ilike(like),
            )
        )
    return query


def get_case_for_officer(case_id):
    """Load a case and make sure this officer is allowed to open it."""
    user = current_user()
    case = db.get_or_404(Case, case_id)
    if not svc.officer_can_access_case(user, case):
        svc.log_audit(user, "ACCESS_DENIED", f"Tried to open case {case.case_reference} without permission.", case)
        db.session.commit()
        abort(403)
    return case


def flash_form_errors(errors):
    for message in errors:
        flash(message, "danger")


@app.route("/officer/dashboard")
@login_required
@role_required(ROLE_OFFICER)
def officer_dashboard():
    user = current_user()
    svc.run_standstill_check()  # keeps the "requiring attention" numbers up to date
    involved = involved_clause(user)

    stats = {
        "registered": Case.query.filter_by(registered_by_id=user.id).count(),
        "assigned": Case.query.filter_by(assigned_officer_id=user.id).count(),
        "open": Case.query.filter(involved, Case.status == STATUS_OPEN).count(),
        "investigating": Case.query.filter(involved, Case.status == STATUS_UNDER_INVESTIGATION).count(),
        "closed": Case.query.filter(involved, Case.status == STATUS_CLOSED).count(),
        "attention": Case.query.filter(involved, pending_escalation_clause()).count(),
    }
    recent = (
        CaseActivity.query.join(Case, CaseActivity.case_id == Case.id)
        .filter(involved)
        .order_by(CaseActivity.created_at.desc(), CaseActivity.id.desc())
        .limit(8)
        .all()
    )
    attention_cases = (
        Case.query.filter(involved, pending_escalation_clause())
        .order_by(Case.last_activity_at.asc())
        .limit(5)
        .all()
    )
    return render_template("officer/dashboard.html", stats=stats, recent=recent, attention_cases=attention_cases)


@app.route("/officer/cases")
@login_required
@role_required(ROLE_OFFICER)
def officer_cases():
    user = current_user()
    view = request.args.get("view", "all")
    if view == "registered":
        query = Case.query.filter(Case.registered_by_id == user.id)
        title = "Cases I registered"
    elif view == "assigned":
        query = Case.query.filter(Case.assigned_officer_id == user.id)
        title = "Cases assigned to me"
    elif view == "attention":
        query = Case.query.filter(involved_clause(user), pending_escalation_clause())
        title = "Cases requiring attention"
    else:
        view = "all"
        query = Case.query.filter(involved_clause(user))
        title = "My cases"

    query = apply_case_filters(query)
    page = request.args.get("page", 1, type=int)
    pager = query.order_by(Case.last_activity_at.desc(), Case.id.desc()).paginate(
        page=page, per_page=app.config["PAGE_SIZE"], error_out=False
    )
    filters = {k: v for k, v in (("view", view), ("status", request.args.get("status", "")),
                                 ("q", request.args.get("q", ""))) if v}
    return render_template("officer/cases.html", pager=pager, view=view, title=title, filters=filters,
                           status_filter=request.args.get("status", ""), search=request.args.get("q", ""))


@app.route("/officer/cases/register", methods=["GET", "POST"])
@login_required
@role_required(ROLE_OFFICER)
def officer_register_case():
    user = current_user()
    if request.method == "POST":
        clean, errors = svc.validate_case_form(request.form, include_statement=True)
        if errors:
            flash_form_errors(errors)
            return render_template("officer/register_case.html", form=request.form,
                                   incident_types=svc.get_incident_types(), today=svc.local_today().isoformat())

        case = svc.register_case(user, clean)
        flash(f"Case registered successfully. Case reference: {case.case_reference}", "success")

        sent, mode = svc.email_case_reference(case, user)
        if sent:
            flash(f"Case reference emailed to {case.complainant_email}.", "success")
        elif mode == "console":
            flash("E-mail is not configured (development mode): the reference e-mail was printed in the server console.", "warning")
        else:
            flash("The case was saved, but the reference e-mail could not be sent. "
                  "Give the reference to the complainant and use 'Resend reference' later.", "warning")
        flash("You can now assign the case to an officer below.", "info")
        return redirect(url_for("officer_case_detail", case_id=case.id))

    return render_template("officer/register_case.html", form={}, incident_types=svc.get_incident_types(),
                           today=svc.local_today().isoformat())


@app.route("/officer/case/<int:case_id>")
@login_required
@role_required(ROLE_OFFICER)
def officer_case_detail(case_id):
    user = current_user()
    case = get_case_for_officer(case_id)
    svc.log_case_view(user, case)
    officers = User.query.filter_by(role=ROLE_OFFICER, is_active=True).order_by(User.full_name).all()
    return render_template(
        "officer/case_detail.html",
        case=case,
        officers=officers,
        audit_entries=case.audit_logs[:60],
        next_statuses=svc.allowed_next_statuses(case),
    )


@app.route("/officer/case/<int:case_id>/update", methods=["GET", "POST"])
@login_required
@role_required(ROLE_OFFICER)
def officer_case_update(case_id):
    user = current_user()
    case = get_case_for_officer(case_id)

    if request.method == "POST":
        clean, errors = svc.validate_progress_form(case, request.form)
        if errors:
            flash_form_errors(errors)
        else:
            svc.record_progress_update(case, user, clean["activity_type"], clean["description"], commit=False)
            if clean["new_status"]:
                svc.change_status(case, clean["new_status"], user, clean["description"], commit=False)
            db.session.commit()
            flash("Progress update recorded successfully.", "success")
            if clean["new_status"]:
                flash(f"Status changed to {clean['new_status']}.", "success")
            return redirect(url_for("officer_case_detail", case_id=case.id))

    return render_template(
        "officer/update_case.html",
        case=case,
        form=request.form if request.method == "POST" else {},
        activity_types=MANUAL_ACTIVITY_TYPES,
        next_statuses=svc.allowed_next_statuses(case),
    )


@app.route("/officer/case/<int:case_id>/assign", methods=["POST"])
@login_required
@role_required(ROLE_OFFICER)
def officer_case_assign(case_id):
    user = current_user()
    case = get_case_for_officer(case_id)

    if case.is_closed:
        flash("A closed case cannot be assigned. Re-open it first.", "danger")
        return redirect(url_for("officer_case_detail", case_id=case.id))

    officer_id = request.form.get("officer_id", "").strip()
    note = request.form.get("note", "").strip()
    officer = None
    if officer_id.isdigit():
        officer = User.query.filter_by(id=int(officer_id), role=ROLE_OFFICER, is_active=True).first()

    if officer is None:
        flash("Please choose an officer to assign the case to.", "danger")
    elif len(note) > 500:
        flash("The assignment note may not be longer than 500 characters.", "danger")
    elif officer.id == case.assigned_officer_id:
        flash(f"The case is already assigned to {officer.full_name}.", "info")
    else:
        svc.assign_case(case, officer, user, note)
        flash(f"Assignment successful. Case assigned to {officer.full_name}.", "success")
    return redirect(url_for("officer_case_detail", case_id=case.id))


@app.route("/officer/case/<int:case_id>/edit", methods=["GET", "POST"])
@login_required
@role_required(ROLE_OFFICER)
def officer_case_edit(case_id):
    user = current_user()
    case = get_case_for_officer(case_id)
    if case.is_closed:
        flash("A closed case cannot be edited. Re-open it first.", "danger")
        return redirect(url_for("officer_case_detail", case_id=case.id))

    if request.method == "POST":
        clean, errors = svc.validate_case_form(request.form, include_statement=False)
        if errors:
            flash_form_errors(errors)
            return render_template("officer/edit_case.html", case=case, form=request.form,
                                   incident_types=svc.get_incident_types(), today=svc.local_today().isoformat())
        if svc.correct_case_details(case, user, clean):
            flash("Case details updated. The change (old and new value) was written to the audit trail.", "success")
        else:
            flash("No changes were made.", "info")
        return redirect(url_for("officer_case_detail", case_id=case.id))

    form = {
        "complainant_name": case.complainant_name,
        "complainant_email": case.complainant_email,
        "complainant_phone": case.complainant_phone or "",
        "incident_date": case.incident_date.isoformat(),
        "incident_location": case.incident_location,
        "incident_type": case.incident_type,
    }
    return render_template("officer/edit_case.html", case=case, form=form, incident_types=svc.get_incident_types(),
                           today=svc.local_today().isoformat())


@app.route("/officer/case/<int:case_id>/resend-reference", methods=["POST"])
@login_required
@role_required(ROLE_OFFICER)
def officer_case_resend(case_id):
    user = current_user()
    case = get_case_for_officer(case_id)
    sent, mode = svc.email_case_reference(case, user)
    if sent:
        flash(f"Case reference emailed to {case.complainant_email}.", "success")
    elif mode == "console":
        flash("E-mail is not configured (development mode): the message was printed in the server console.", "warning")
    else:
        flash("The e-mail could not be sent. Please check the mail settings in .env.", "danger")
    return redirect(url_for("officer_case_detail", case_id=case.id))


@app.route("/officer/profile", methods=["GET", "POST"])
@login_required
@role_required(ROLE_OFFICER)
def officer_profile():
    user = current_user()

    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if not user.check_password(current_password):
            flash("Your current password is incorrect.", "danger")
        elif len(new_password) < 8 or not re.search(r"[A-Za-z]", new_password) or not re.search(r"\d", new_password):
            flash("The new password must be at least 8 characters and contain letters and numbers.", "danger")
        elif new_password != confirm:
            flash("The new password and the confirmation do not match.", "danger")
        elif new_password == current_password:
            flash("The new password must be different from the current one.", "danger")
        else:
            user.set_password(new_password)
            svc.log_audit(user, "PASSWORD_CHANGED", f"{user.full_name} changed their password.")
            db.session.commit()
            flash("Password changed successfully.", "success")
            return redirect(url_for("officer_profile"))

    my_actions = (
        AuditLog.query.filter_by(user_id=user.id)
        .order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
        .limit(15)
        .all()
    )
    counts = {
        "registered": Case.query.filter_by(registered_by_id=user.id).count(),
        "assigned": Case.query.filter_by(assigned_officer_id=user.id).count(),
        "actions": AuditLog.query.filter_by(user_id=user.id).count(),
    }
    return render_template("officer/profile.html", my_actions=my_actions, counts=counts)


@app.route("/officer/non-registrations")
@login_required
@role_required(ROLE_OFFICER)
def officer_refusals():
    user = current_user()
    page = request.args.get("page", 1, type=int)
    pager = (
        NonRegistration.query.filter_by(officer_id=user.id)
        .order_by(NonRegistration.created_at.desc(), NonRegistration.id.desc())
        .paginate(page=page, per_page=app.config["PAGE_SIZE"], error_out=False)
    )
    return render_template("officer/refusals.html", pager=pager)


@app.route("/officer/non-registrations/new", methods=["GET", "POST"])
@login_required
@role_required(ROLE_OFFICER)
def officer_refusal_new():
    user = current_user()
    if request.method == "POST":
        clean, errors = svc.validate_non_registration_form(request.form)
        if errors:
            flash_form_errors(errors)
            return render_template("officer/refusal_form.html", form=request.form, reasons=svc.get_non_registration_reasons())
        record = svc.record_non_registration(user, clean)
        flash(f"Non-registration record {record.reference} saved. It cannot be edited or deleted.", "success")
        return redirect(url_for("officer_refusals"))
    return render_template("officer/refusal_form.html", form={}, reasons=svc.get_non_registration_reasons())


# =============================================================================
# 6. SUPERVISOR ROUTES  (MODULE A: oversight pages)
# =============================================================================
@app.route("/supervisor/dashboard")
@login_required
@role_required(ROLE_SUPERVISOR)
def supervisor_dashboard():
    svc.run_standstill_check()

    stats = {
        "total": Case.query.count(),
        "open": Case.query.filter_by(status=STATUS_OPEN).count(),
        "investigating": Case.query.filter_by(status=STATUS_UNDER_INVESTIGATION).count(),
        "closed": Case.query.filter_by(status=STATUS_CLOSED).count(),
        "attention": Escalation.query.filter_by(status=ESCALATION_PENDING).count(),
        "refusals": NonRegistration.query.count(),
        "findings_pending": AuditFinding.query.filter_by(status=FINDING_OPEN).count(),
    }
    pending = (
        Escalation.query.filter_by(status=ESCALATION_PENDING)
        .order_by(Escalation.created_at.desc())
        .limit(5)
        .all()
    )
    recent_audit = AuditLog.query.order_by(AuditLog.timestamp.desc(), AuditLog.id.desc()).limit(10).all()
    workload = (
        db.session.query(User, func.count(Case.id))
        .outerjoin(Case, and_(Case.assigned_officer_id == User.id, Case.status != STATUS_CLOSED))
        .filter(User.role == ROLE_OFFICER)
        .group_by(User.id)
        .order_by(User.full_name)
        .all()
    )
    return render_template("supervisor/dashboard.html", stats=stats, pending=pending,
                           recent_audit=recent_audit, workload=workload)


@app.route("/supervisor/cases")
@login_required
@role_required(ROLE_SUPERVISOR)
def supervisor_cases():
    svc.run_standstill_check()
    query = Case.query
    attention_only = request.args.get("attention", "") == "1"
    if attention_only:
        query = query.filter(pending_escalation_clause())
    query = apply_case_filters(query)

    page = request.args.get("page", 1, type=int)
    pager = query.order_by(Case.last_activity_at.desc(), Case.id.desc()).paginate(
        page=page, per_page=app.config["PAGE_SIZE"], error_out=False
    )
    filters = {k: v for k, v in (("status", request.args.get("status", "")),
                                 ("q", request.args.get("q", "")),
                                 ("attention", "1" if attention_only else "")) if v}
    return render_template("supervisor/cases.html", pager=pager, filters=filters,
                           status_filter=request.args.get("status", ""),
                           search=request.args.get("q", ""), attention_only=attention_only)


@app.route("/supervisor/case/<int:case_id>")
@login_required
@role_required(ROLE_SUPERVISOR)
def supervisor_case_detail(case_id):
    user = current_user()
    case = db.get_or_404(Case, case_id)
    svc.log_case_view(user, case)
    officers = User.query.filter_by(role=ROLE_OFFICER, is_active=True).order_by(User.full_name).all()
    return render_template(
        "supervisor/case_detail.html",
        case=case,
        officers=officers,
        audit_entries=case.audit_logs[:100],
        supervisor_actions=SUPERVISOR_ACTIONS,
        form={},
    )


@app.route("/supervisor/escalations")
@login_required
@role_required(ROLE_SUPERVISOR)
def supervisor_escalations():
    svc.run_standstill_check()
    status = request.args.get("status", ESCALATION_PENDING)
    query = Escalation.query
    if status in (ESCALATION_PENDING, ESCALATION_REVIEWED):
        query = query.filter_by(status=status)
    else:
        status = "ALL"
    page = request.args.get("page", 1, type=int)
    pager = query.order_by(Escalation.created_at.desc(), Escalation.id.desc()).paginate(
        page=page, per_page=app.config["PAGE_SIZE"], error_out=False
    )
    return render_template("supervisor/escalations.html", pager=pager, status=status,
                           filters={"status": status},
                           pending_count=Escalation.query.filter_by(status=ESCALATION_PENDING).count())


@app.route("/supervisor/escalations/scan", methods=["POST"])
@login_required
@role_required(ROLE_SUPERVISOR)
def supervisor_escalation_scan():
    created = svc.run_standstill_check()
    if created:
        flash(f"Standstill check complete: {len(created)} case(s) flagged for attention.", "warning")
    else:
        flash(f"Standstill check complete: no new cases found "
              f"(threshold: {app.config['CASE_STANDSTILL_DAYS']} day(s) without activity).", "info")
    return redirect(url_for("supervisor_escalations"))


@app.route("/supervisor/escalation/<int:escalation_id>/review", methods=["POST"])
@login_required
@role_required(ROLE_SUPERVISOR)
def supervisor_escalation_review(escalation_id):
    user = current_user()
    escalation = db.get_or_404(Escalation, escalation_id)
    case = escalation.case

    if escalation.status != ESCALATION_PENDING:
        flash("This escalation has already been reviewed.", "info")
        return redirect(url_for("supervisor_case_detail", case_id=case.id))

    officers = User.query.filter_by(role=ROLE_OFFICER, is_active=True).order_by(User.full_name).all()
    clean, errors = svc.validate_escalation_review_form(request.form, {o.id for o in officers})
    if errors:
        flash_form_errors(errors)
        return render_template(
            "supervisor/case_detail.html",
            case=case, officers=officers, audit_entries=case.audit_logs[:100],
            supervisor_actions=SUPERVISOR_ACTIONS, form=request.form,
        )

    reassign_officer = db.session.get(User, clean["reassign_to"]) if clean["reassign_to"] else None
    svc.review_escalation(escalation, user, clean["supervisor_action"], clean["supervisor_notes"], reassign_officer)
    flash("Supervisor review recorded in the audit trail. The case continues.", "success")
    return redirect(url_for("supervisor_case_detail", case_id=case.id))


@app.route("/supervisor/audit")
@login_required
@role_required(ROLE_SUPERVISOR)
def supervisor_audit():
    query = AuditLog.query.outerjoin(Case, AuditLog.case_id == Case.id)

    reference = request.args.get("q", "").strip()
    user_filter = request.args.get("user", "").strip()
    action_filter = request.args.get("action", "").strip()
    if reference:
        query = query.filter(Case.case_reference.ilike(f"%{reference}%"))
    if user_filter.isdigit():
        query = query.filter(AuditLog.user_id == int(user_filter))
    if action_filter:
        query = query.filter(AuditLog.action == action_filter)

    page = request.args.get("page", 1, type=int)
    pager = query.order_by(AuditLog.timestamp.desc(), AuditLog.id.desc()).paginate(
        page=page, per_page=20, error_out=False
    )
    users = User.query.order_by(User.full_name).all()
    actions = [row[0] for row in db.session.query(AuditLog.action).distinct().order_by(AuditLog.action).all()]
    filters = {k: v for k, v in (("q", reference), ("user", user_filter), ("action", action_filter)) if v}
    return render_template("supervisor/audit.html", pager=pager, users=users, actions=actions, filters=filters,
                           reference=reference, user_filter=user_filter, action_filter=action_filter)


@app.route("/supervisor/non-registrations")
@login_required
@role_required(ROLE_SUPERVISOR)
def supervisor_refusals():
    page = request.args.get("page", 1, type=int)
    pager = NonRegistration.query.order_by(
        NonRegistration.created_at.desc(), NonRegistration.id.desc()
    ).paginate(page=page, per_page=app.config["PAGE_SIZE"], error_out=False)
    return render_template("supervisor/refusals.html", pager=pager)


@app.route("/supervisor/findings")
@login_required
@role_required(ROLE_SUPERVISOR)
def supervisor_findings():
    status = request.args.get("status", FINDING_OPEN)
    query = AuditFinding.query
    if status in FINDING_STATUSES:
        query = query.filter_by(status=status)
    else:
        status = "ALL"
    page = request.args.get("page", 1, type=int)
    pager = query.order_by(AuditFinding.created_at.desc(), AuditFinding.id.desc()).paginate(
        page=page, per_page=app.config["PAGE_SIZE"], error_out=False
    )
    return render_template("supervisor/findings.html", pager=pager, status=status,
                           pending_count=AuditFinding.query.filter_by(status=FINDING_OPEN).count())


@app.route("/supervisor/findings/<int:finding_id>")
@login_required
@role_required(ROLE_SUPERVISOR)
def supervisor_finding_detail(finding_id):
    finding = db.get_or_404(AuditFinding, finding_id)
    return render_template("supervisor/finding_detail.html", finding=finding, form={})


@app.route("/supervisor/findings/<int:finding_id>/respond", methods=["POST"])
@login_required
@role_required(ROLE_SUPERVISOR)
def supervisor_finding_respond(finding_id):
    """SUPERVISOR responds to / investigates an audit finding raised by an auditor."""
    user = current_user()
    finding = db.get_or_404(AuditFinding, finding_id)

    if finding.status != FINDING_OPEN:
        flash("This finding does not currently require a response.", "info")
        return redirect(url_for("supervisor_finding_detail", finding_id=finding.id))

    response_text = request.form.get("response_text", "").strip()
    if len(response_text) < 10:
        flash("Please describe the investigation or action taken (at least 10 characters).", "danger")
        return render_template("supervisor/finding_detail.html", finding=finding, form=request.form)

    svc.respond_to_finding(finding, user, response_text)
    flash("Response submitted to the auditor for review.", "success")
    return redirect(url_for("supervisor_finding_detail", finding_id=finding.id))



#=============================================================================
# 6B. INTERNAL AUDITOR / IPID OFFICER ROUTES  (MODULE A: read-only oversight)
# =============================================================================

@app.route("/auditor/dashboard")
@login_required
@role_required(ROLE_AUDITOR)
def auditor_dashboard():
    stats = {
        "total": Case.query.count(),
        "open": Case.query.filter_by(status=STATUS_OPEN).count(),
        "investigating": Case.query.filter_by(status=STATUS_UNDER_INVESTIGATION).count(),
        "closed": Case.query.filter_by(status=STATUS_CLOSED).count(),
        "escalated": Escalation.query.filter_by(status=ESCALATION_PENDING).count(),
        "refusals": NonRegistration.query.count(),
        "audit_entries": AuditLog.query.count(),
        "findings_to_review": AuditFinding.query.filter_by(status=FINDING_RESPONDED).count(),
    }
    recent_audit = AuditLog.query.order_by(AuditLog.timestamp.desc(), AuditLog.id.desc()).limit(10).all()
    recent_refusals = (
        NonRegistration.query.order_by(NonRegistration.created_at.desc(), NonRegistration.id.desc())
        .limit(5)
        .all()
    )
    access_denied_count = AuditLog.query.filter_by(action="ACCESS_DENIED").count()
    return render_template(
        "auditor/dashboard.html",
        stats=stats,
        recent_audit=recent_audit,
        recent_refusals=recent_refusals,
        access_denied_count=access_denied_count,
    )


@app.route("/auditor/cases")
@login_required
@role_required(ROLE_AUDITOR)
def auditor_cases():
    query = apply_case_filters(Case.query)
    page = request.args.get("page", 1, type=int)
    pager = query.order_by(Case.last_activity_at.desc(), Case.id.desc()).paginate(
        page=page, per_page=app.config["PAGE_SIZE"], error_out=False
    )
    filters = {k: v for k, v in (("status", request.args.get("status", "")),
                                 ("q", request.args.get("q", ""))) if v}
    return render_template("auditor/cases.html", pager=pager, filters=filters,
                           status_filter=request.args.get("status", ""),
                           search=request.args.get("q", ""))


@app.route("/auditor/case/<int:case_id>")
@login_required
@role_required(ROLE_AUDITOR)
def auditor_case_detail(case_id):
    user = current_user()
    case = db.get_or_404(Case, case_id)
    svc.log_case_view(user, case, "CASE_VIEWED", f"Auditor reviewed case {case.case_reference}.")
    return render_template("auditor/case_details.html", case=case, audit_entries=case.audit_logs[:200])


@app.route("/auditor/audit")
@login_required
@role_required(ROLE_AUDITOR)
def auditor_audit():
    query = AuditLog.query.outerjoin(Case, AuditLog.case_id == Case.id)

    reference = request.args.get("q", "").strip()
    user_filter = request.args.get("user", "").strip()
    action_filter = request.args.get("action", "").strip()
    if reference:
        query = query.filter(Case.case_reference.ilike(f"%{reference}%"))
    if user_filter.isdigit():
        query = query.filter(AuditLog.user_id == int(user_filter))
    if action_filter:
        query = query.filter(AuditLog.action == action_filter)

    page = request.args.get("page", 1, type=int)
    pager = query.order_by(AuditLog.timestamp.desc(), AuditLog.id.desc()).paginate(
        page=page, per_page=20, error_out=False
    )
    users = User.query.order_by(User.full_name).all()
    actions = [row[0] for row in db.session.query(AuditLog.action).distinct().order_by(AuditLog.action).all()]
    filters = {k: v for k, v in (("q", reference), ("user", user_filter), ("action", action_filter)) if v}
    return render_template("auditor/audit.html", pager=pager, users=users, actions=actions, filters=filters,
                           reference=reference, user_filter=user_filter, action_filter=action_filter)


@app.route("/auditor/non-registrations")
@login_required
@role_required(ROLE_AUDITOR)
def auditor_refusals():
    page = request.args.get("page", 1, type=int)
    pager = NonRegistration.query.order_by(
        NonRegistration.created_at.desc(), NonRegistration.id.desc()
    ).paginate(page=page, per_page=app.config["PAGE_SIZE"], error_out=False)
    return render_template("auditor/refusals.html", pager=pager)


@app.route("/auditor/case/<int:case_id>/export")
@login_required
@role_required(ROLE_AUDITOR)
def auditor_case_export(case_id):
    """Export Report - a downloadable CSV of the case details, timeline and audit trail."""
    user = current_user()
    case = db.get_or_404(Case, case_id)
    svc.log_audit(user, "CASE_REPORT_EXPORTED", f"Exported a report for case {case.case_reference}.", case)
    db.session.commit()
    return svc.build_case_report_csv(case)


@app.route("/auditor/case/<int:case_id>/flag", methods=["GET", "POST"])
@login_required
@role_required(ROLE_AUDITOR)
def auditor_flag_case(case_id):
    """Flag for Review - the auditor creates an audit finding against a case."""
    user = current_user()
    case = db.get_or_404(Case, case_id)

    if request.method == "POST":
        clean, errors = svc.validate_finding_form(request.form)
        if errors:
            flash_form_errors(errors)
            return render_template("auditor/flag_case.html", case=case, form=request.form,
                                   severities=FINDING_SEVERITIES)
        finding = svc.create_finding(case, user, clean)
        flash(f"Finding {finding.reference} raised and sent to supervisors for a response.", "success")
        return redirect(url_for("auditor_finding_detail", finding_id=finding.id))

    return render_template("auditor/flag_case.html", case=case, form={}, severities=FINDING_SEVERITIES)


@app.route("/auditor/findings")
@login_required
@role_required(ROLE_AUDITOR)
def auditor_findings():
    status = request.args.get("status", "")
    query = AuditFinding.query
    if status in FINDING_STATUSES:
        query = query.filter_by(status=status)
    else:
        status = ""
    page = request.args.get("page", 1, type=int)
    pager = query.order_by(AuditFinding.created_at.desc(), AuditFinding.id.desc()).paginate(
        page=page, per_page=app.config["PAGE_SIZE"], error_out=False
    )
    return render_template("auditor/findings.html", pager=pager, status=status,
                           open_count=AuditFinding.query.filter_by(status=FINDING_OPEN).count(),
                           responded_count=AuditFinding.query.filter_by(status=FINDING_RESPONDED).count())


@app.route("/auditor/findings/<int:finding_id>")
@login_required
@role_required(ROLE_AUDITOR)
def auditor_finding_detail(finding_id):
    finding = db.get_or_404(AuditFinding, finding_id)
    return render_template("auditor/finding_detail.html", finding=finding)


@app.route("/auditor/findings/<int:finding_id>/review", methods=["POST"])
@login_required
@role_required(ROLE_AUDITOR)
def auditor_finding_review(finding_id):
    """The auditor reviews the supervisor's response: close the finding, or send it back."""
    user = current_user()
    finding = db.get_or_404(AuditFinding, finding_id)

    if finding.status != FINDING_RESPONDED:
        flash("This finding is not currently awaiting review.", "info")
        return redirect(url_for("auditor_finding_detail", finding_id=finding.id))

    decision = request.form.get("decision", "").strip()
    notes = request.form.get("review_notes", "").strip()
    if len(notes) < 5:
        flash("Please add review notes (at least 5 characters) before recording a decision.", "danger")
    elif decision == "close":
        svc.close_finding(finding, user, notes)
        flash(f"Finding {finding.reference} closed.", "success")
    elif decision == "reopen":
        svc.reopen_finding(finding, user, notes)
        flash(f"Finding {finding.reference} sent back to the supervisor for a further response.", "warning")
    else:
        flash("Please choose whether to close the finding or request a further response.", "danger")
    return redirect(url_for("auditor_finding_detail", finding_id=finding.id))


# =============================================================================
# 6C. SYSTEM ADMINISTRATOR ROUTES  (MODULE A: users, configuration, reference
#     data, system maintenance)
# =============================================================================
@app.route("/admin/dashboard")
@login_required
@role_required(ROLE_ADMIN)
def admin_dashboard():
    stats = {
        "total_users": User.query.count(),
        "active_users": User.query.filter_by(is_active=True).count(),
        "disabled_users": User.query.filter_by(is_active=False).count(),
        "officers": User.query.filter_by(role=ROLE_OFFICER).count(),
        "supervisors": User.query.filter_by(role=ROLE_SUPERVISOR).count(),
        "auditors": User.query.filter_by(role=ROLE_AUDITOR).count(),
        "admins": User.query.filter_by(role=ROLE_ADMIN).count(),
        "total_cases": Case.query.count(),
        "open_findings": AuditFinding.query.filter_by(status=FINDING_OPEN).count(),
        "audit_entries": AuditLog.query.count(),
    }
    recent_users = User.query.order_by(User.created_at.desc()).limit(5).all()
    recent_audit = AuditLog.query.order_by(AuditLog.timestamp.desc(), AuditLog.id.desc()).limit(10).all()
    return render_template("admin/dashboard.html", stats=stats, recent_users=recent_users, recent_audit=recent_audit)


@app.route("/admin/users")
@login_required
@role_required(ROLE_ADMIN)
def admin_users():
    query = User.query
    role_filter = request.args.get("role", "")
    if role_filter in (ROLE_OFFICER, ROLE_SUPERVISOR, ROLE_AUDITOR, ROLE_ADMIN):
        query = query.filter_by(role=role_filter)
    status_filter = request.args.get("status", "")
    if status_filter == "active":
        query = query.filter_by(is_active=True)
    elif status_filter == "disabled":
        query = query.filter_by(is_active=False)
    search = request.args.get("q", "").strip()
    if search:
        like = f"%{search}%"
        query = query.filter(or_(User.full_name.ilike(like), User.username.ilike(like), User.email.ilike(like)))

    page = request.args.get("page", 1, type=int)
    pager = query.order_by(User.full_name).paginate(page=page, per_page=app.config["PAGE_SIZE"], error_out=False)
    filters = {k: v for k, v in (("role", role_filter), ("status", status_filter), ("q", search)) if v}
    return render_template("admin/users.html", pager=pager, filters=filters,
                           role_filter=role_filter, status_filter=status_filter, search=search)


@app.route("/admin/users/new", methods=["GET", "POST"])
@login_required
@role_required(ROLE_ADMIN)
def admin_user_new():
    if request.method == "POST":
        clean, errors = svc.validate_user_form(request.form)
        if errors:
            flash_form_errors(errors)
            return render_template("admin/user_form.html", form=request.form, roles=svc.USER_ROLES)
        user, temp_password = svc.create_user(current_user(), clean)
        flash(f"Account '{user.username}' created. Temporary password: {temp_password} "
              f"(copy this now - it will not be shown again; ask the user to change it after logging in).", "success")
        return redirect(url_for("admin_user_detail", user_id=user.id))
    return render_template("admin/user_form.html", form={}, roles=svc.USER_ROLES)


@app.route("/admin/users/<int:user_id>")
@login_required
@role_required(ROLE_ADMIN)
def admin_user_detail(user_id):
    user = db.get_or_404(User, user_id)
    actions = (
        AuditLog.query.filter_by(user_id=user.id)
        .order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
        .limit(30)
        .all()
    )
    return render_template("admin/user_detail.html", user=user, actions=actions, roles=svc.USER_ROLES)


@app.route("/admin/users/<int:user_id>/toggle-active", methods=["POST"])
@login_required
@role_required(ROLE_ADMIN)
def admin_user_toggle_active(user_id):
    actor = current_user()
    user = db.get_or_404(User, user_id)
    if user.id == actor.id:
        flash("You cannot disable your own account.", "danger")
    else:
        svc.set_user_active(user, not user.is_active, actor)
        flash(f"Account '{user.username}' {'enabled' if user.is_active else 'disabled'}.", "success")
    return redirect(url_for("admin_user_detail", user_id=user.id))


@app.route("/admin/users/<int:user_id>/role", methods=["POST"])
@login_required
@role_required(ROLE_ADMIN)
def admin_user_role(user_id):
    actor = current_user()
    user = db.get_or_404(User, user_id)
    new_role = request.form.get("role", "").strip().upper()
    if new_role not in (ROLE_OFFICER, ROLE_SUPERVISOR, ROLE_AUDITOR, ROLE_ADMIN):
        flash("Please choose a valid role.", "danger")
    elif user.id == actor.id:
        flash("You cannot change your own role.", "danger")
    elif new_role == user.role:
        flash("No change - the user already has that role.", "info")
    else:
        svc.change_user_role(user, new_role, actor)
        flash(f"Role for '{user.username}' changed to {new_role}.", "success")
    return redirect(url_for("admin_user_detail", user_id=user.id))


@app.route("/admin/users/<int:user_id>/reset-password", methods=["POST"])
@login_required
@role_required(ROLE_ADMIN)
def admin_user_reset_password(user_id):
    actor = current_user()
    user = db.get_or_404(User, user_id)
    temp_password = svc.reset_user_password(user, actor)
    flash(f"Password reset for '{user.username}'. Temporary password: {temp_password} "
          f"(copy this now - it will not be shown again; ask the user to change it after logging in).", "success")
    return redirect(url_for("admin_user_detail", user_id=user.id))


@app.route("/admin/configuration", methods=["GET", "POST"])
@login_required
@role_required(ROLE_ADMIN)
def admin_configuration():
    if request.method == "POST":
        clean, errors = svc.validate_configuration_form(request.form)
        if errors:
            flash_form_errors(errors)
        else:
            actor = current_user()
            for key, value in clean.items():
                svc.set_setting(key, value, actor)
            svc.log_audit(actor, "SYSTEM_CONFIG_UPDATED", f"{actor.full_name} updated the system configuration.")
            db.session.commit()
            flash("System configuration updated.", "success")
            return redirect(url_for("admin_configuration"))

    values = {}
    for key, meta in CONFIGURABLE_SETTINGS.items():
        cast = bool if meta["type"] == "bool" else int
        values[key] = svc.get_setting(key, app.config.get(key), cast=cast)
    return render_template("admin/configuration.html", settings=CONFIGURABLE_SETTINGS, values=values)


@app.route("/admin/reference-data")
@login_required
@role_required(ROLE_ADMIN)
def admin_reference_data():
    category = request.args.get("category", REFERENCE_CATEGORY_INCIDENT_TYPE)
    if category not in REFERENCE_CATEGORIES:
        category = REFERENCE_CATEGORY_INCIDENT_TYPE
    items = (
        ReferenceData.query.filter_by(category=category)
        .order_by(ReferenceData.sort_order, ReferenceData.label)
        .all()
    )
    return render_template("admin/reference_data.html", items=items, category=category,
                           categories=REFERENCE_CATEGORIES,
                           needs_code=(category == REFERENCE_CATEGORY_NON_REGISTRATION))


@app.route("/admin/reference-data/new", methods=["POST"])
@login_required
@role_required(ROLE_ADMIN)
def admin_reference_data_new():
    actor = current_user()
    category = request.form.get("category", "").strip()
    label = request.form.get("label", "").strip()
    code = request.form.get("code", "").strip().upper() or None

    if category not in REFERENCE_CATEGORIES:
        flash("Please choose a valid reference-data category.", "danger")
    elif len(label) < 2 or len(label) > 150:
        flash("The label must be 2 to 150 characters.", "danger")
    elif category == REFERENCE_CATEGORY_NON_REGISTRATION and not code:
        flash("A short code (e.g. NR07) is required for a non-registration reason.", "danger")
    else:
        svc.add_reference_data(category, code, label, actor)
        flash("Reference data item added.", "success")
    return redirect(url_for("admin_reference_data", category=category))


@app.route("/admin/reference-data/<int:item_id>/toggle", methods=["POST"])
@login_required
@role_required(ROLE_ADMIN)
def admin_reference_data_toggle(item_id):
    item = db.get_or_404(ReferenceData, item_id)
    svc.toggle_reference_data(item, current_user())
    flash(f"'{item.label}' {'reactivated' if item.is_active else 'deactivated'}.", "success")
    return redirect(url_for("admin_reference_data", category=item.category))


@app.route("/admin/maintenance")
@login_required
@role_required(ROLE_ADMIN)
def admin_maintenance():
    stats = {
        "users": User.query.count(),
        "cases": Case.query.count(),
        "activities": CaseActivity.query.count(),
        "audit_logs": AuditLog.query.count(),
        "escalations": Escalation.query.count(),
        "non_registrations": NonRegistration.query.count(),
        "findings": AuditFinding.query.count(),
        "active_lockouts": len([k for k, v in FAILED_LOGINS.items() if v]),
    }
    return render_template("admin/maintenance.html", stats=stats,
                           standstill_days=app.config["CASE_STANDSTILL_DAYS"])


@app.route("/admin/maintenance/standstill-check", methods=["POST"])
@login_required
@role_required(ROLE_ADMIN)
def admin_run_standstill():
    actor = current_user()
    created = svc.run_standstill_check()
    svc.log_audit(actor, "MAINTENANCE_STANDSTILL_CHECK",
                  f"{actor.full_name} ran the standstill check manually: {len(created)} new escalation(s).")
    db.session.commit()
    flash(f"Standstill check complete: {len(created)} new escalation(s) created.", "info")
    return redirect(url_for("admin_maintenance"))


@app.route("/admin/maintenance/clear-lockouts", methods=["POST"])
@login_required
@role_required(ROLE_ADMIN)
def admin_clear_lockouts():
    actor = current_user()
    count = len(FAILED_LOGINS)
    FAILED_LOGINS.clear()
    svc.log_audit(actor, "MAINTENANCE_LOCKOUTS_CLEARED",
                  f"{actor.full_name} cleared {count} login-lockout record(s).")
    db.session.commit()
    flash("Login lockouts cleared.", "success")
    return redirect(url_for("admin_maintenance"))


@app.route("/admin/maintenance/export-audit")
@login_required
@role_required(ROLE_ADMIN)
def admin_export_audit():
    actor = current_user()
    entries = AuditLog.query.order_by(AuditLog.timestamp.desc(), AuditLog.id.desc()).all()
    svc.log_audit(actor, "AUDIT_TRAIL_EXPORTED", f"{actor.full_name} exported the full audit trail ({len(entries)} entries).")
    db.session.commit()
    return svc.build_audit_log_csv(entries)


# =============================================================================
# 7. ERROR PAGES  (friendly messages - no technical details for normal users)
# =============================================================================
def render_error(code, title, message):
    return render_template("error.html", code=code, title=title, message=message), code


@app.errorhandler(403)
def error_403(error):
    return render_error(403, "Access denied",
                        "You do not have permission to open this page. The attempt has been recorded.")


@app.errorhandler(404)
def error_404(error):
    return render_error(404, "Page not found", "We could not find the page you asked for.")


@app.errorhandler(405)
def error_405(error):
    return render_error(405, "Action not allowed", "That action is not allowed on this page.")


@app.errorhandler(CSRFError)
def error_csrf(error):
    return render_error(400, "Form expired",
                        "Your form session expired or was not valid. Please go back, refresh the page and try again.")


@app.errorhandler(500)
def error_500(error):
    db.session.rollback()
    return render_error(500, "Something went wrong",
                        "An unexpected error occurred. Nothing was lost - please try again or contact your supervisor.")


# =============================================================================
# 8. DATABASE START-UP  (SHARED)
# =============================================================================
def init_database():
    """Create the tables (if they do not exist), the audit-protection triggers and the demo data."""
    with app.app_context():
        db.create_all()
        connection = db.session.connection()
        for statement in IMMUTABILITY_TRIGGERS:
            connection.exec_driver_sql(statement)
        db.session.commit()
        svc.seed_reference_data()  # incident types / non-registration reasons - not gated by SEED_DEMO_DATA
        if app.config["SEED_DEMO_DATA"]:
            seed_database()


@app.cli.command("init-db")
def init_db_command():
    """Terminal command:  flask --app app init-db"""
    init_database()
    print("Database ready (saps_docket.db).")


@app.cli.command("check-standstill")
def check_standstill_command():
    """Terminal command:  flask --app app check-standstill"""
    created = svc.run_standstill_check()
    print(f"{len(created)} new escalation(s) created.")


init_database()  # runs whenever the app starts, so no manual database step is needed

if __name__ == "__main__":
    app.run(debug=app.config["DEBUG_MODE"], host="127.0.0.1", port=5000)
