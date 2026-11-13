-- 0032: schema integrity invariants and case-insensitive natural keys.
-- Moves invariants and natural-key rules the application already assumes into the database.

-- A. Ledger amounts (pgledger_transfers) ----------------------------------------------------------

-- Enforce whole integer paise on transfer amounts (pgledger stores NUMERIC, Arno requires integer paise).
ALTER TABLE pgledger_transfers
    ADD CONSTRAINT pgledger_transfers_amount_integer_check CHECK (amount = trunc(amount));

-- B. State invariants (movements, accounts, schedule_occurrences) ---------------------------------

-- Enforce reversal polarity: a movement has kind='reversal' if and only if reverses_id is set.
ALTER TABLE movements DROP CONSTRAINT movements_check;
ALTER TABLE movements
    ADD CONSTRAINT movements_reversal_pair_check CHECK (
        (kind = 'reversal') = (reverses_id IS NOT NULL)
    );

-- Enforce card limit/day completeness and statement balance/date pairing on accounts.
ALTER TABLE accounts DROP CONSTRAINT accounts_check3;
ALTER TABLE accounts
    ADD CONSTRAINT accounts_card_fields_check CHECK (
        kind <> 'card'
        OR (credit_limit_paise IS NOT NULL AND statement_day IS NOT NULL AND due_day IS NOT NULL)
    ),
    ADD CONSTRAINT accounts_statement_balance_pair_check CHECK (
        (statement_balance_paise IS NULL) = (statement_balance_as_of IS NULL)
    );

-- Enforce the same closed kind and subtype sets on schedule_occurrences snapshots as on schedules.
ALTER TABLE schedule_occurrences
    ADD CONSTRAINT schedule_occurrences_kind_check CHECK (
        kind IN (
            'income_fixed', 'income_variable', 'income_oneoff',
            'obligation', 'instalment', 'goal_contribution'
        )
    ),
    ADD CONSTRAINT schedule_occurrences_subtype_check CHECK (
        subtype IS NULL
        OR subtype IN ('rent', 'emi', 'subscription', 'insurance', 'family_transfer', 'other')
    ),
    ADD CONSTRAINT schedule_occurrences_obligation_subtype_check CHECK (
        kind = 'obligation' OR subtype IS NULL
    );

-- C. Case-insensitive natural keys (categories, payees, statement_layouts) ------------------------

-- Enforce case-insensitive uniqueness on category names, replacing the exact-text constraint.
ALTER TABLE categories DROP CONSTRAINT categories_name_key;
CREATE UNIQUE INDEX categories_lower_name_idx ON categories (lower(name));

-- Replace case-sensitive UPI ID uniqueness with a case-insensitive index matching lookup queries.
DROP INDEX payees_upi_id_idx;
CREATE UNIQUE INDEX payees_lower_upi_id_idx ON payees (lower(upi_id)) WHERE upi_id IS NOT NULL;

-- Replace case-sensitive statement layout uniqueness with a case-insensitive index matching layout lookups.
ALTER TABLE statement_layouts DROP CONSTRAINT statement_layouts_institution_kind_key;
CREATE UNIQUE INDEX statement_layouts_lower_inst_kind_idx
    ON statement_layouts (lower(institution), lower(kind));
