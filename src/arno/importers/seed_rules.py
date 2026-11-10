"""The seed list: large Indian merchants mapped to the default categories.

It ships with the app and is inserted idempotently at boot. A pattern is matched with `contains`
unless it is a regex, so `Zomato87` and `SWIGGY PVT LTD E COM1 BENGALURU` both hit. Apps that
front anything are never matched by name; their rows go to the model with the amount and context.
"""

from __future__ import annotations

from arno.db.conn import Connection

PAYMENT_APPS: tuple[str, ...] = (
    "one97",
    "paytm",
    "phonepe",
    "google pay",
    "gpay",
    "cred",
    "amazon pay",
    "razorpay",
)

# (category name, match, pattern)
SEED_RULES: tuple[tuple[str, str, str], ...] = (
    ("Food & Dining", "contains", "swiggy"),
    ("Food & Dining", "contains", "zomato"),
    ("Food & Dining", "contains", "dominos"),
    ("Food & Dining", "contains", "pizza hut"),
    ("Food & Dining", "contains", "kfc"),
    ("Food & Dining", "contains", "burger king"),
    ("Food & Dining", "contains", "mcdonald"),
    ("Food & Dining", "contains", "subway"),
    ("Groceries", "contains", "bigbasket"),
    ("Groceries", "contains", "big basket"),
    ("Groceries", "contains", "dmart"),
    ("Groceries", "contains", "d mart"),
    ("Groceries", "contains", "reliance fresh"),
    ("Groceries", "contains", "more supermarket"),
    ("Groceries", "contains", "spencer"),
    ("Groceries", "contains", "natures basket"),
    ("Transport", "contains", "uber"),
    ("Transport", "contains", "ola cabs"),
    ("Transport", "contains", "rapido"),
    ("Transport", "contains", "namma yatri"),
    ("Transport", "contains", "blusmart"),
    ("Transport", "contains", "indrive"),
    ("Transport", "contains", "shuttl"),
    ("Transport", "contains", "cityflo"),
    ("Fuel", "contains", "indian oil"),
    ("Fuel", "contains", "iocl"),
    ("Fuel", "contains", "bharat petroleum"),
    ("Fuel", "contains", "bpcl"),
    ("Fuel", "contains", "hpcl"),
    ("Fuel", "contains", "hp petrol"),
    ("Fuel", "contains", "nayara"),
    ("Fuel", "contains", "jio bp"),
    ("Shopping", "contains", "amazon"),
    ("Shopping", "contains", "flipkart"),
    ("Shopping", "contains", "myntra"),
    ("Shopping", "contains", "ajio"),
    ("Shopping", "contains", "meesho"),
    ("Shopping", "contains", "snapdeal"),
    ("Shopping", "contains", "tata cliq"),
    ("Shopping", "contains", "croma"),
    ("Entertainment", "contains", "bookmyshow"),
    ("Entertainment", "contains", "pvr"),
    ("Entertainment", "contains", "inox"),
    ("Entertainment", "contains", "cinepolis"),
    ("Entertainment", "contains", "wonderla"),
    ("Entertainment", "contains", "imagica"),
    ("Entertainment", "contains", "essel world"),
    ("Entertainment", "contains", "steam games"),
    ("Subscriptions", "contains", "netflix"),
    ("Subscriptions", "contains", "spotify"),
    ("Subscriptions", "contains", "hotstar"),
    ("Subscriptions", "contains", "prime video"),
    ("Subscriptions", "contains", "sonyliv"),
    ("Subscriptions", "contains", "zee5"),
    ("Subscriptions", "contains", "jiocinema"),
    ("Subscriptions", "contains", "youtube premium"),
    ("Utilities", "contains", "bescom"),
    ("Utilities", "contains", "mseb"),
    ("Utilities", "contains", "mahadiscom"),
    ("Utilities", "contains", "tata power"),
    ("Utilities", "contains", "adani electricity"),
    ("Utilities", "contains", "torrent power"),
    ("Utilities", "contains", "cesc"),
    ("Utilities", "contains", "bsphcl"),
    ("Mobile & Internet", "contains", "airtel"),
    ("Mobile & Internet", "contains", "jiofiber"),
    ("Mobile & Internet", "contains", "act fibernet"),
    ("Mobile & Internet", "contains", "hathway"),
    ("Mobile & Internet", "contains", "excitel"),
    ("Mobile & Internet", "contains", "you broadband"),
    ("Mobile & Internet", "contains", "tata play fiber"),
    ("Mobile & Internet", "contains", "vodafone"),
    ("Rent", "contains", "no broker"),
    ("Rent", "contains", "nestaway"),
    ("Rent", "contains", "stanza living"),
    ("Rent", "contains", "colive"),
    ("Rent", "contains", "zolo"),
    ("Rent", "contains", "landlord"),
    ("Rent", "regex", r"\brent\b"),
    ("Home Maintenance", "contains", "urban company"),
    ("Home Maintenance", "contains", "urbanclap"),
    ("Home Maintenance", "contains", "asian paints"),
    ("Home Maintenance", "contains", "berger paints"),
    ("Home Maintenance", "contains", "nilkamal"),
    ("Home Maintenance", "contains", "godrej interio"),
    ("Home Maintenance", "contains", "homelane"),
    ("Home Maintenance", "contains", "livspace"),
    ("Health", "contains", "apollo pharmacy"),
    ("Health", "contains", "pharmeasy"),
    ("Health", "contains", "netmeds"),
    ("Health", "contains", "tata 1mg"),
    ("Health", "contains", "medplus"),
    ("Health", "contains", "wellness forever"),
    ("Health", "contains", "practo"),
    ("Health", "contains", "healthifyme"),
    ("Education", "contains", "byju"),
    ("Education", "contains", "unacademy"),
    ("Education", "contains", "vedantu"),
    ("Education", "contains", "aakash"),
    ("Education", "contains", "allen"),
    ("Education", "contains", "physics wallah"),
    ("Education", "contains", "toppr"),
    ("Education", "contains", "coursera"),
    ("Insurance", "contains", "life insurance corporation"),
    ("Insurance", "contains", "hdfc life"),
    ("Insurance", "contains", "icici prudential"),
    ("Insurance", "contains", "sbi life"),
    ("Insurance", "contains", "max life"),
    ("Insurance", "contains", "tata aia"),
    ("Insurance", "contains", "bajaj allianz"),
    ("Insurance", "contains", "star health"),
    ("Loan Interest", "contains", "loan interest"),
    ("Loan Interest", "contains", "home loan interest"),
    ("Loan Interest", "contains", "emi interest"),
    ("Loan Interest", "contains", "interest on loan"),
    ("Family Transfer", "contains", "family transfer"),
    ("Savings & Investments", "contains", "zerodha"),
    ("Savings & Investments", "contains", "groww"),
    ("Savings & Investments", "contains", "upstox"),
    ("Savings & Investments", "contains", "angel one"),
    ("Savings & Investments", "contains", "icici direct"),
    ("Savings & Investments", "contains", "hdfc securities"),
    ("Savings & Investments", "contains", "kotak securities"),
    ("Savings & Investments", "contains", "kuvera"),
    ("Fees & Charges", "contains", "annual fee"),
    ("Fees & Charges", "contains", "late fee"),
    ("Fees & Charges", "contains", "service charge"),
    ("Fees & Charges", "contains", "convenience fee"),
    ("Fees & Charges", "contains", "processing fee"),
    ("Fees & Charges", "contains", "minimum balance charge"),
    ("Fees & Charges", "contains", "forex markup"),
    ("Fees & Charges", "contains", "gst on fees"),
    ("Fees & Charges", "contains", "surcharge"),
    ("Fees & Charges", "contains", "interest charge"),
    ("Fees & Charges", "contains", "card fee"),
    ("Fees & Charges", "contains", "joining fee"),
    ("Fees & Charges", "contains", "renewal fee"),
    ("Fees & Charges", "contains", "overlimit fee"),
    ("Fees & Charges", "regex", r"\bcharges?\b"),
    ("Travel", "contains", "irctc"),
    ("Travel", "contains", "makemytrip"),
    ("Travel", "contains", "goibibo"),
    ("Travel", "contains", "cleartrip"),
    ("Travel", "contains", "yatra"),
    ("Travel", "contains", "ixigo"),
    ("Travel", "contains", "easemytrip"),
    ("Travel", "contains", "indigo"),
    ("Gifts & Donations", "contains", "ferns n petals"),
    ("Gifts & Donations", "contains", "interflora"),
    ("Gifts & Donations", "contains", "archies"),
    ("Gifts & Donations", "contains", "giveindia"),
    ("Gifts & Donations", "contains", "akshaya patra"),
    ("Gifts & Donations", "contains", "ketto"),
    ("Gifts & Donations", "contains", "milaap"),
    ("Gifts & Donations", "contains", "impactguru"),
    ("Personal Care", "contains", "nykaa"),
    ("Personal Care", "contains", "purplle"),
    ("Personal Care", "contains", "mamaearth"),
    ("Personal Care", "contains", "minimalist"),
    ("Personal Care", "contains", "the man company"),
    ("Personal Care", "contains", "bombay shaving"),
    ("Personal Care", "contains", "lenskart"),
    ("Personal Care", "regex", r"\bsalon\b"),
)


async def ensure_seed_rules(conn: Connection) -> int:
    """Insert the seed list, leaving a user or llm rule on the same match and pattern alone."""
    categories = {
        str(row["name"]): str(row["id"])
        for row in await conn.fetch("SELECT id, name FROM categories WHERE active")
    }
    inserted = 0
    for category_name, match, pattern in SEED_RULES:
        category_id = categories.get(category_name)
        if category_id is None:
            continue
        row = await conn.fetchrow(
            """
            INSERT INTO category_rules (match, pattern, category_id, source, priority)
            VALUES ($1, $2, $3, 'seed', 0)
            ON CONFLICT (match, pattern) DO NOTHING
            RETURNING id
            """,
            match,
            pattern,
            category_id,
        )
        if row is not None:
            inserted += 1
    return inserted


__all__ = ["PAYMENT_APPS", "SEED_RULES", "ensure_seed_rules"]
