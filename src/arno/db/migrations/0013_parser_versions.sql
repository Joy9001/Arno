-- 0013: parser versions. A layout is one bank and statement type, holding its known header
-- variants and a pointer to its active parser version. Versions are append-only, and a rollback
-- moves the pointer. Statement files are no longer purged at commit or reject; `unlocked_at`
-- marks a file whose stored bytes no longer need the password (the password itself is never
-- stored, and `sha256` stays the original upload's hash for dedupe).

UPDATE imports SET layout_id = NULL WHERE layout_id IS NOT NULL;
DROP TABLE IF EXISTS statement_layouts CASCADE;

CREATE TABLE statement_layouts (
    id TEXT PRIMARY KEY DEFAULT pgledger_generate_id('lay'),
    institution TEXT NOT NULL,
    kind TEXT NOT NULL,
    -- The known normalized header fingerprints for this bank and statement type.
    variants TEXT[] NOT NULL DEFAULT '{}',
    active_version_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (institution, kind)
);

CREATE INDEX statement_layouts_variants_idx ON statement_layouts USING GIN (variants);

CREATE TABLE parser_versions (
    id TEXT PRIMARY KEY DEFAULT pgledger_generate_id('pv'),
    layout_id TEXT NOT NULL REFERENCES statement_layouts (id) ON DELETE CASCADE,
    version INTEGER NOT NULL CHECK (version > 0),
    parser TEXT NOT NULL,
    helper_version TEXT,
    source_import_id TEXT REFERENCES imports (id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (layout_id, version)
);

CREATE INDEX parser_versions_layout_id_idx ON parser_versions (layout_id, version DESC);

ALTER TABLE statement_layouts
    ADD CONSTRAINT statement_layouts_active_version_fkey
    FOREIGN KEY (active_version_id) REFERENCES parser_versions (id) ON DELETE SET NULL;

ALTER TABLE imports
    DROP CONSTRAINT IF EXISTS imports_layout_id_fkey,
    ADD CONSTRAINT imports_layout_id_fkey
        FOREIGN KEY (layout_id) REFERENCES statement_layouts (id) ON DELETE SET NULL;

ALTER TABLE attachments ADD COLUMN unlocked_at TIMESTAMPTZ;
