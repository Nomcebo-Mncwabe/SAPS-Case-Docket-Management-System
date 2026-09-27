# SAPS Case Docket Management System — Setup Guide

University group project prototype (BINCT). **Not an official SAPS system.**
Built with Python, Flask, Flask-SQLAlchemy, SQLite, HTML, CSS and JavaScript.

This is **your portion** of the project (landing page, login, officer pages, supervisor
pages, citizen portal, templates, CSS) plus all shared backend code (models, business
logic, audit trail, e-mail, OTP) needed for the whole application to run as one system.

---

## 1. Folder structure

```
saps_docket/
│
├── app.py                 <- MAIN FILE. Run this to start the system.
├── config.py               shared settings, read from .env
├── models.py                database tables (User, Case, CaseActivity, AuditLog, OTP, Escalation, NonRegistration)
├── services.py               business logic: registration, assignment, audit trail, e-mail, OTP, escalation
├── seed.py                    demo users + demo cases
├── requirements.txt
├── .env.example             <- copy this to .env and fill in your own values
├── .gitignore
│
├── templates/
│   ├── base.html, index.html, login.html, error.html, _macros.html
│   ├── officer/     (dashboard, cases, case_detail, register_case, edit_case,
│   │                 update_case, profile, refusals, refusal_form, _case_fields)
│   ├── supervisor/  (dashboard, cases, case_detail, escalations, audit, refusals)
│   └── citizen/     (portal, verify_otp, status)
│
└── static/
    ├── css/style.css  (SAPS Navy & Gold theme)
    ├── js/main.js
    └── img/
        ├── saps_logo.svg (Official SAPS emblem)
        └── saps_hero.jpg (Patrol vehicle hero banner)
```

## 2. Database relationships (plain English)

- A **User** (Officer or Supervisor) can **register** many Cases and can be **assigned** many Cases.
- A **Case** belongs to the officer who registered it and (optionally) an assigned officer.
- A **Case** has many **CaseActivity** rows — its progress timeline (e.g. "CCTV Requested").
- A **Case** has many **AuditLog** rows, many **OTP** rows, and many **Escalation** rows.
- **AuditLog** rows record `user_id -> action -> case_id -> timestamp`. They are **immutable**:
  the database itself refuses UPDATE/DELETE on this table (enforced twice — in SQLAlchemy and with
  a SQLite trigger), so the trail can be trusted.
- **NonRegistration** rows (refused reports) are immutable in the same way.

## 3. Route table

| Route | Method | Who | Purpose |
|---|---|---|---|
| `/` | GET | Public | Landing page |
| `/login`, `/logout` | GET/POST | Public | Authentication |
| `/citizen` | GET | Public | Enter case reference |
| `/citizen/request-otp` | POST | Public | Send OTP to the case's stored e-mail |
| `/citizen/verify-otp` | GET/POST | Public | Enter the 6-digit OTP |
| `/citizen/status` | GET | Verified citizen | View case status |
| `/officer/dashboard` | GET | Officer | Dashboard |
| `/officer/cases` | GET | Officer | My cases / assigned / attention (filters) |
| `/officer/cases/register` | GET/POST | Officer | Register new case |
| `/officer/case/<id>` | GET | Officer (involved) | Case detail |
| `/officer/case/<id>/update` | GET/POST | Officer (involved) | Add progress + change status |
| `/officer/case/<id>/assign` | POST | Officer (involved) | Assign officer |
| `/officer/case/<id>/edit` | GET/POST | Officer (involved) | Correct complainant/incident details |
| `/officer/case/<id>/resend-reference` | POST | Officer (involved) | Resend the reference e-mail |
| `/officer/non-registrations`, `/new` | GET/POST | Officer | Non-registration records |
| `/officer/profile` | GET/POST | Officer | Profile + change password |
| `/supervisor/dashboard` | GET | Supervisor | Oversight dashboard |
| `/supervisor/cases`, `/case/<id>` | GET | Supervisor | All cases |
| `/supervisor/escalations` | GET | Supervisor | Escalation queue |
| `/supervisor/escalation/<id>/review` | POST | Supervisor | Review an escalation |
| `/supervisor/audit` | GET | Supervisor | Full searchable audit trail |
| `/supervisor/non-registrations` | GET | Supervisor | Monitor refusals |

