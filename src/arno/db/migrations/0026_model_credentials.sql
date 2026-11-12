-- 0026: the native ChatGPT sign-in's credentials, so a refresh survives the process.
--
-- The provider owns refresh, rotation, and single-flight; this table is only its application-owned
-- store. `agent_ro` gets no grant: these are provider credentials, never model-readable data.
-- Deleting the row is the revocation action. The backup role reads it through its default grant.

CREATE TABLE model_credentials (
    provider TEXT PRIMARY KEY CHECK (provider IN ('openai-chatgpt')),
    client_id TEXT NOT NULL,
    access_token TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    id_token TEXT,
    scope TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE model_credentials IS
    'Native provider sign-ins. Credentials only; never granted to agent_ro.';
