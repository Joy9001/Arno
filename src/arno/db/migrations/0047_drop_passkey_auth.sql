-- 0047: sign-in moves to Tailscale identity or a passphrase, so the passkey tables go.
-- The profile name lived on `users`; it moves to the settings row first.

ALTER TABLE settings ADD COLUMN display_name TEXT NOT NULL DEFAULT 'You';

UPDATE settings SET display_name = u.display_name
FROM (SELECT display_name FROM users LIMIT 1) AS u;

DROP TABLE webauthn_challenges, setup_tokens, webauthn_credentials, sessions, users;
