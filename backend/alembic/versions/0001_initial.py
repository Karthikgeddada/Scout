"""Initial SCOUT schema: 22 tables, pgvector, FTS, RLS.

Revision ID: 0001
Revises: -
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "vector"')

    # ---------- core ----------
    op.execute(
        """
        CREATE TABLE profiles (
            id uuid PRIMARY KEY,
            supabase_user_id varchar(64) NOT NULL UNIQUE,
            display_name varchar(200),
            email varchar(320),
            avatar_url text,
            preferences jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_profiles_email ON profiles (email);

        CREATE TABLE workspaces (
            id uuid PRIMARY KEY,
            name varchar(200) NOT NULL,
            slug varchar(220) NOT NULL UNIQUE,
            description text,
            created_by uuid NOT NULL REFERENCES profiles (id),
            created_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE workspace_members (
            id uuid PRIMARY KEY,
            workspace_id uuid NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
            profile_id uuid NOT NULL REFERENCES profiles (id) ON DELETE CASCADE,
            role varchar(20) NOT NULL DEFAULT 'member',
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, profile_id)
        );
        """
    )

    # ---------- documents ----------
    op.execute(
        """
        CREATE TABLE documents (
            id uuid PRIMARY KEY,
            workspace_id uuid NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
            title varchar(300) NOT NULL,
            source_filename varchar(300) NOT NULL,
            storage_path text NOT NULL,
            fmt varchar(20) NOT NULL,
            mime varchar(120) NOT NULL,
            size_bytes integer NOT NULL,
            status varchar(30) NOT NULL DEFAULT 'pending',
            version integer NOT NULL DEFAULT 1,
            collection varchar(200),
            uploaded_by uuid NOT NULL REFERENCES profiles (id),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_documents_ws_status ON documents (workspace_id, status);
        CREATE INDEX ix_documents_ws_created ON documents (workspace_id, created_at DESC);
        CREATE INDEX ix_documents_collection ON documents (collection);

        CREATE TABLE document_versions (
            id uuid PRIMARY KEY,
            document_id uuid NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
            version integer NOT NULL,
            canonical jsonb NOT NULL,
            quality jsonb NOT NULL DEFAULT '{}'::jsonb,
            parser_versions jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (document_id, version)
        );

        CREATE TABLE document_sections (
            id uuid PRIMARY KEY,
            document_id uuid NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
            section_id varchar(40) NOT NULL,
            title varchar(500) NOT NULL,
            level integer NOT NULL,
            parent_section_id varchar(40),
            path text
        );
        CREATE INDEX ix_sections_doc ON document_sections (document_id);

        CREATE TABLE document_elements (
            id uuid PRIMARY KEY,
            document_id uuid NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
            section_id varchar(40),
            element_id varchar(40) NOT NULL,
            kind varchar(20) NOT NULL,
            text text NOT NULL,
            level integer,
            page integer,
            sheet varchar(200),
            locator varchar(100),
            bbox jsonb,
            metadata jsonb NOT NULL DEFAULT '{}'::jsonb
        );
        CREATE INDEX ix_elements_doc ON document_elements (document_id);
        CREATE INDEX ix_elements_kind ON document_elements (document_id, kind);

        CREATE TABLE document_tables (
            id uuid PRIMARY KEY,
            document_id uuid NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
            table_id varchar(40) NOT NULL,
            title varchar(300),
            sheet varchar(200),
            pages jsonb NOT NULL DEFAULT '[]'::jsonb,
            headers jsonb NOT NULL DEFAULT '[]'::jsonb,
            n_rows integer NOT NULL DEFAULT 0,
            n_cols integer NOT NULL DEFAULT 0,
            footnotes jsonb NOT NULL DEFAULT '[]'::jsonb,
            validation_flags jsonb NOT NULL DEFAULT '[]'::jsonb
        );
        CREATE INDEX ix_tables_doc ON document_tables (document_id);

        CREATE TABLE document_table_rows (
            id uuid PRIMARY KEY,
            document_id uuid NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
            table_db_id uuid NOT NULL REFERENCES document_tables (id) ON DELETE CASCADE,
            row_index integer NOT NULL,
            is_header boolean NOT NULL DEFAULT false,
            page integer
        );
        CREATE INDEX ix_rows_table ON document_table_rows (table_db_id);

        CREATE TABLE document_table_cells (
            id uuid PRIMARY KEY,
            row_db_id uuid NOT NULL REFERENCES document_table_rows (id) ON DELETE CASCADE,
            row_index integer NOT NULL,
            col_index integer NOT NULL,
            raw_value text NOT NULL,
            normalized_value text,
            data_type varchar(20) NOT NULL DEFAULT 'text',
            unit varchar(20),
            currency varchar(8),
            is_negative boolean NOT NULL DEFAULT false,
            decimal_value varchar(100),
            numeric_score real
        );
        CREATE INDEX ix_cells_row ON document_table_cells (row_db_id);
        """
    )

    # ---------- retrieval ----------
    op.execute(
        """
        CREATE TABLE document_chunks (
            id uuid PRIMARY KEY,
            chunk_id varchar(40) NOT NULL,
            document_id uuid NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
            workspace_id uuid NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
            version integer NOT NULL DEFAULT 1,
            kind varchar(20) NOT NULL,
            text text NOT NULL,
            token_count integer NOT NULL,
            parent_section_id varchar(40),
            section_title text,
            pages jsonb NOT NULL DEFAULT '[]'::jsonb,
            sheet varchar(200),
            locator varchar(100),
            table_id varchar(40),
            content_hash varchar(64) NOT NULL,
            metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            tsv tsvector
        );
        CREATE INDEX ix_chunks_doc ON document_chunks (document_id);
        CREATE INDEX ix_chunks_ws_kind ON document_chunks (workspace_id, kind);
        CREATE INDEX ix_chunks_content_hash ON document_chunks (content_hash);
        -- PostgreSQL full-text lexical index (tsvector ranking, not BM25).
        CREATE INDEX ix_chunks_tsv ON document_chunks USING gin (tsv);
        CREATE FUNCTION chunks_tsv_update() RETURNS trigger AS $$
        BEGIN
            NEW.tsv := to_tsvector('english', coalesce(NEW.text, ''));
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_chunks_tsv BEFORE INSERT OR UPDATE OF text
            ON document_chunks FOR EACH ROW EXECUTE FUNCTION chunks_tsv_update();

        CREATE TABLE document_embeddings (
            id uuid PRIMARY KEY,
            chunk_db_id uuid NOT NULL REFERENCES document_chunks (id) ON DELETE CASCADE,
            document_id uuid NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
            workspace_id uuid NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
            model_id varchar(120) NOT NULL,
            model_version varchar(80),
            dim integer NOT NULL,
            embedding vector(1024) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_emb_chunk ON document_embeddings (chunk_db_id);
        -- HNSW approximate-nearest-neighbour index for dense retrieval.
        CREATE INDEX ix_emb_hnsw ON document_embeddings
            USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
        """
    )

    # ---------- chat ----------
    op.execute(
        """
        CREATE TABLE conversations (
            id uuid PRIMARY KEY,
            workspace_id uuid NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
            profile_id uuid NOT NULL REFERENCES profiles (id) ON DELETE CASCADE,
            title varchar(300),
            mode varchar(20) NOT NULL DEFAULT 'chat',
            document_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_conv_ws_time ON conversations (workspace_id, created_at DESC);

        CREATE TABLE messages (
            id uuid PRIMARY KEY,
            conversation_id uuid NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
            role varchar(10) NOT NULL,
            content text NOT NULL,
            task_type varchar(30),
            retrieval_run_id uuid,
            metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_msg_conv_order ON messages (conversation_id, created_at);

        CREATE TABLE citations (
            id uuid PRIMARY KEY,
            message_id uuid NOT NULL REFERENCES messages (id) ON DELETE CASCADE,
            conversation_id uuid NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
            workspace_id uuid NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
            evidence_id integer NOT NULL,
            chunk_db_id uuid REFERENCES document_chunks (id) ON DELETE SET NULL,
            document_id uuid REFERENCES documents (id) ON DELETE SET NULL,
            document_title varchar(300),
            page integer,
            sheet varchar(200),
            locator varchar(100),
            quote text,
            valid boolean NOT NULL DEFAULT true,
            rejection_reason varchar(200),
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_cit_message ON citations (message_id);

        CREATE TABLE generated_artifacts (
            id uuid PRIMARY KEY,
            workspace_id uuid NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
            profile_id uuid NOT NULL REFERENCES profiles (id) ON DELETE CASCADE,
            kind varchar(30) NOT NULL,
            title varchar(300) NOT NULL,
            document_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
            storage_path text,
            format varchar(10),
            status varchar(20) NOT NULL DEFAULT 'pending',
            citation_count integer NOT NULL DEFAULT 0,
            content jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_artifacts_ws_time ON generated_artifacts (workspace_id, created_at DESC);
        """
    )

    # ---------- jobs, retrieval runs, usage, audit ----------
    op.execute(
        """
        CREATE TABLE processing_jobs (
            id uuid PRIMARY KEY,
            workspace_id uuid NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
            document_id uuid NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
            job_type varchar(40) NOT NULL DEFAULT 'ingest',
            status varchar(30) NOT NULL DEFAULT 'queued',
            attempts integer NOT NULL DEFAULT 0,
            max_attempts integer NOT NULL DEFAULT 3,
            progress integer NOT NULL DEFAULT 0,
            stage varchar(60),
            error text,
            idempotency_key varchar(120) UNIQUE,
            result jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            started_at timestamptz,
            finished_at timestamptz
        );
        CREATE INDEX ix_jobs_ws_status ON processing_jobs (workspace_id, status);
        CREATE INDEX ix_jobs_doc ON processing_jobs (document_id);

        CREATE TABLE outbox_events (
            id uuid PRIMARY KEY,
            topic varchar(60) NOT NULL,
            payload jsonb NOT NULL,
            dispatched boolean NOT NULL DEFAULT false,
            dispatched_at timestamptz,
            attempts integer NOT NULL DEFAULT 0,
            last_error text,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_outbox_pending ON outbox_events (dispatched, created_at);

        CREATE TABLE retrieval_runs (
            id uuid PRIMARY KEY,
            workspace_id uuid NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
            conversation_id uuid REFERENCES conversations (id) ON DELETE SET NULL,
            message_id uuid REFERENCES messages (id) ON DELETE SET NULL,
            query text NOT NULL,
            task_type varchar(30) NOT NULL,
            lexical_count integer NOT NULL DEFAULT 0,
            dense_count integer NOT NULL DEFAULT 0,
            reranked boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_runs_ws_time ON retrieval_runs (workspace_id, created_at DESC);

        CREATE TABLE retrieval_candidates (
            id uuid PRIMARY KEY,
            run_id uuid NOT NULL REFERENCES retrieval_runs (id) ON DELETE CASCADE,
            chunk_db_id uuid REFERENCES document_chunks (id) ON DELETE SET NULL,
            rank integer NOT NULL,
            source varchar(20) NOT NULL,
            score real,
            selected boolean NOT NULL DEFAULT false
        );
        CREATE INDEX ix_cand_run_rank ON retrieval_candidates (run_id, rank);

        CREATE TABLE usage_records (
            id uuid PRIMARY KEY,
            workspace_id uuid NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
            profile_id uuid REFERENCES profiles (id) ON DELETE SET NULL,
            kind varchar(40) NOT NULL,
            units integer NOT NULL DEFAULT 1,
            input_tokens integer NOT NULL DEFAULT 0,
            output_tokens integer NOT NULL DEFAULT 0,
            metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_usage_ws_time ON usage_records (workspace_id, created_at DESC);

        CREATE TABLE audit_logs (
            id uuid PRIMARY KEY,
            workspace_id uuid REFERENCES workspaces (id) ON DELETE SET NULL,
            actor_profile_id uuid REFERENCES profiles (id) ON DELETE SET NULL,
            action varchar(80) NOT NULL,
            target_type varchar(40),
            target_id varchar(64),
            details jsonb NOT NULL DEFAULT '{}'::jsonb,
            request_id varchar(64),
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_audit_ws_time ON audit_logs (workspace_id, created_at DESC);
        """
    )

    # ---------- row-level security ----------
    # The API connects as `scout_app` (NOT the service role) and sets the request
    # context `app.workspace_id` per session; RLS then scopes every table.
    op.execute(
        """
        CREATE FUNCTION scout_current_workspace() RETURNS uuid AS $$
            SELECT nullif(current_setting('app.workspace_id', true), '')::uuid
        $$ LANGUAGE sql STABLE;

        CREATE FUNCTION scout_is_member(ws uuid, user_id uuid) RETURNS boolean AS $$
            SELECT EXISTS (
                SELECT 1 FROM workspace_members
                WHERE workspace_id = ws AND profile_id = user_id
            )
        $$ LANGUAGE sql STABLE SECURITY DEFINER;
        """
    )
    for table in [
        "workspaces", "workspace_members", "documents", "document_versions",
        "document_sections", "document_elements", "document_tables",
        "document_table_rows", "document_table_cells", "document_chunks",
        "document_embeddings", "conversations", "messages", "citations",
        "generated_artifacts", "processing_jobs", "retrieval_runs",
        "retrieval_candidates", "usage_records",
    ]:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_ws_isolation ON {table} USING "
            f"(workspace_id = scout_current_workspace())"
        )

    # Workspaces themselves: visible only to members.
    op.execute("DROP POLICY IF EXISTS workspaces_ws_isolation ON workspaces")
    op.execute(
        """CREATE POLICY workspaces_member_only ON workspaces USING (
            EXISTS (
                SELECT 1 FROM workspace_members m
                WHERE m.workspace_id = workspaces.id
                  AND m.profile_id = nullif(current_setting('app.profile_id', true), '')::uuid
            )
        )"""
    )
    op.execute("ALTER TABLE profiles ENABLE ROW LEVEL SECURITY")
    op.execute(
        """CREATE POLICY profiles_self_only ON profiles USING (
            supabase_user_id = current_setting('app.supabase_user_id', true)
        )"""
    )

    op.execute("ALTER TABLE outbox_events ENABLE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY outbox_noapp ON outbox_events USING (false)")
    op.execute("ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY audit_noapp ON audit_logs USING (false)")


def downgrade() -> None:
    for table in [
        "profiles", "workspaces", "workspace_members", "documents", "document_versions",
        "document_sections", "document_elements", "document_tables",
        "document_table_rows", "document_table_cells", "document_chunks",
        "document_embeddings", "conversations", "messages", "citations",
        "generated_artifacts", "processing_jobs", "outbox_events",
        "retrieval_runs", "retrieval_candidates", "usage_records", "audit_logs",
    ]:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS chunks_tsv_update()")
    op.execute("DROP FUNCTION IF EXISTS scout_current_workspace()")
    op.execute("DROP FUNCTION IF EXISTS scout_is_member(uuid, uuid)")
