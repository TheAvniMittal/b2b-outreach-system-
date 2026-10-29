"""
Phase 9 Database Migration:
1. Adds audit_reason_enum values:
   - 'catalogue_uploaded'
   - 'catalogue_attachment_toggled'
   - 'claims_uploaded'
   - 'rulebook_updated'
2. Adds columns:
   - segments.attach_catalogue (BOOLEAN NOT NULL DEFAULT TRUE)
   - catalogues.file_size_bytes (INTEGER NULL)
   - outbound_queue.attachment_checksum (VARCHAR(64) NULL)
   - approved_claims.evidence_note (VARCHAR(255) NULL)
   - approved_claims.is_active (BOOLEAN NOT NULL DEFAULT TRUE)
3. Creates rulebook_configs table via Base.metadata.create_all.
4. Initializes default RulebookConfig if table is empty.
"""

from sqlalchemy import text
from app.db.session import engine, get_db_session
from app.db.models import Base, RulebookConfig


def run_migration():
    print("Applying Phase 9 DB migrations (Catalogues, Rulebooks, Claims Configuration)...")

    # 1. Update PostgreSQL audit_reason_enum
    new_enums = [
        "catalogue_uploaded",
        "catalogue_attachment_toggled",
        "claims_uploaded",
        "rulebook_updated",
    ]
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for val in new_enums:
            try:
                conn.execute(text(f"ALTER TYPE audit_reason_enum ADD VALUE IF NOT EXISTS '{val}'"))
                print(f"AuditReason enum '{val}' ensured.")
            except Exception as e:
                print(f"Note/Error adding enum {val}: {e}")

        # 2. Add columns with IF NOT EXISTS
        alter_statements = [
            ("segments", "attach_catalogue", "BOOLEAN NOT NULL DEFAULT TRUE"),
            ("catalogues", "file_size_bytes", "INTEGER NULL"),
            ("outbound_queue", "attachment_checksum", "VARCHAR(64) NULL"),
            ("approved_claims", "evidence_note", "VARCHAR(255) NULL"),
            ("approved_claims", "is_active", "BOOLEAN NOT NULL DEFAULT TRUE"),
        ]

        for table_name, col_name, col_type in alter_statements:
            try:
                conn.execute(text(
                    f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {col_name} {col_type}"
                ))
                print(f"Column {table_name}.{col_name} ({col_type}) ensured.")
            except Exception as e:
                print(f"Note/Error adding column {col_name} to {table_name}: {e}")

    # 3. Create tables (rulebook_configs)
    Base.metadata.create_all(engine)
    print("Base.metadata.create_all completed.")

    # 4. Initialize default active rulebook if empty
    with get_db_session() as session:
        existing_rulebook = session.query(RulebookConfig).first()
        if not existing_rulebook:
            print("Creating default active RulebookConfig (Version 1)...")
            default_rulebook = RulebookConfig(
                version=1,
                name="Default House Rulebook v1",
                is_active=True,
                banned_openers=[
                    "i hope this email finds you well",
                    "i hope you are doing well",
                    "i trust this email finds you in good health",
                ],
                banned_phrases=[
                    "reaching out",
                    "leverage",
                    "synergy",
                    "circle back",
                ],
                max_paragraphs=3,
                max_sentences_per_paragraph=3,
                allow_exclamation=False,
                allow_all_caps=False,
                created_by="system",
            )
            session.add(default_rulebook)
            session.commit()
            print("Default RulebookConfig created.")

    print("Phase 9 database migration completed successfully.")


if __name__ == "__main__":
    run_migration()
