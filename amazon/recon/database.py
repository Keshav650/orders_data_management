import sqlite3
from pathlib import Path


SCHEMA = """
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    digest TEXT UNIQUE NOT NULL,
    kind TEXT NOT NULL,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    payload BLOB NOT NULL,
    summary TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_rows (
    id INTEGER PRIMARY KEY,
    import_id INTEGER REFERENCES imports(id),
    sheet TEXT,
    row_number INTEGER,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    order_item_id TEXT PRIMARY KEY,
    amazon_order_id TEXT NOT NULL,
    purchase_date TEXT NOT NULL,
    last_updated_date TEXT,
    order_status TEXT NOT NULL,
    fulfillment_channel TEXT,
    sales_channel TEXT,
    product_name TEXT,
    sku TEXT NOT NULL,
    asin TEXT,
    quantity INTEGER NOT NULL,
    currency TEXT DEFAULT 'INR',
    item_price INTEGER NOT NULL DEFAULT 0,
    item_tax INTEGER NOT NULL DEFAULT 0,
    shipping_price INTEGER NOT NULL DEFAULT 0,
    shipping_tax INTEGER NOT NULL DEFAULT 0,
    gift_wrap_price INTEGER NOT NULL DEFAULT 0,
    gift_wrap_tax INTEGER NOT NULL DEFAULT 0,
    item_discount INTEGER NOT NULL DEFAULT 0,
    ship_city TEXT,
    ship_state TEXT,
    ship_postal_code TEXT,
    ship_country TEXT,
    fulfilled_by TEXT,
    is_prime TEXT,
    import_id INTEGER REFERENCES imports(id),
    raw TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orders_amazon_id ON orders(amazon_order_id);
CREATE INDEX IF NOT EXISTS idx_orders_sku ON orders(sku);
CREATE INDEX IF NOT EXISTS idx_orders_purchase_date ON orders(purchase_date);

CREATE TABLE IF NOT EXISTS transactions (
    fingerprint TEXT PRIMARY KEY,
    settlement_id TEXT NOT NULL,
    post_date TEXT NOT NULL,
    type TEXT NOT NULL,
    order_id TEXT,
    sku TEXT,
    description TEXT,
    quantity INTEGER,
    marketplace TEXT,
    account_type TEXT,
    fulfillment TEXT,
    city TEXT,
    state TEXT,
    postal TEXT,
    product_sales INTEGER NOT NULL DEFAULT 0,
    shipping_credits INTEGER NOT NULL DEFAULT 0,
    gift_wrap_credits INTEGER NOT NULL DEFAULT 0,
    promotional_rebates INTEGER NOT NULL DEFAULT 0,
    tax_liable INTEGER NOT NULL DEFAULT 0,
    tcs_cgst INTEGER NOT NULL DEFAULT 0,
    tcs_sgst INTEGER NOT NULL DEFAULT 0,
    tcs_igst INTEGER NOT NULL DEFAULT 0,
    tds INTEGER NOT NULL DEFAULT 0,
    selling_fees INTEGER NOT NULL DEFAULT 0,
    fba_fees INTEGER NOT NULL DEFAULT 0,
    other_fees INTEGER NOT NULL DEFAULT 0,
    other_amount INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'Released',
    release_date TEXT,
    import_id INTEGER REFERENCES imports(id),
    raw TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trans_order_id ON transactions(order_id);
CREATE INDEX IF NOT EXISTS idx_trans_sku ON transactions(sku);
CREATE INDEX IF NOT EXISTS idx_trans_post_date ON transactions(post_date);
CREATE INDEX IF NOT EXISTS idx_trans_type ON transactions(type);

CREATE TABLE IF NOT EXISTS returns (
    id INTEGER PRIMARY KEY,
    order_id TEXT NOT NULL,
    order_item_id TEXT,
    return_date TEXT NOT NULL,
    return_status TEXT,
    return_type TEXT NOT NULL,
    return_reason TEXT,
    tracking_id TEXT,
    sku TEXT,
    return_qty INTEGER NOT NULL DEFAULT 1,
    resolution TEXT,
    refund_amount INTEGER NOT NULL DEFAULT 0,
    delivery_date TEXT,
    import_id INTEGER REFERENCES imports(id),
    raw TEXT NOT NULL,
    UNIQUE(order_id, order_item_id, return_date, return_type)
);
CREATE INDEX IF NOT EXISTS idx_returns_order_id ON returns(order_id);
CREATE INDEX IF NOT EXISTS idx_returns_sku ON returns(sku);

CREATE TABLE IF NOT EXISTS ads_product (
    fingerprint TEXT PRIMARY KEY,
    campaign_id TEXT,
    campaign_name TEXT,
    ad_group_id TEXT,
    ad_group_name TEXT,
    sku TEXT NOT NULL,
    date_range TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    impressions INTEGER NOT NULL DEFAULT 0,
    clicks INTEGER NOT NULL DEFAULT 0,
    cost INTEGER NOT NULL DEFAULT 0,
    sales INTEGER NOT NULL DEFAULT 0,
    units INTEGER NOT NULL DEFAULT 0,
    roas REAL NOT NULL DEFAULT 0.0,
    import_id INTEGER REFERENCES imports(id),
    raw TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ads_sku ON ads_product(sku);
CREATE INDEX IF NOT EXISTS idx_ads_dates ON ads_product(start_date, end_date);

CREATE TABLE IF NOT EXISTS ads_campaign (
    fingerprint TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    campaign_name TEXT NOT NULL,
    date_range TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    impressions INTEGER NOT NULL DEFAULT 0,
    clicks INTEGER NOT NULL DEFAULT 0,
    cost INTEGER NOT NULL DEFAULT 0,
    sales INTEGER NOT NULL DEFAULT 0,
    roas REAL NOT NULL DEFAULT 0.0,
    import_id INTEGER REFERENCES imports(id),
    raw TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_camp_dates ON ads_campaign(start_date, end_date);

CREATE TABLE IF NOT EXISTS ads_search_term (
    fingerprint TEXT PRIMARY KEY,
    campaign_id TEXT,
    campaign_name TEXT,
    ad_group_id TEXT,
    ad_group_name TEXT,
    search_term TEXT NOT NULL,
    date_range TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    impressions INTEGER NOT NULL DEFAULT 0,
    clicks INTEGER NOT NULL DEFAULT 0,
    cost INTEGER NOT NULL DEFAULT 0,
    sales INTEGER NOT NULL DEFAULT 0,
    units INTEGER NOT NULL DEFAULT 0,
    roas REAL NOT NULL DEFAULT 0.0,
    import_id INTEGER REFERENCES imports(id),
    raw TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_st_term ON ads_search_term(search_term);

CREATE TABLE IF NOT EXISTS products (
    sku TEXT PRIMARY KEY,
    display_sku TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS product_preferences (
    sku TEXT PRIMARY KEY REFERENCES products(sku),
    disabled INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS product_versions (
    id INTEGER PRIMARY KEY,
    sku TEXT REFERENCES products(sku),
    effective_date TEXT NOT NULL,
    cost INTEGER,
    packaging INTEGER,
    saved_at TEXT DEFAULT CURRENT_TIMESTAMP,
    source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_product_versions ON product_versions(sku, effective_date);

CREATE TABLE IF NOT EXISTS recovery_events (
    id INTEGER PRIMARY KEY,
    order_id TEXT NOT NULL,
    order_item_id TEXT,
    effective_date TEXT NOT NULL,
    state TEXT NOT NULL,
    note TEXT NOT NULL,
    saved_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_recovery_order_id ON recovery_events(order_id);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT OR IGNORE INTO settings VALUES ('gst_rate', '18');
INSERT OR IGNORE INTO settings VALUES ('include_gst', 'true');
INSERT OR IGNORE INTO settings VALUES ('cutoff_days', '14');

CREATE TABLE IF NOT EXISTS import_runs (
    id INTEGER PRIMARY KEY,
    import_id INTEGER REFERENCES imports(id),
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    summary TEXT NOT NULL
);
"""


class Connection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, factory=Connection, timeout=30)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    
    # Auto-populate products table from orders, transactions, and ads if new
    for row in db.execute("""
        SELECT DISTINCT sku FROM orders WHERE sku IS NOT NULL AND sku != ''
        UNION
        SELECT DISTINCT sku FROM transactions WHERE sku IS NOT NULL AND sku != ''
        UNION
        SELECT DISTINCT sku FROM ads_product WHERE sku IS NOT NULL AND sku != ''
    """).fetchall():
        display = str(row[0] or '').strip()
        if display:
            key = ' '.join(display.split()).casefold()
            db.execute('INSERT OR IGNORE INTO products(sku, display_sku) VALUES (?,?)', (key, display))
    db.commit()
    return db
