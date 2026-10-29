"""Initial schema: segments, catalogues, approved_claims, contacts, outbound_queue, send_key, suppression, audit_log

Revision ID: 0001_initial_schema
Revises: 
Create Date: 2026-09-21 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0001_initial_schema'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. catalogues table
    op.create_table(
        'catalogues',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('catalogue_file', sa.String(length=255), nullable=False),
        sa.Column('checksum', sa.String(length=64), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('registered_term_list', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('is_summary_confirmed', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('supersession_policy', sa.String(length=30), nullable=False, server_default='pin'),
        sa.Column('retention_class', sa.String(length=50), nullable=False, server_default='operational_config'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )

    # 2. segments table
    op.create_table(
        'segments',
        sa.Column('segment', sa.String(length=50), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('catalogue_id', sa.Integer(), nullable=True),
        sa.Column('allowed_angles', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('allowed_ctas', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('approved_claim_ids', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('forbidden_topics', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('target_words_min', sa.Integer(), nullable=False, server_default='80'),
        sa.Column('target_words_max', sa.Integer(), nullable=False, server_default='150'),
        sa.Column('register_spec', sa.Text(), nullable=True),
        sa.Column('cadence_override', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('opportunity_weights', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('retention_class', sa.String(length=50), nullable=False, server_default='operational_config'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['catalogue_id'], ['catalogues.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('segment')
    )

    # 3. approved_claims table
    op.create_table(
        'approved_claims',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('segment', sa.String(length=50), nullable=False),
        sa.Column('product_category', sa.String(length=100), nullable=False),
        sa.Column('claim_type', sa.String(length=50), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('valid_from', sa.Date(), nullable=False),
        sa.Column('valid_to', sa.Date(), nullable=True),
        sa.Column('last_reviewed_at', sa.Date(), nullable=False),
        sa.Column('retention_class', sa.String(length=50), nullable=False, server_default='operational_config'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['segment'], ['segments.segment']),
        sa.PrimaryKeyConstraint('id')
    )

    # 4. contacts table
    op.create_table(
        'contacts',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('company_name', sa.String(length=255), nullable=False),
        sa.Column('segment', sa.String(length=50), nullable=False),
        sa.Column('company_domain', sa.String(length=255), nullable=True),
        sa.Column('contact_name', sa.String(length=255), nullable=False),
        sa.Column('designation', sa.String(length=255), nullable=True),
        sa.Column('country', sa.String(length=100), nullable=True),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('product_interest', sa.Text(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('verification_status', sa.String(length=50), nullable=False, server_default='unknown'),
        sa.Column('verification_reason', sa.String(length=255), nullable=True),
        sa.Column('verification_stage_reached', sa.Integer(), nullable=True),
        sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='pending'),
        sa.Column('opportunity_score', sa.Float(), nullable=True),
        sa.Column('due_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('retention_class', sa.String(length=50), nullable=False, server_default='prospect_pii'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['segment'], ['segments.segment']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_contacts_email'), 'contacts', ['email'], unique=False)

    # 5. outbound_queue table
    op.create_table(
        'outbound_queue',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('contact_id', sa.Integer(), nullable=False),
        sa.Column('campaign_step', sa.String(length=50), nullable=False, server_default='intro'),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='eligible'),
        sa.Column('locked_by', sa.String(length=100), nullable=True),
        sa.Column('locked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('due_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('rendered_subject', sa.Text(), nullable=True),
        sa.Column('rendered_body', sa.Text(), nullable=True),
        sa.Column('send_key_id', sa.Integer(), nullable=True),
        sa.Column('retention_class', sa.String(length=50), nullable=False, server_default='sent_messages'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['contact_id'], ['contacts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_outbound_queue_contact_id'), 'outbound_queue', ['contact_id'], unique=False)
    op.create_index(op.f('ix_outbound_queue_due_at'), 'outbound_queue', ['due_at'], unique=False)
    op.create_index(op.f('ix_outbound_queue_lease_expires_at'), 'outbound_queue', ['lease_expires_at'], unique=False)
    op.create_index(op.f('ix_outbound_queue_status'), 'outbound_queue', ['status'], unique=False)

    # 6. send_key table
    op.create_table(
        'send_key',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('key', sa.String(length=255), nullable=False),
        sa.Column('queue_item_id', sa.Integer(), nullable=False),
        sa.Column('contact_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='sending'),
        sa.Column('provider_message_id', sa.String(length=255), nullable=True),
        sa.Column('retention_class', sa.String(length=50), nullable=False, server_default='provider_message_ids'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['contact_id'], ['contacts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['queue_item_id'], ['outbound_queue.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_send_key_contact_id'), 'send_key', ['contact_id'], unique=False)
    op.create_index(op.f('ix_send_key_key'), 'send_key', ['key'], unique=True)
    op.create_index(op.f('ix_send_key_queue_item_id'), 'send_key', ['queue_item_id'], unique=False)

    # 7. suppression table
    op.create_table(
        'suppression',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('email_hash', sa.String(length=64), nullable=False),
        sa.Column('contact_id', sa.Integer(), nullable=True),
        sa.Column('reason', sa.String(length=100), nullable=False),
        sa.Column('created_by', sa.String(length=100), nullable=False, server_default='system'),
        sa.Column('retention_class', sa.String(length=50), nullable=False, server_default='permanent'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_suppression_contact_id'), 'suppression', ['contact_id'], unique=False)
    op.create_index(op.f('ix_suppression_email_hash'), 'suppression', ['email_hash'], unique=True)

    # 8. audit_log table
    # Create PostgreSQL ENUM type for AuditReason
    audit_reason_enum = postgresql.ENUM(
        'unsubscribe_request', 'hard_bounce', 'human_reply', 'admin_pause',
        'validator_hold', 'veto_limit', 'lease_expired', 'worker_crash',
        'dead_letter', 'import_verified', 'initial_enqueue', 'reconcile_found',
        'reconcile_not_found', 'reconcile_ambiguous_hold', 'cap_enforced',
        name='audit_reason_enum'
    )
    audit_reason_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'audit_log',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('timestamp', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('actor', sa.String(length=100), nullable=False),
        sa.Column('action', sa.String(length=100), nullable=False),
        sa.Column('object_type', sa.String(length=50), nullable=False),
        sa.Column('object_id', sa.String(length=100), nullable=False),
        sa.Column('prev_state', sa.String(length=50), nullable=True),
        sa.Column('new_state', sa.String(length=50), nullable=True),
        sa.Column('reason', postgresql.ENUM(name='audit_reason_enum', create_type=False), nullable=False),
        sa.Column('rulebook_version', sa.String(length=50), nullable=True),
        sa.Column('prompt_version', sa.String(length=50), nullable=True),
        sa.Column('retention_class', sa.String(length=50), nullable=False, server_default='audit_log'),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('audit_log')
    # drop enum
    audit_reason_enum = postgresql.ENUM(name='audit_reason_enum')
    audit_reason_enum.drop(op.get_bind(), checkfirst=True)

    op.drop_index(op.f('ix_suppression_email_hash'), table_name='suppression')
    op.drop_index(op.f('ix_suppression_contact_id'), table_name='suppression')
    op.drop_table('suppression')

    op.drop_index(op.f('ix_send_key_queue_item_id'), table_name='send_key')
    op.drop_index(op.f('ix_send_key_key'), table_name='send_key')
    op.drop_index(op.f('ix_send_key_contact_id'), table_name='send_key')
    op.drop_table('send_key')

    op.drop_index(op.f('ix_outbound_queue_status'), table_name='outbound_queue')
    op.drop_index(op.f('ix_outbound_queue_lease_expires_at'), table_name='outbound_queue')
    op.drop_index(op.f('ix_outbound_queue_due_at'), table_name='outbound_queue')
    op.drop_index(op.f('ix_outbound_queue_contact_id'), table_name='outbound_queue')
    op.drop_table('outbound_queue')

    op.drop_index(op.f('ix_contacts_email'), table_name='contacts')
    op.drop_table('contacts')

    op.drop_table('approved_claims')
    op.drop_table('segments')
    op.drop_table('catalogues')
