import sys
from sqlalchemy import text
from app.db.session import engine
from app.db.models import Base

def run_migration():
    print("Applying Phase 2 DB migrations...")
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        # 1. Update audit_reason_enum
        for val in ['tone_approved', 'tone_rollback']:
            try:
                conn.execute(text(f"ALTER TYPE audit_reason_enum ADD VALUE IF NOT EXISTS '{val}'"))
                print(f"AuditReason enum '{val}' ensured.")
            except Exception as e:
                print(f"Error adding enum {val}: {e}")

        # 2. Create tables
        Base.metadata.create_all(engine)
        print("Base.metadata.create_all completed.")

        # 3. Verify tables
        tables = [r[0] for r in conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")).fetchall()]
        print("Public tables:", tables)
        assert "tone_studio_sessions" in tables, "tone_studio_sessions missing"
        assert "tone_references" in tables, "tone_references missing"

        # 4. Verify enums
        enums = [r[0] for r in conn.execute(text("SELECT enumlabel FROM pg_enum JOIN pg_type ON pg_enum.enumtypid = pg_type.oid WHERE typname = 'audit_reason_enum'")).fetchall()]
        print("AuditReason enum values:", enums)
        assert "tone_approved" in enums, "tone_approved enum missing"
        assert "tone_rollback" in enums, "tone_rollback enum missing"

    print("Phase 2 DB migration completed successfully.")

if __name__ == "__main__":
    run_migration()
