-- 0007: enrollment links for first-run setup and adding a device.
--
-- A setup token is now one of two kinds. `first_run` creates the one user and its first passkey;
-- `add_device` adds a passkey for an existing user and is bound to that user. Keeping both in
-- `setup_tokens` means one hash/comparison path and one cleanup path. The server, not the client,
-- decides which kind a link is by looking up the token.
--
-- The challenge remembers the token that started it, so verification re-checks the same kind and
-- cannot be replayed after setup state changes.

ALTER TABLE setup_tokens
    ADD COLUMN kind TEXT NOT NULL DEFAULT 'first_run'
        CHECK (kind IN ('first_run', 'add_device')),
    ADD COLUMN user_id TEXT REFERENCES users (id) ON DELETE CASCADE;

ALTER TABLE setup_tokens
    ADD CONSTRAINT setup_tokens_kind_user
    CHECK (
        (kind = 'first_run' AND user_id IS NULL)
        OR (kind = 'add_device' AND user_id IS NOT NULL)
    );

-- One live token per kind. Issuers remove the previous unconsumed token of the same kind before
-- inserting, so this also catches a bug that would leave two links alive.
CREATE UNIQUE INDEX setup_tokens_one_active_per_kind
    ON setup_tokens (kind) WHERE consumed_at IS NULL;

CREATE INDEX setup_tokens_user_id_idx
    ON setup_tokens (user_id) WHERE user_id IS NOT NULL;

ALTER TABLE webauthn_challenges
    ADD COLUMN setup_token_id TEXT REFERENCES setup_tokens (id) ON DELETE SET NULL;
