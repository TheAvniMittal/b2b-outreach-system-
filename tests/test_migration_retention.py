import ast
from pathlib import Path
import pytest

def get_tables_missing_retention_class_from_ast(tree: ast.AST):
    """
    Analyzes AST of a migration file to find all op.create_table calls
    and verify that each one includes a 'retention_class' column.
    """
    missing = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # Check for op.create_table(...)
            is_create_table = False
            if isinstance(node.func, ast.Attribute) and node.func.attr == "create_table":
                is_create_table = True

            if is_create_table:
                # First argument or 'name'/'table_name' kwarg is table name
                table_name = "unknown"
                if node.args and isinstance(node.args[0], ast.Constant):
                    table_name = str(node.args[0].value)

                # Check all columns defined in args and keywords
                has_retention_class = False
                for arg in node.args[1:]:
                    if isinstance(arg, ast.Call):
                        # check if it's sa.Column('retention_class', ...) or Column('retention_class', ...)
                        if arg.args and isinstance(arg.args[0], ast.Constant) and arg.args[0].value == "retention_class":
                            has_retention_class = True
                            break

                if not has_retention_class:
                    missing.append(table_name)
    return missing


def check_migration_file_for_retention_class(file_path: Path):
    source = file_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(file_path))
    return get_tables_missing_retention_class_from_ast(tree)


def test_all_migrations_declare_retention_class():
    """
    CI Check (§14.8a):
    Every table added in any migration MUST declare a 'retention_class' column.
    A migration adding a table without one fails CI.
    """
    versions_dir = Path(__file__).resolve().parent.parent / "alembic" / "versions"
    assert versions_dir.exists(), f"Versions directory not found: {versions_dir}"

    migration_files = list(versions_dir.glob("*.py"))
    assert len(migration_files) > 0, "No migration files found to check"

    failures = {}
    for mig in migration_files:
        missing = check_migration_file_for_retention_class(mig)
        if missing:
            failures[mig.name] = missing

    assert not failures, (
        f"CI CHECK FAILED (§14.8a): Migration(s) create table(s) without a 'retention_class' field: {failures}"
    )


def test_ci_check_catches_table_without_retention_class():
    """
    Negative test ensuring that the AST checker actually fails if a table lacks retention_class.
    """
    bad_migration_source = '''
from alembic import op
import sqlalchemy as sa

def upgrade():
    op.create_table(
        "bad_table",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String())
    )
'''
    tree = ast.parse(bad_migration_source)
    missing = get_tables_missing_retention_class_from_ast(tree)
    assert missing == ["bad_table"], f"Expected ['bad_table'] missing retention_class, got: {missing}"
