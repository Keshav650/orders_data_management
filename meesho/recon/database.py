import sqlite3
from pathlib import Path


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS imports (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, digest TEXT UNIQUE NOT NULL,
 kind TEXT NOT NULL, imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 payload BLOB NOT NULL, summary TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS raw_rows (
 id INTEGER PRIMARY KEY, import_id INTEGER REFERENCES imports(id),
 sheet TEXT, row_number INTEGER, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS order_events (
 fingerprint TEXT PRIMARY KEY, sub_order_id TEXT NOT NULL, order_date TEXT NOT NULL,
 sku TEXT NOT NULL, quantity INTEGER NOT NULL, listed_paise INTEGER NOT NULL,
 status TEXT NOT NULL, packet TEXT, import_id INTEGER REFERENCES imports(id), raw TEXT
);
CREATE INDEX IF NOT EXISTS orders_id ON order_events(sub_order_id);
CREATE TABLE IF NOT EXISTS payments (
 fingerprint TEXT PRIMARY KEY, sub_order_id TEXT NOT NULL, order_date TEXT,
 payment_date TEXT NOT NULL, transaction_id TEXT, status TEXT,
 settlement INTEGER NOT NULL, shipping INTEGER NOT NULL, reverse_shipping INTEGER NOT NULL,
 tds INTEGER NOT NULL, tcs INTEGER NOT NULL, sale INTEGER NOT NULL, sale_return INTEGER NOT NULL,
 sku TEXT, import_id INTEGER REFERENCES imports(id), raw TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS payments_id ON payments(sub_order_id);
CREATE TABLE IF NOT EXISTS ads (
 fingerprint TEXT PRIMARY KEY, spend_start TEXT NOT NULL, spend_end TEXT NOT NULL,
 deduction_date TEXT NOT NULL, campaign TEXT, cost INTEGER NOT NULL,
 import_id INTEGER REFERENCES imports(id), raw TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS other_payments (
 fingerprint TEXT PRIMARY KEY, kind TEXT, payment_date TEXT, amount INTEGER,
 import_id INTEGER REFERENCES imports(id), raw TEXT
);
CREATE TABLE IF NOT EXISTS products (sku TEXT PRIMARY KEY, display_sku TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS product_preferences (
 sku TEXT PRIMARY KEY REFERENCES products(sku), disabled INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS order_current (
 sub_order_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL REFERENCES order_events(fingerprint)
);
CREATE TABLE IF NOT EXISTS import_runs (
 id INTEGER PRIMARY KEY, import_id INTEGER REFERENCES imports(id),
 applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, summary TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS product_versions (
 id INTEGER PRIMARY KEY, sku TEXT REFERENCES products(sku), effective_date TEXT NOT NULL,
 cost INTEGER, packaging INTEGER, saved_at TEXT DEFAULT CURRENT_TIMESTAMP,
 source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS product_dates ON product_versions(sku,effective_date);
CREATE TABLE IF NOT EXISTS recovery_events (
 id INTEGER PRIMARY KEY, sub_order_id TEXT NOT NULL, effective_date TEXT NOT NULL,
 state TEXT NOT NULL, note TEXT NOT NULL, saved_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT OR IGNORE INTO settings VALUES ('gst_rate','18');
INSERT OR IGNORE INTO settings VALUES ('include_gst','true');
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
    if not db.execute("SELECT 1 FROM settings WHERE key='sku_discovery_v1'").fetchone():
        for row in db.execute("SELECT DISTINCT sku FROM order_events UNION SELECT DISTINCT sku FROM payments WHERE sku IS NOT NULL"):
            display = str(row[0] or '').strip()
            if display:
                db.execute('INSERT OR IGNORE INTO products(sku,display_sku) VALUES (?,?)', (' '.join(display.split()).casefold(), display))
        db.execute("INSERT INTO settings VALUES ('sku_discovery_v1','done')")
        db.commit()
    return db
