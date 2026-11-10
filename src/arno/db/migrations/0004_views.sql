-- 0004: the semantic layer. The tools and run_query share these views, so each number has one
-- definition. Money is returned as bigint paise.

-- Transfers into and out of the Spending account, net, with category and payee.
-- Positive means money went into Spending (spend); negative is a refund.
CREATE VIEW v_spending AS
SELECT
    m.id AS movement_id,
    m.date,
    m.description,
    t.id AS transfer_id,
    tl.category_id,
    c.name AS category_name,
    tl.payee_id,
    p.display_name AS payee_name,
    (CASE WHEN t.to_account_id = sp.ledger_account_id THEN t.amount ELSE -t.amount END)::bigint
        AS amount_paise
FROM pgledger_transfers t
JOIN accounts sp ON sp.kind = 'spending'
JOIN movements m ON m.id = t.metadata ->> 'movement_id'
LEFT JOIN transfer_labels tl ON tl.transfer_id = t.id
LEFT JOIN categories c ON c.id = tl.category_id
LEFT JOIN payees p ON p.id = tl.payee_id
WHERE t.from_account_id = sp.ledger_account_id OR t.to_account_id = sp.ledger_account_id;

-- Every transfer with both sides named, so money in and out of liquid accounts is composable.
CREATE VIEW v_cashflow AS
SELECT
    t.id AS transfer_id,
    m.id AS movement_id,
    m.date,
    m.kind AS movement_kind,
    t.from_account_id,
    fa.name AS from_name,
    fa.kind AS from_kind,
    fa.liquid AS from_liquid,
    t.to_account_id,
    ta.name AS to_name,
    ta.kind AS to_kind,
    ta.liquid AS to_liquid,
    t.amount::bigint AS amount_paise
FROM pgledger_transfers t
JOIN accounts fa ON fa.ledger_account_id = t.from_account_id
JOIN accounts ta ON ta.ledger_account_id = t.to_account_id
LEFT JOIN movements m ON m.id = t.metadata ->> 'movement_id';

-- Balances by date, summed from entries by each movement's date. pgledger's stored running
-- balances follow write order, and a backfill arrives out of order, so they aren't used here.
CREATE VIEW v_balances AS
WITH dated AS (
    SELECT
        e.account_id,
        coalesce(
            m.date,
            (t.event_at AT TIME ZONE 'Asia/Kolkata')::date
        ) AS as_of,
        e.amount
    FROM pgledger_entries e
    JOIN pgledger_transfers t ON t.id = e.transfer_id
    LEFT JOIN movements m ON m.id = t.metadata ->> 'movement_id'
),
daily AS (
    SELECT account_id, as_of, sum(amount) AS delta
    FROM dated
    GROUP BY account_id, as_of
)
SELECT
    a.id AS account_id,
    a.name,
    a.kind,
    a.liquid,
    d.as_of,
    sum(d.delta) OVER (
        PARTITION BY d.account_id
        ORDER BY d.as_of
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    )::bigint AS balance_paise
FROM daily d
JOIN accounts a ON a.ledger_account_id = d.account_id
ORDER BY a.id, d.as_of;

-- Who owes what: person account balances with expected dates. Positive means they owe you.
CREATE VIEW v_owed AS
SELECT
    p.id AS payee_id,
    p.display_name,
    a.id AS account_id,
    coalesce(sum(e.amount), 0)::bigint AS balance_paise,
    CASE
        WHEN coalesce(sum(e.amount), 0) > 0 THEN 'owes_you'
        WHEN coalesce(sum(e.amount), 0) < 0 THEN 'you_owe'
        ELSE 'settled'
    END AS direction,
    min(l.expected_date) FILTER (
        WHERE l.expected_date >= current_date
    ) AS next_expected_date
FROM accounts a
JOIN payees p ON p.person_account_id = a.id
LEFT JOIN pgledger_entries e ON e.account_id = a.ledger_account_id
LEFT JOIN transfer_labels l ON l.transfer_id = e.transfer_id
WHERE a.kind = 'person'
GROUP BY p.id, p.display_name, a.id;
