"""Explicit data repairs, run only when requested; never automatic on startup."""
from .importer import sku_key


def backfill_initial_packaging(db):
    """Apply each SKU's first configured packaging rate to earlier blank periods.

    Preserve all purchase prices, explicit zero packaging, and later dated rates.
    Append audit versions rather than editing existing records.
    """
    starts = {}
    for row in db.execute('SELECT sku,MIN(order_date) first_order FROM order_events GROUP BY sku'):
        key = sku_key(row['sku'])
        starts[key] = min(starts.get(key, row['first_order']), row['first_order'])
    changed = 0
    for sku, start in starts.items():
        versions = [dict(v) for v in db.execute('SELECT * FROM product_versions WHERE sku=? ORDER BY effective_date,id', (sku,))]
        known = [v for v in versions if v['packaging'] is not None]
        if not known:
            continue
        first_date = known[0]['effective_date']
        first = next(v for v in reversed(known) if v['effective_date'] == first_date)
        dates = sorted({start} | {v['effective_date'] for v in versions if start <= v['effective_date'] < first_date})
        for applied in dates:
            if applied >= first_date:
                continue
            previous = next((v for v in reversed(versions) if v['effective_date'] <= applied), None)
            if previous and previous['packaging'] is not None:
                continue
            db.execute('INSERT INTO product_versions(sku,effective_date,cost,packaging,source) VALUES (?,?,?,?,?)',
                       (sku, applied, previous['cost'] if previous else None, first['packaging'],
                        f'Historical packaging repair: rate first saved for {first_date}'))
            changed += 1
    return changed
