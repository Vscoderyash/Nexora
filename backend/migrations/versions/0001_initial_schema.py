"""Create the initial NEXORA relational schema."""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sources",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False, unique=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("license_notes", sa.Text(), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_sources_url", "sources", ["url"])

    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("permissions", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("request_count", sa.Integer(), nullable=False),
    )
    op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"])

    op.create_table(
        "entities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("normalized_name", sa.String(240), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("normalized_name", "kind", name="uq_entities_normalized_kind"),
    )
    op.create_index("ix_entities_name", "entities", ["name"])
    op.create_index("ix_entities_normalized_name", "entities", ["normalized_name"])

    op.create_table(
        "documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_id", sa.String(36), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False, unique=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("publisher", sa.String(300), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("categories", sa.JSON(), nullable=False),
        sa.Column("quality", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("duplicate_of", sa.String(36), sa.ForeignKey("documents.id")),
    )
    op.create_index("ix_documents_source_id", "documents", ["source_id"])
    op.create_index("ix_documents_url", "documents", ["url"])
    op.create_index("ix_documents_fingerprint", "documents", ["fingerprint"])
    op.create_index("ix_documents_duplicate_of", "documents", ["duplicate_of"])

    op.create_table(
        "request_logs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("key_id", sa.String(36), sa.ForeignKey("api_keys.id")),
        sa.Column("method", sa.String(12), nullable=False),
        sa.Column("path", sa.String(300), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_request_logs_key_id", "request_logs", ["key_id"])
    op.create_index("ix_request_logs_created_at", "request_logs", ["created_at"])

    op.create_table(
        "collection_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_id", sa.String(36), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("discovered", sa.Integer(), nullable=False),
        sa.Column("collected", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_collection_jobs_source_id", "collection_jobs", ["source_id"])

    op.create_table(
        "document_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_document_versions_document_id", "document_versions", ["document_id"])

    op.create_table(
        "document_entities",
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("entity_id", sa.String(36), sa.ForeignKey("entities.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence", sa.String(400), nullable=False),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_document_entities_entity_id", "document_entities", ["entity_id"])


def downgrade():
    op.drop_index("ix_document_entities_entity_id", table_name="document_entities")
    op.drop_table("document_entities")
    op.drop_index("ix_document_versions_document_id", table_name="document_versions")
    op.drop_table("document_versions")
    op.drop_index("ix_collection_jobs_source_id", table_name="collection_jobs")
    op.drop_table("collection_jobs")
    op.drop_index("ix_request_logs_created_at", table_name="request_logs")
    op.drop_index("ix_request_logs_key_id", table_name="request_logs")
    op.drop_table("request_logs")
    op.drop_index("ix_documents_duplicate_of", table_name="documents")
    op.drop_index("ix_documents_fingerprint", table_name="documents")
    op.drop_index("ix_documents_url", table_name="documents")
    op.drop_index("ix_documents_source_id", table_name="documents")
    op.drop_table("documents")
    op.drop_index("ix_entities_normalized_name", table_name="entities")
    op.drop_index("ix_entities_name", table_name="entities")
    op.drop_table("entities")
    op.drop_index("ix_api_keys_key_hash", table_name="api_keys")
    op.drop_table("api_keys")
    op.drop_index("ix_sources_url", table_name="sources")
    op.drop_table("sources")
