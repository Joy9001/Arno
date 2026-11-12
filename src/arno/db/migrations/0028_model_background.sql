-- Only the owner sign-in command grants background plan use. Refresh preserves that choice.
ALTER TABLE model_credentials ADD COLUMN allow_background BOOLEAN NOT NULL DEFAULT false;