## 4. Package requirements

```
Flask==3.1.3
Flask-SQLAlchemy==3.1.1
Flask-WTF==1.3.0
python-dotenv==1.2.2
Werkzeug==3.1.7
```

---

## 5. Setup instructions (PyCharm)

**1. Open the project folder in PyCharm** (File → Open → select the `saps_docket` folder).

**2. Create a virtual environment.**
   PyCharm usually offers this automatically. If not, open the PyCharm **Terminal** tab and run:
   ```
   python -m venv .venv
   ```
   Then select it as the project interpreter: *Settings → Project → Python Interpreter*.

**3. Install the packages** (in the PyCharm terminal, with the venv active):
   ```
   pip install -r requirements.txt
   ```

**4. Set up your `.env` file.**
   Copy `.env.example` to a new file named exactly `.env` in the project root, then fill it in:
   - `SECRET_KEY`: generate one with
     ```
     python -c "import secrets; print(secrets.token_hex(32))"
     ```
     and paste the result after `SECRET_KEY=`.
   - `MAIL_USERNAME` / `MAIL_PASSWORD`: **optional for marking**. If you leave these blank,
     the app still works — e-mails (case reference and OTP) are printed to the PyCharm
     **Run** console instead of being sent, which is enough to demonstrate the feature.
     To send real e-mails with Gmail: turn on 2-Step Verification, create an
     **App Password**, and use that as `MAIL_PASSWORD` (not your normal Gmail password).
   - Everything else can be left at the provided defaults.

   `.env` is already listed in `.gitignore` — **never commit it to GitHub.**

**5. Database initialisation — nothing to do manually.**
   The bottom of `app.py` calls `init_database()` automatically every time the app starts.
   It creates all tables, the audit-immutability triggers, and (if `saps_docket.db` doesn't
   exist yet) the demo users and demo cases. You do not need to run any command yourself.

**6. Demo users created automatically:**

   | Role | Username | Password |
   |---|---|---|
   | CSC Officer | `officer1` | `password123` |
   | CSC Officer | `officer2` | `password123` |
   | CSC Officer | `officer3` | `password123` |
   | CSC Supervisor | `supervisor1` | `password123` |

   **Demo credentials only.** Change them (via *Profile → Change password*) before any real use.

**7. Run the application.**
   Right-click `app.py` in PyCharm → **Run 'app'**. Or in the terminal:
   ```
   python app.py
   ```

**8. Open the system in your browser:**
   ```
   http://127.0.0.1:5000
   ```

---

## 6. How to test each requirement

1. **Officer login** — go to `/login`, sign in as `officer1` / `password123`.
2. **Register a case** — Dashboard → *Register new case*, fill in the form, submit.
   A case reference such as `SAPS-2026-000001` appears, and the reference e-mail is either sent
   or printed in the PyCharm console (if e-mail is not configured).
3. **Assignment** — open the new case → *Assign officer* panel → choose an officer → Assign.
4. **Progress update / status change** — open the case → *Add progress update* → pick an
   activity type, write a description, optionally change status → Save.
5. **Audit trail** — as `officer1`, open *Profile* to see "My recent actions", or log in as
   `supervisor1` and open *Audit Trail* to see every action, by user, on every case.
6. **Escalation / standstill** — the standstill check runs automatically whenever the officer
   or supervisor dashboard loads. To see it immediately without waiting `CASE_STANDSTILL_DAYS`
   days, you can temporarily lower `CASE_STANDSTILL_DAYS` in `.env` to `0`, restart the app,
   and open any dashboard — a case with no recent activity will be flagged. Then log in as
   `supervisor1` → *Escalations* → *Review* a pending one.
7. **Citizen OTP** — go to `/citizen`, enter a case reference you registered (using your own
   e-mail address if you want a real inbox test), request the OTP, check your e-mail (or the
   PyCharm console), enter the 6-digit code, view the status page.
