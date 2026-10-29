"""
Phase 7 Database Migration: Add AuditReason enums for Inbound Reply Classification & Operator Replies.
"""

from sqlalchemy import text
from app.db.session import get_db_session

NEW_ENUM_VALUES = [
    "out_of_office_snooze",
    "inbound_reply_classified",
    "operator_email_sent",
]

def migrate_phase7():
    with get_db_session() as session:
        print("Checking and adding new AuditReason enum values...")
        for val in NEW_ENUM_VALUES:
            try:
                session.execute(text(f"ALTER TYPE audit_reason_enum ADD VALUE IF NOT EXISTS '{val}'"))
                session.commit()
                print(f"  Added enum value: {val}")
            except Exception as e:
                session.rollback()
                print(f"  Note on {val}: {e}")

        print("Phase 7 database migration completed successfully.")

if __name__ == "__main__":
    migrate_phase7()
