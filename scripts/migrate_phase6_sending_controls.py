import sys
from sqlalchemy import text
from app.db.session import engine
from app.db.models import Base

def run_migration():
    print("Applying Phase 6 DB migrations (Sending Controls & Enums)...")
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        # 1. Update audit_reason_enum in PostgreSQL
        new_enums = [
            'policy_block',
            'spam_complaint',
            'sending_started',
            'sending_stopped',
            'auto_stop_triggered',
            'auto_stop_cleared',
            'segment_paused',
            'segment_resumed',
        ]
        for val in new_enums:
            try:
                conn.execute(text(f"ALTER TYPE audit_reason_enum ADD VALUE IF NOT EXISTS '{val}'"))
                print(f"AuditReason enum '{val}' ensured.")
            except Exception as e:
                print(f"Note/Error adding enum {val}: {e}")

        # 2. Create tables
        Base.metadata.create_all(engine)
        print("Base.metadata.create_all completed.")

        # 3. Verify system_controls table
        tables = [r[0] for r in conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")).fetchall()]
        print("Public tables:", tables)
        assert "system_controls" in tables, "system_controls table missing"

    print("Phase 6 DB migration completed successfully.")

if __name__ == "__main__":
    run_migration()
