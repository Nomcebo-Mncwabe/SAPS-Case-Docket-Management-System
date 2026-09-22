"""
create_or_fix_auditor.py

Creates the 'auditor1' demo user if it does not exist in THIS database, or
fixes its password/active status if it does. Touches only this one user
row - nothing else in your database is read or modified.

Run from inside your saps_docket/ folder:

    python create_or_fix_auditor.py
"""
import os
import sys

sys.path.insert(0, os.getcwd())

from app import app          # noqa: E402
from config import ROLE_AUDITOR  # noqa: E402
from models import User, db  # noqa: E402

USERNAME = "auditor1"
EMAIL = "auditor1@example.com"
FULL_NAME = "Nomvula Dube"
PASSWORD = "password123"

with app.app_context():
    user = User.query.filter_by(username=USERNAME).first()

    if user is None:
        # Also make sure the e-mail isn't already taken by someone else
        # (unique constraint), just in case.
        clashing_email = User.query.filter_by(email=EMAIL).first()
        if clashing_email:
            print(f"Cannot create '{USERNAME}': email {EMAIL} is already used by "
                  f"user id={clashing_email.id} username={clashing_email.username!r}.")
            print("Rename one of them first.")
            sys.exit(1)

        user = User(
            full_name=FULL_NAME,
            username=USERNAME,
            email=EMAIL,
            role=ROLE_AUDITOR,
            is_active=True,
        )
        user.set_password(PASSWORD)
        db.session.add(user)
        db.session.commit()
        print(f"Created new user '{USERNAME}' (role={ROLE_AUDITOR}) with password '{PASSWORD}'.")
    else:
        user.set_password(PASSWORD)
        user.is_active = True
        user.role = ROLE_AUDITOR
        db.session.commit()
        print(f"User '{USERNAME}' already existed (id={user.id}) - password reset to "
              f"'{PASSWORD}' and reactivated.")

    print("\nTry logging in now with auditor1 / password123.")
