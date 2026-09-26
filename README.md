# 🇿🇦 South African Police Service (SAPS) — Case Docket Management System

![SAPS Badge](saps_docket/static/img/saps_logo.svg)

> **University Group Project Prototype (BINCT)**  
> *Enhancing accountability, transparency, and digital traceability in Community Service Centre (CSC) case docket registration.*

---

## 🌟 Overview & Branding Updates

This system has been upgraded with an authentic **South African Police Service (SAPS)** public-service visual identity:

* **Official SAPS 9-Pointed Star Emblem ([`saps_logo.svg`](saps_docket/static/img/saps_logo.svg)):** Integrated across all layouts, browser favicon, public header, sidebar navigation, and authentication panels.
* **Photographic Hero Showcase ([`saps_hero.jpg`](saps_docket/static/img/saps_hero.jpg)):** Real SAPS patrol vehicle hero photography with an overlay gradient and service motto: *"SERVE & PROTECT • DIEN EN BESKERM"*.
* **Authentic SAPS Navy & Gold Theme:**
  * **Deep Night Patrol Navy:** `#040d1a`
  * **SAPS Service Navy:** `#08172e`
  * **Uniform Navy Blue:** `#0c2344`
  * **Polished Insignia Gold:** `#d4af37` and `#b8860b`
* **Emergency Service Footer:** Featuring official national hotline links (`10111`, Crime Stop: `08600 10111`).

---

## 🏛️ System Capabilities

1. **Community Service Centre (CSC) Registration:**
   * Record complainants, victims, and incident details at the desk.
   * Auto-generate unique, tamper-proof case reference IDs (e.g. `SAPS-2026-000001`).
   * Automated dispatch of case references to complainants via email or console delivery.

2. **Chain-of-Custody & Detective Workflows:**
   * Assign investigating officers and record detailed investigation progress entries.
   * Structured case status transitions (`OPEN` → `UNDER INVESTIGATION` → `CLOSED`).
   * Permanent audit logging on every case action.

3. **Supervisory Standstill Detection & Escalations:**
   * Automated watchdog flags inactive cases exceeding configurable threshold days (`CASE_STANDSTILL_DAYS`).
   * Supervisor review queue to clear or resolve stalled dockets.

4. **Public Citizen Portal with Dual-Factor OTP:**
   * Complainants can track investigation milestones using their case reference.
   * Secured by HMAC-SHA256 one-time PIN (OTP) verification codes sent to the registered email address.

5. **Immutable Audit Ledger & Non-Registration Tracking:**
   * Dual-layer immutability: enforced both at application level and SQLite database triggers.
   * Full refusal/non-registration logging with justification tracking.

---

## 📂 Project Directory Structure

```
SAPS-Case-Docket-Management-System/
├── README.md                      <- Root repository guide (displays on GitHub)
├── .gitignore                     <- Git ignore rules
└── saps_docket/                   <- Core Application Folder
    ├── app.py                     <- Main Flask application router
    ├── config.py                  <- System settings and .env loader
    ├── models.py                  <- SQLAlchemy database schema & triggers
    ├── services.py                <- Business logic (audit, OTP, assignment, email)
    ├── seed.py                    <- Database seeder with demo accounts
    ├── run_saps.bat               <- One-click Windows batch launcher
    ├── requirements.txt           <- Python dependencies
    ├── .env.example               <- Environment variables template
    ├── static/
    │   ├── css/style.css          <- SAPS Navy & Gold theme stylesheet
    │   ├── js/main.js             <- Client-side UX scripts
    │   └── img/
    │       ├── saps_logo.svg      <- Official SAPS vector emblem
    │       ├── saps_hero.jpg      <- Optimized SAPS vehicle hero banner
    │       └── saps_police_car.jpg<- High-resolution source image
    └── templates/
        ├── base.html, index.html, login.html, error.html, _macros.html
        ├── officer/               <- Officer dashboard & case management
        ├── supervisor/            <- Supervisor dashboard & escalations
        ├── citizen/               <- Citizen tracking portal & OTP verify
        ├── auditor/               <- Auditor review & inspection portal
        └── admin/                 <- System settings & reference management
```

---

## 🚀 Quickstart & Setup Guide

### 1. Requirements
* Python 3.10+
* Virtual Environment (recommended)

### 2. Installation
```powershell
cd saps_docket
python -m venv venv
.\venv\Scripts\pip install -r requirements.txt
```

### 3. Environment Configuration
Copy `.env.example` to `.env`:
```powershell
copy .env.example .env
```
Generate a secret key (optional, random key generated automatically if blank):
```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

### 4. Running the Application
#### Option A: One-Click Batch File (Windows)
Double-click **`run_saps.bat`** in the `saps_docket` folder.

#### Option B: Terminal Command
```powershell
cd saps_docket
.\venv\Scripts\python.exe app.py
```
Open your browser at **[http://127.0.0.1:5000](http://127.0.0.1:5000)**.

---

## 🔑 Demo Login Accounts

All demo accounts use the default password: **`password123`**

| Role | Username | Purpose |
| :--- | :--- | :--- |
| **CSC Officer** | `officer1` | Register cases, update docket activities, view my cases |
| **CSC Officer** | `officer2` | Alternate officer for workload assignment |
| **Supervisor** | `supervisor1` | Assign dockets, monitor escalations, review audit log |
| **Auditor** | `auditor1` | Independent compliance inspections & audit findings |
| **Administrator** | `admin1` | Reference data & configurable system settings |

---

## 📧 Email & OTP Delivery Configuration
By default, the application runs in **Development Mode** where emails and OTPs are printed directly to the console. To enable real email delivery to external mailboxes (such as Gmail), add your credentials to `saps_docket/.env`:

```env
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USE_TLS=True
MAIL_USERNAME=your-email@gmail.com
MAIL_PASSWORD=your-16-char-app-password
MAIL_SENDER=your-email@gmail.com
```

---

## ⚖️ License & Disclaimer
This is an academic research prototype created for university educational assessment (BINCT). **It is not an official system of the South African Police Service.** Official insignia and emblems are protected under the South African Heraldry Act and are utilized here strictly under academic fair-use guidelines for educational demonstration.