8. **Invalid OTP rejection** — on the OTP entry page, type the wrong 6 digits: you'll see
   "Invalid OTP" with attempts remaining; after 3 wrong attempts the OTP is cancelled.
9. **Unauthorized access rejection** — while logged out, try opening
   `http://127.0.0.1:5000/officer/dashboard` directly: you're redirected to login. While
   logged in as an officer, try `http://127.0.0.1:5000/supervisor/dashboard`: you get a
   403 "Access denied" page.
10. **Database persistence / CRUD** — stop and restart the app; your registered cases,
    activities and audit entries are still there (stored in `saps_docket.db`).
11. **Non-registration record** — *Non-Registrations → Record a non-registration*, choose a
    coded reason, write a justification, save. It appears on both the officer's and the
    supervisor's non-registration lists and can never be edited afterwards.

---

## 7. Common errors and fixes

| Problem | Fix |
|---|---|
| `ModuleNotFoundError: No module named 'flask_sqlalchemy'` | Activate your venv, then `pip install -r requirements.txt`. |
| Page loads with no styling | Make sure the `static/` folder sits next to `app.py`, and hard-refresh the browser (Ctrl+Shift+R). |
| Login always says "Incorrect username or password" | Confirm you're using the demo username/password exactly (case-sensitive), and that `saps_docket.db` was actually created (see below). |
| Old / wrong demo data appears | Delete `saps_docket.db` in the project folder and restart the app — it will be rebuilt with fresh demo data. |
| E-mails never arrive | That's expected if `MAIL_USERNAME`/`MAIL_PASSWORD` are blank in `.env` — check the PyCharm **Run** console, the message is printed there instead. |
| `CSRFError` / "Form expired" | Your browser had an old page open in a tab from before the last restart — refresh the page and submit the form again. |
| Port 5000 already in use | Another program (or a previous run) is using it. Stop that process, or change the port in the last line of `app.py`. |
| SECRET_KEY warning on startup | Add a `SECRET_KEY` value to `.env` (see step 4). Without it, everyone is logged out whenever you restart the server — harmless for development, but should be fixed for your final submission. |

---

## 8. Checklist

- [ ] Packages installed (`pip install -r requirements.txt`)
- [ ] `.env` created from `.env.example`, with a `SECRET_KEY` set
- [ ] Database created (`saps_docket.db` appears automatically on first run)
- [ ] Demo officer created (automatic)
- [ ] Demo supervisor created (automatic)
- [ ] Email configured (optional — console fallback works for marking)
- [ ] Application runs (`python app.py`, open `http://127.0.0.1:5000`)
- [ ] Login works
- [ ] Case registration works
- [ ] Case reference generated
- [ ] Email sent (or printed to console)
- [ ] Case assigned
- [ ] Progress update works
- [ ] Audit trail works
- [ ] Citizen OTP works
- [ ] Citizen status works
- [ ] Supervisor dashboard works
- [ ] Escalation works

---

## 9. Notes on design decisions (for your presentation)

- **Roles**: only `OFFICER` and `SUPERVISOR` exist, as documented — no separate "Investigator" login.
- **Statuses**: only `OPEN`, `UNDER INVESTIGATION`, `CLOSED`. "Reviewing CCTV footage" etc. are
  recorded as `CaseActivity` entries, not extra statuses.
- **Email instead of SMS**: both the case reference and the citizen OTP are sent by e-mail
  (via Python's built-in `smtplib`) to avoid paid SMS services, per the brief.
- **Immutable audit trail**: enforced at two levels — SQLAlchemy blocks in-app updates/deletes,
  and a SQLite trigger blocks changes even from a raw database tool.
- **Standstill days is configurable**: `CASE_STANDSTILL_DAYS` in `.env`, not hard-coded.
- **A 6th model, `NonRegistration`**, was added beyond the five listed in the brief, because the
  brief requires that a refusal record a coded reason, justification, officer identity and
  timestamp, be monitorable by supervisors, and never be deleted — the `AuditLog` table alone
  cannot hold that structured, permanent, queryable record.
