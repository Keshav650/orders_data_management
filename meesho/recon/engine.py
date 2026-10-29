"""One calculation path for the dashboard, order detail, and exports.

All stored money is integer paise. Decimal is used only for tax extraction
and inclusive-day allocation of advertising periods.
"""
import json
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from .importer import day, sku_key, money, counted_payments
from .costing import purchase_cost, packaging_cost


def rounded(value):
    return int(Decimal(value).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def get_settings(db):
    return dict(db.execute('SELECT key,value FROM settings').fetchall())


def save_settings(db, values):
    rate = Decimal(str(values['gst_rate']))
    if not rate.is_finite() or rate < 0 or rate > 100:
        raise ValueError('GST must be between 0 and 100 percent')
    if not isinstance(values['include_gst'], bool):
        raise ValueError('Include GST must be checked or unchecked')
    db.executemany('UPDATE settings SET value=? WHERE key=?',
                   [(str(rate), 'gst_rate'), ('true' if values['include_gst'] else 'false', 'include_gst')])


def save_products(db, updates, effective_date):
    effective_date = day(effective_date)
    if not updates:
        raise ValueError('No products selected')
    for update in updates:
        sku = sku_key(update['sku'])
        if not db.execute('SELECT 1 FROM products WHERE sku=?', (sku,)).fetchone():
            raise ValueError(f'Unknown SKU: {sku}')
        old = db.execute("SELECT * FROM product_versions WHERE sku=? AND effective_date<=? AND source!='Packaging update' ORDER BY effective_date DESC,id DESC LIMIT 1", (sku, effective_date)).fetchone()
        values = {}
        for key in ('cost', 'packaging'):
            values[key] = money(update[key], optional=True) if key in update else (old[key] if old else None)
            if values[key] is not None and values[key] < 0:
                raise ValueError('Costs cannot be negative')
        if 'packaging' not in update:
            values['packaging'] = packaging_cost(list(db.execute('SELECT * FROM product_versions WHERE sku=?', (sku,))))
        db.execute('INSERT INTO product_versions(sku,effective_date,cost,packaging,source) VALUES (?,?,?,?,?)',
                   (sku, effective_date, values['cost'], values['packaging'], 'Manual update' if 'cost' in update else 'Packaging update'))


def create_product(db, values):
    display = str(values.get('sku', '')).strip()
    if not display or len(display) > 1000:
        raise ValueError('Enter a SKU name (up to 1000 characters)')
    sku = sku_key(display)
    if db.execute('SELECT 1 FROM products WHERE sku=?', (sku,)).fetchone():
        raise ValueError('This SKU already exists. Find it in Products & costs and edit it.')
    if money(values.get('cost'), optional=True) is None:
        raise ValueError('Enter a product cost, including 0 if appropriate')
    db.execute('INSERT INTO products VALUES (?,?)', (sku, display))
    save_products(db, [dict(sku=sku, cost=values['cost'], packaging=values.get('packaging', ''))], values['effective_date'])


def products(db, effective_date):
    result = []
    versions = defaultdict(list)
    for row in db.execute('SELECT * FROM product_versions ORDER BY effective_date,id'):
        versions[row['sku']].append(dict(row))
    for p in db.execute('SELECT p.*,COALESCE(f.disabled,0) disabled FROM products p LEFT JOIN product_preferences f ON p.sku=f.sku ORDER BY display_sku COLLATE NOCASE'):
        version = db.execute("SELECT * FROM product_versions WHERE sku=? AND effective_date<=? AND source!='Packaging update' ORDER BY effective_date DESC,id DESC LIMIT 1", (p['sku'], effective_date)).fetchone()
        cost, source, initial = purchase_cost(versions, p['sku'], effective_date)
        result.append(dict(p, cost=cost, cost_source_sku=source, initial_cost_applied=initial, packaging=packaging_cost(versions[p['sku']]),
                           effective_date=version['effective_date'] if version else None))
    return result


def set_product_disabled(db, sku, disabled):
    if not isinstance(disabled, bool):
        raise ValueError('Disabled must be true or false')
    key = sku_key(sku)
    if not db.execute('SELECT 1 FROM products WHERE sku=?', (key,)).fetchone():
        raise ValueError('Unknown SKU')
    db.execute('INSERT INTO product_preferences(sku,disabled) VALUES (?,?) ON CONFLICT(sku) DO UPDATE SET disabled=excluded.disabled', (key, int(disabled)))


def save_recovery(db, values):
    if values['state'] not in ('awaiting', 'sellable', 'damaged', 'lost'):
        raise ValueError('Unknown recovery state')
    sid = str(values['sub_order_id'])
    if not db.execute('SELECT 1 FROM order_events WHERE sub_order_id=?', (sid,)).fetchone():
        raise ValueError('Unknown sub-order ID')
    db.execute('INSERT INTO recovery_events(sub_order_id,effective_date,state,note) VALUES (?,?,?,?)',
               (sid, day(values['effective_date']), values['state'], str(values.get('note', ''))[:1000]))


def report(db, start=None, end=None, as_of=None, mode='mature', sku=None, skus=None):
    selected_skus = set(sku_key(s) for s in (skus or ([sku] if sku else [])) if s)
    sku = bool(selected_skus)
    as_of = day(as_of or date.today().isoformat())
    cutoff = (date.fromisoformat(as_of) - timedelta(days=21)).isoformat()
    earliest, latest = db.execute('SELECT MIN(order_date),MAX(order_date) FROM order_events').fetchone()
    # Limit the default spend period to actual imported order coverage. This
    # avoids charging August ads to July-only data just because August is old.
    end = min(cutoff, latest or cutoff) if mode == 'mature' else day(end or as_of)
    start = day(start) if start else (date.fromisoformat(end) - timedelta(days=29)).isoformat()
    if start > end:
        if mode != 'mature':
            raise ValueError('Order start date must not be after end date')
    if end > as_of:
        raise ValueError('Order end date must be on or before payments-through date')
    settings = get_settings(db)
    tax_factor = Decimal(1) + Decimal(settings['gst_rate']) / 100
    include_gst = settings['include_gst'] == 'true'
    grouped = defaultdict(list)
    all_ids = set()
    for row in db.execute('SELECT e.* FROM order_events e LEFT JOIN order_current c ON c.sub_order_id=e.sub_order_id WHERE c.fingerprint IS NULL OR c.fingerprint=e.fingerprint ORDER BY e.import_id,e.fingerprint'):
        row = dict(row)
        all_ids.add(row['sub_order_id'])
        if start <= row['order_date'] <= end:
            grouped[row['sub_order_id']].append(row)
    ledger = defaultdict(list)
    unmatched = []
    payment_rows = [dict(row) for row in db.execute('SELECT p.*,i.name source_file FROM payments p JOIN imports i ON p.import_id=i.id WHERE payment_date<=? ORDER BY payment_date,import_id,fingerprint', (as_of,))]
    for row in counted_payments(payment_rows):
        if row['sub_order_id'] not in all_ids:
            unmatched.append(row)
        ledger[row['sub_order_id']].append(row)
    versions = defaultdict(list)
    for row in db.execute('SELECT * FROM product_versions ORDER BY effective_date,id'):
        versions[row['sku']].append(dict(row))
    recoveries = {}
    for row in db.execute('SELECT * FROM recovery_events WHERE effective_date<=? ORDER BY effective_date,id', (as_of,)):
        recoveries[row['sub_order_id']] = dict(row)
    result, issues = [], []
    sku_options = {}
    totals = Counter()
    timeline = defaultdict(lambda: Counter())
    sku_totals = defaultdict(lambda: Counter())
    for sid, events in grouped.items():
        base = events[-1]
        order_sku = sku_key(base['sku'])
        sku_options[order_sku] = base['sku']
        if selected_skus and order_sku not in selected_skus:
            continue
        entries = ledger[sid]
        order_states = {e['status'] for e in events}
        payment_states = {p['status'] for p in entries if p['status']}
        # Cancellation/RTO/exchange are explicit order events. Customer returns
        # are often absent from Orders CSV, so payment Return evidence is retained.
        if 'CANCELLED' in order_states:
            current_status = 'CANCELLED'
        elif 'RETURN' in payment_states or 'RETURN' in order_states:
            current_status = 'RETURN'
        elif 'RTO' in order_states or 'RTO' in payment_states:
            current_status = 'RTO'
        elif 'EXCHANGE' in order_states or 'EXCHANGE' in payment_states:
            current_status = 'EXCHANGE'
        elif 'LOST' in order_states:
            current_status = 'LOST'
        else:
            current_status = base['status'] or next((p['status'] for p in reversed(entries) if p['status']), 'UNKNOWN')
        cancelled = current_status == 'CANCELLED'
        row_issues = []
        if len({(e['sku'], e['quantity'], e['order_date']) for e in events}) > 1:
            row_issues.append('Conflicting order SKU, quantity, or date; latest imported row used')
        if len(events) > 1:
            row_issues.append('Multiple order events; counted once per sub-order')
        if order_states != payment_states and payment_states and (payment_states - order_states):
            row_issues.append('Order/payment status difference; inspect history')
        if cancelled and entries and sum(e['settlement'] for e in entries):
            row_issues.append('Cancelled order has financial transactions; excluded from profit')
        if current_status == 'RTO' and any(e['reverse_shipping'] for e in entries):
            row_issues.append('Unexpected RTO reverse shipping deduction')
        if current_status == 'EXCHANGE':
            row_issues.append('Exchange: one product cost counted; additional replacement costs need review')
        unit_cost, cost_source_sku, initial_cost_applied = purchase_cost(versions, sku_key(base['sku']), base['order_date'])
        packaging = packaging_cost(versions[sku_key(base['sku'])])
        recovery = recoveries.get(sid, {})
        # RTO stock is reusable by default; explicit dated outcomes override it.
        default_recovery = {'RTO': 'sellable', 'LOST': 'lost'}.get(current_status, 'awaiting')
        recovery_state = recovery.get('state', default_recovery) if current_status in ('RETURN', 'RTO', 'LOST', 'EXCHANGE') else None
        if cancelled:
            product_cost, packaging = 0, 0
        elif recovery_state == 'sellable' and current_status in ('RETURN', 'RTO'):
            product_cost = 0
        elif unit_cost is None:
            product_cost = None
            row_issues.append('Missing product cost for order date')
        else:
            product_cost = rounded(Decimal(unit_cost * base['quantity']) / (1 if include_gst else tax_factor))
        if not cancelled and packaging is None:
            row_issues.append('Missing packaging cost for order date')
        if not cancelled and recovery_state == 'awaiting':
            row_issues.append('Recovery unconfirmed; product cost retained')
        financial = {key: sum(p[key] for p in entries) for key in ('settlement', 'shipping', 'reverse_shipping', 'tds', 'tcs', 'sale', 'sale_return')}
        due = (date.fromisoformat(base['order_date']) + timedelta(days=21)).isoformat()
        payment_state = ('Excluded' if cancelled else ('Positive payment recorded' if financial['settlement'] > 0 else 'Zero payment recorded' if financial['settlement'] == 0 else 'Negative payment recorded')) if entries or cancelled else ('Overdue / no record' if due <= as_of else 'Within 21 days')
        contribution = 0 if cancelled else financial['settlement'] - (product_cost or 0) - (packaging or 0)
        order = dict(sub_order_id=sid, sku=base['sku'], quantity=base['quantity'], order_date=base['order_date'], status=current_status,
                     source_statuses=sorted(order_states), payment_statuses=sorted(payment_states), payment_state=payment_state,
                     payment_date=max((p['payment_date'] for p in entries), default=None), payment_count=len(entries),
                     due_date=due, listed_value=base['listed_paise'] * base['quantity'], unit_cost=unit_cost,
                     product_cost=product_cost, packaging=packaging, contribution=contribution,
                     costs_missing=product_cost is None or packaging is None,
                     cost_source_sku=cost_source_sku, initial_cost_applied=initial_cost_applied,
                     recovery=recovery_state, recovery_note=recovery.get('note', ''), issues=row_issues, **financial)
        result.append(order)
        for issue in row_issues:
            issues.append({'sub_order_id': sid, 'sku': base['sku'], 'issue': issue})
        totals['cancelled' if cancelled else 'orders'] += 1
        if cancelled:
            continue
        totals[current_status] += 1
        for key, value in financial.items():
            totals[key] += value
        totals['product_cost'] += product_cost or 0
        totals['packaging'] += packaging or 0
        totals['missing_cost'] += product_cost is None
        totals['missing_packaging'] += packaging is None
        totals['recovery_pending'] += recovery_state == 'awaiting'
        totals['awaiting'] += not entries
        totals['overdue'] += not entries and due <= as_of
        totals['within_window'] += not entries and due > as_of
        totals['recorded'] += bool(entries)
        totals['listed_value'] += order['listed_value']
        t = timeline[base['order_date']]
        t['orders'] += 1
        t['settlement'] += financial['settlement']
        t['listed_value'] += order['listed_value']
        s = sku_totals[order_sku]
        s['orders'] += 1
        s['contribution'] += contribution or 0
        s['incomplete'] += product_cost is None or packaging is None
        s['returns'] += current_status == 'RETURN'
        s['rto'] += current_status == 'RTO'
        for key in ('settlement', 'product_cost', 'packaging', 'quantity', 'listed_value'):
            s[key] += order[key] or 0
        s['awaiting'] += not entries
        s['provisional'] += bool(order['costs_missing'] or not entries or recovery_state == 'awaiting' or current_status == 'EXCHANGE')
    advertising = []
    for a in db.execute('SELECT a.*,i.name source_file FROM ads a JOIN imports i ON a.import_id=i.id ORDER BY spend_start'):
        a = dict(a)
        lower, upper = max(a['spend_start'], start), min(a['spend_end'], end, as_of)
        if lower <= upper:
            days = (date.fromisoformat(a['spend_end']) - date.fromisoformat(a['spend_start'])).days + 1
            count = (date.fromisoformat(upper) - date.fromisoformat(lower)).days + 1
            a['allocated_cost'] = rounded(Decimal(a['cost']) * count / days)
            advertising.append(a)
    period_ads = sum(a['allocated_cost'] for a in advertising)
    # Campaign spend has no reliable SKU mapping in these exports.
    ads_cost = 0 if sku else period_ads
    fields = ['orders', 'cancelled', 'settlement', 'product_cost', 'packaging', 'shipping', 'reverse_shipping', 'tds', 'tcs',
              'sale', 'sale_return', 'missing_cost', 'missing_packaging', 'awaiting', 'overdue', 'within_window', 'recorded', 'recovery_pending', 'listed_value']
    summary = {key: totals[key] for key in fields}
    summary.update(ads=ads_cost, unallocated_ads=period_ads if sku else 0, profit_before_ads=bool(sku), net_profit=totals['settlement'] - totals['product_cost'] - totals['packaging'] - ads_cost,
                   provisional=bool(totals['awaiting'] or totals['recovery_pending'] or totals['missing_cost'] or totals['missing_packaging'] or any('Conflicting' in i['issue'] or 'Exchange:' in i['issue'] for i in issues)),
                   customer_return_rate=round(100 * totals['RETURN'] / totals['orders'], 2) if totals['orders'] else 0,
                   rto_rate=round(100 * totals['RTO'] / totals['orders'], 2) if totals['orders'] else 0,
                   statuses={key: totals[key] for key in ('DELIVERED', 'RETURN', 'RTO', 'EXCHANGE', 'LOST', 'SHIPPED')})
    coverage = dict(db.execute('SELECT MIN(payment_date) first_payment,MAX(payment_date) last_payment FROM payments').fetchone())
    coverage.update(first_order=earliest, last_order=latest, last_import=db.execute('SELECT MAX(applied_at) FROM import_runs').fetchone()[0] or db.execute('SELECT MAX(imported_at) FROM imports').fetchone()[0])
    return {'filters': dict(start=start, end=end, as_of=as_of, mode=mode, cutoff=cutoff, sku=next(iter(selected_skus)) if len(selected_skus)==1 else '', skus=sorted(selected_skus)), 'settings': settings,
            'sku_options': [dict(value=k, label=v) for k,v in sorted(sku_options.items())],
            'summary': summary, 'orders': sorted(result, key=lambda o: (o['order_date'], o['sub_order_id']), reverse=True),
            'issues': issues, 'unmatched_payments': unmatched, 'ads': advertising,
            'other_payments': [dict(r) for r in db.execute('SELECT * FROM other_payments WHERE payment_date BETWEEN ? AND ?', (start, as_of))],
            'timeline': [dict(date=k, **v) for k, v in sorted(timeline.items())],
            'sku_performance': [dict(sku=sku_options[k], sku_key=k, **v) for k, v in sorted(sku_totals.items(), key=lambda kv: kv[1]['contribution'], reverse=True)],
            'coverage': coverage}


def order_detail(db, sid):
    payments = [dict(r) for r in db.execute('SELECT p.*,i.name source_file FROM payments p JOIN imports i ON p.import_id=i.id WHERE sub_order_id=? ORDER BY payment_date', (sid,))]
    counted = {p['fingerprint'] for p in counted_payments(payments)}
    for payment in payments:
        payment['counted'] = payment['fingerprint'] in counted
    return {'order_events': [dict(r) for r in db.execute('SELECT e.*,i.name source_file FROM order_events e JOIN imports i ON e.import_id=i.id WHERE sub_order_id=?', (sid,))],
            'payments': payments,
            'recovery_history': [dict(r) for r in db.execute('SELECT * FROM recovery_events WHERE sub_order_id=? ORDER BY effective_date,id', (sid,))]}
