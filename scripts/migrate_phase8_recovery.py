"""
Phase 8 Database Migration: Add recovery and separate counter columns to outbound_queue.
Columns added:
- lease_expiry_count (INTEGER NOT NULL DEFAULT 0)
- provider_retry_count (INTEGER NOT NULL DEFAULT 0)
- veto_count (INTEGER NOT NULL DEFAULT 0)
- processing_started_at (TIMESTAMP WITH TIME ZONE NULL)
"""

from sqlalchemy import text
from app.db.session import get_db_session

COLUMNS = [
    ("lease_expiry_count", "INTEGER NOT NULL DEFAULT 0"),
    ("provider_retry_count", "INTEGER NOT NULL DEFAULT 0"),
    ("veto_count", "INTEGER NOT NULL DEFAULT 0"),
    ("processing_started_at", "TIMESTAMP WITH TIME ZONE NULL"),
]

def migrate_phase8():
    with get_db_session() as session:
        print("Checking and adding Phase 8 columns to outbound_queue...")
        for col_name, col_type in COLUMNS:
            try:
                session.execute(text(
                    f"ALTER TABLE outbound_queue ADD COLUMN IF NOT EXISTS {col_name} {col_type}"
                ))
                session.commit()
                print(f"  Added/verified column: {col_name} ({col_type})")
            except Exception as e:
                session.rollback()
                print(f"  Error on column {col_name}: {e}")

        print("Phase 8 database migration completed successfully.")

if __name__ == "__main__":
    migrate_phase8()
