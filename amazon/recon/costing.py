"""Dated purchase-cost lookup, including first-entry and export text recovery."""
import re


def sku_key(value):
    return ' '.join(str(value or '').strip().split()).casefold()


def packaging_cost(versions):
    """Latest saved packaging applies to every order date for this SKU."""
    manual = [v for v in versions if v['source'] in ('Manual update', 'Packaging update')]
    candidates = manual or [v for v in versions if v.get('packaging') is not None]
    return max(candidates, key=lambda v: v['id'])['packaging'] if candidates else None


def match_key(sku):
    return re.sub(r'\s+[?–—-]\s+', ' ', sku).strip()


def purchase_cost(versions, sku, order_date):
    """Return (cost, source SKU, initial-price fallback).

    The first price supplies previously unpriced history. An explicit later
    blank remains unknown; existing dated prices, including zero, are preserved.
    """
    key = sku_key(sku)
    price_versions = {k: [v for v in rows if v.get('source') != 'Packaging update'] for k, rows in versions.items()}
    local = price_versions.get(key, [])
    source = key
    if not any(v['cost'] is not None for v in local):
        candidates = [k for k, rows in price_versions.items()
                      if k != key and match_key(k) == match_key(key)
                      and any(v['cost'] is not None for v in rows)]
        if len(candidates) == 1:
            source = candidates[0]
            local = price_versions[source]
    dated = next((v for v in reversed(local) if v['effective_date'] <= order_date), None)
    if dated and dated['cost'] is not None:
        return dated['cost'], source, False
    known = [v for v in local if v['cost'] is not None]
    if known and order_date < known[0]['effective_date']:
        first_date = known[0]['effective_date']
        first = next(v for v in reversed(known) if v['effective_date'] == first_date)
        return first['cost'], source, True
    return None, source, False
