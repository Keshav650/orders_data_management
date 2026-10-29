"""Strict source readers. Source text is data, never executable instructions."""
import csv
import hashlib
import io
import json
import re
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import openpyxl


def text(value):
    return str(value).strip() if value is not None else ''


def sku_key(value):
    return ' '.join(text(value).split()).casefold()


def money(value, optional=False):
    if value is None or text(value) == '':
        if optional:
            return None
        return 0
    s = text(value).replace('₹', '').replace(',', '')
    if s.startswith('(') and s.endswith(')'):
        s = '-' + s[1:-1]
    try:
        n = Decimal(s)
        if not n.is_finite():
            raise ValueError('Amount must be finite')
        return int((n * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except InvalidOperation as exc:
        raise ValueError(f'Invalid amount: {value}') from exc


def day(value):
    if isinstance(value, (datetime, date)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    s = text(value)
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
        try:
            return datetime.strptime(s[:10], fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f'Invalid or missing date: {value}')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def status(value):
    s = text(value).upper().replace(' ', '_')
    return {'RTO_COMPLETE': 'RTO', 'RETURNED': 'RETURN',
            'DOOR_STEP_EXCHANGED': 'EXCHANGE', 'CANCELED': 'CANCELLED'}.get(s, s)


def unique_headers(row):
    result, counts = [], Counter()
    for item in row:
        label = text(item)
        counts[label] += 1
        result.append(label if counts[label] == 1 else f'{label} [{counts[label]}]')
    return result


def read_workbook(payload):
    workbook = openpyxl.load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
    try:
        for sheet in workbook:
            # Meesho exports incorrectly declare A1:A1 as the used range.
            sheet.reset_dimensions()
            yield sheet.title, list(sheet.values)
    finally:
        workbook.close()


def parse_file(name, payload, kind, effective_date):
    if kind not in ('orders', 'payments', 'products'):
        raise ValueError('Choose orders, payments, or products')
    effective_date = day(effective_date)
    parsed = {'name': name, 'digest': hashlib.sha256(payload).hexdigest(), 'kind': kind,
              'payload': payload, 'rows': [], 'records': [], 'warnings': []}
    if kind == 'orders':
        if not name.lower().endswith('.csv'):
            raise ValueError('Orders must be a CSV file')
        try:
            decoded = payload.decode('utf-8-sig')
        except UnicodeDecodeError:
            decoded = payload.decode('cp1252')
        tables = [('Orders', list(csv.reader(io.StringIO(decoded))))]
    elif name.lower().endswith('.zip') and kind == 'payments':
        tables = []
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            entries = [f for f in archive.infolist() if f.filename.lower().endswith('.xlsx') and not f.filename.startswith('__MACOSX')]
            if not entries:
                raise ValueError('ZIP contains no XLSX payment workbook')
            if sum(f.file_size for f in entries) > 200 * 1024 * 1024:
                raise ValueError('Uncompressed payment files exceed 200 MB')
            for entry in entries:
                tables.extend(read_workbook(archive.read(entry)))
    elif name.lower().endswith('.xlsx'):
        tables = list(read_workbook(payload))
    else:
        raise ValueError('Use an XLSX workbook or a payment ZIP')
    seen = Counter()
    for sheet, rows in tables:
        if kind == 'payments' and sheet not in ('Order Payments', 'Ads Cost', 'Referral Payments', 'Compensation and Recovery'):
            continue
        header_idx = 1 if kind == 'payments' else 0
        if len(rows) <= header_idx:
            continue
        headers = unique_headers(rows[header_idx])
        required = {'orders': ['Sub Order No', 'Order Date', 'SKU', 'Quantity', 'Reason for Credit Entry'],
                    'products': [], 'payments': []}[kind]
        if kind == 'payments':
            required = {'Order Payments': ['Sub Order No', 'Payment Date', 'Final Settlement Amount'],
                        'Ads Cost': ['Deduction Duration', 'Deduction Date', 'Total Ads Cost'],
                        'Referral Payments': ['Payment Date', 'Net Referral Amount'],
                        'Compensation and Recovery': ['Date', 'Amount (inc GST) INR']}[sheet]
        if any(h not in headers for h in required):
            raise ValueError(f'{sheet}: missing columns: {", ".join(h for h in required if h not in headers)}')
        if kind == 'products':
            if len(headers) != 2 or not any('cost' in h.lower() for h in headers):
                raise ValueError(f'{sheet}: expected two columns, SKU and Cost Price')
            cost_header = next(h for h in headers if 'cost' in h.lower())
            sku_header = next(h for h in headers if h != cost_header)
        start = 3 if kind == 'payments' else 1
        for index, values in enumerate(rows[start:], start + 1):
            if not any(v is not None and text(v) for v in values):
                continue
            if text(values[0]).startswith('No data is available'):
                continue
            raw = dict(zip(headers, values))
            parsed['rows'].append((sheet, index, json.dumps(raw, default=str)))
            try:
                if kind == 'orders':
                    sid, sku = text(raw['Sub Order No']), text(raw['SKU'])
                    if not sid or not sku:
                        raise ValueError('Missing sub-order ID or SKU')
                    qty = Decimal(text(raw['Quantity']))
                    if qty < 1 or qty != qty.to_integral_value():
                        raise ValueError('Quantity must be a positive integer')
                    record = dict(sub_order_id=sid, order_date=day(raw['Order Date']), sku=sku,
                                  quantity=int(qty), listed_paise=money(raw.get('Supplier Discounted Price (Incl GST and Commision)', raw.get('Supplier Listed Price (Incl. GST + Commission)'))),
                                  status=status(raw['Reason for Credit Entry']), packet=text(raw.get('Packet Id')))
                    table = 'order_events'
                elif kind == 'products':
                    sku = text(raw[sku_header])
                    if not sku:
                        raise ValueError('Missing SKU')
                    cost = money(raw[cost_header], optional=True)
                    if cost is None or cost < 0:
                        raise ValueError('Product cost must be a non-negative amount')
                    record = dict(sku=sku_key(sku), display_sku=sku, cost=cost, effective_date=effective_date)
                    table = 'product_versions'
                elif sheet == 'Order Payments':
                    sid = text(raw['Sub Order No'])
                    if not sid or raw['Final Settlement Amount'] is None:
                        raise ValueError('Missing sub-order ID or settlement')
                    record = dict(sub_order_id=sid, order_date=day(raw['Order Date']),
                                  payment_date=day(raw['Payment Date']), transaction_id=text(raw.get('Transaction ID')),
                                  status=status(raw.get('Live Order Status')), sku=text(raw.get('Supplier SKU')),
                                  settlement=money(raw['Final Settlement Amount']),
                                  shipping=money(raw.get('Shipping Charge (Incl. GST)')),
                                  reverse_shipping=money(raw.get('Return Shipping Charge (Incl. GST)')),
                                  tds=money(raw.get('TDS')), tcs=money(raw.get('TCS')),
                                  sale=money(raw.get('Total Sale Amount (Incl. Shipping & GST)')),
                                  sale_return=money(raw.get('Total Sale Return Amount (Incl. Shipping & GST)')))
                    table = 'payments'
                elif sheet == 'Ads Cost':
                    dates = re.findall(r'\d{4}-\d{2}-\d{2}', text(raw['Deduction Duration']))
                    if not dates:
                        dates = [day(raw['Deduction Duration'])]
                    record = dict(spend_start=day(dates[0]), spend_end=day(dates[-1]),
                                  deduction_date=day(raw['Deduction Date']), campaign=text(raw.get('Campaign ID')),
                                  cost=-money(raw['Total Ads Cost']))
                    if record['spend_start'] > record['spend_end']:
                        raise ValueError('Ads period ends before it starts')
                    table = 'ads'
                else:
                    referral = sheet == 'Referral Payments'
                    record = dict(kind=sheet, payment_date=day(raw['Payment Date' if referral else 'Date']),
                                  amount=money(raw['Net Referral Amount' if referral else 'Amount (inc GST) INR']))
                    table = 'other_payments'
                # Status/name fields can change on a re-export; financial identity must not.
                identity = dict(raw) if table in ('payments', 'ads', 'other_payments') else record
                if table == 'payments':
                    for k in ('Live Order Status', 'Product Name', 'Supplier SKU'):
                        identity.pop(k, None)
                fp = digest([table, identity])
                seen[(table, fp)] += 1
                if seen[(table, fp)] > 1:
                    parsed['warnings'].append(f'{sheet} row {index}: repeated identical record; retained in source history, counted once.')
                    continue
                parsed['records'].append((table, fp, record, json.dumps(raw, default=str)))
            except (ValueError, InvalidOperation, KeyError) as exc:
                raise ValueError(f'{name} / {sheet}, row {index}: {exc}') from exc
    if not parsed['records']:
        if kind != 'payments':
            raise ValueError('No data records found')
        parsed['warnings'].append('No payment or advertising records in this file.')
    product_costs = {}
    for table, fp, record, raw in parsed['records']:
        if table == 'product_versions':
            if record['sku'] in product_costs and product_costs[record['sku']] != record['cost']:
                raise ValueError(f'Conflicting costs for {record["display_sku"]}; correct the source before importing')
            product_costs[record['sku']] = record['cost']
    parsed['counts'] = dict(Counter(r[0] for r in parsed['records']))
    return parsed


def payment_signature(record, raw):
    """Match an unidentified payout only when all other source details agree."""
    details = dict(json.loads(raw) if isinstance(raw, str) else raw)
    for key in ('Transaction ID', 'Live Order Status', 'Product Name', 'Supplier SKU'):
        details.pop(key, None)
    financial = {k: record.get(k) for k in ('sub_order_id', 'order_date', 'payment_date',
                 'settlement', 'shipping', 'reverse_shipping', 'tds', 'tcs', 'sale', 'sale_return')}
    return digest([financial, details])


def counted_payments(rows):
    """Prefer the identified version of a payout, retaining distinct bank IDs."""
    groups = defaultdict(list)
    for row in rows:
        groups[payment_signature(row, row['raw'])].append(row)
    result = []
    for group in groups.values():
        identified = [r for r in group if r['transaction_id']]
        if len({r['transaction_id'] for r in identified}) == 1:
            result.extend(identified)
        else:
            result.extend(group)
    return result


def payment_key(record, raw):
    """A payout ID is scoped to a sub-order, date and price type, not an order."""
    if not record.get('transaction_id'):
        return None
    details = json.loads(raw) if isinstance(raw, str) else raw
    return (record['sub_order_id'], record['transaction_id'], record['payment_date'], text(details.get('Price Type')))


def matching_record(db, table, fp, record, raw):
    if table == 'order_events':
        return db.execute('SELECT fingerprint FROM order_events WHERE sub_order_id=? LIMIT 1', (record['sub_order_id'],)).fetchone()
    if table == 'product_versions':
        return db.execute('SELECT id FROM product_versions WHERE sku=? AND effective_date=? LIMIT 1', (record['sku'],record['effective_date'])).fetchone()
    exact = db.execute(f'SELECT fingerprint FROM {table} WHERE fingerprint=?', (fp,)).fetchone()
    if exact:
        return exact
    if table == 'payments' and payment_key(record, raw):
        key = payment_key(record, raw)
        candidates = [r for r in db.execute('SELECT * FROM payments WHERE sub_order_id=? AND transaction_id=? AND payment_date=?', key[:3]) if payment_key(dict(r), r['raw']) == key]
        if len(candidates) > 1:
            raise ValueError('Ambiguous payment identity: multiple records for the same sub-order, transaction, date and price type. Review the source before updating.')
        if candidates:
            return candidates[0]
    if table == 'payments':
        signature = payment_signature(record, raw)
        candidates = [r for r in db.execute('SELECT * FROM payments WHERE sub_order_id=? AND payment_date=?',
                      (record['sub_order_id'], record['payment_date']))
                      if payment_signature(dict(r), r['raw']) == signature]
        identified = {r['transaction_id'] for r in candidates if r['transaction_id']}
        if len(identified | ({record['transaction_id']} if record['transaction_id'] else set())) <= 1:
            fallback = [r for r in candidates if not r['transaction_id'] or not record['transaction_id']]
            if len(fallback) == 1:
                return fallback[0]
    if table == 'ads' and record.get('campaign'):
        candidates = db.execute('SELECT fingerprint FROM ads WHERE campaign=? AND spend_start=? AND spend_end=? AND deduction_date=?',
                                (record['campaign'],record['spend_start'],record['spend_end'],record['deduction_date'])).fetchall()
        if len(candidates) > 1:
            raise ValueError('Ambiguous advertising records for the same campaign and deduction period; review before updating.')
        return candidates[0] if candidates else None
    return None


def preview(db, parsed):
    existing = db.execute('SELECT id FROM imports WHERE digest=?', (parsed['digest'],)).fetchone()
    duplicates = 0
    payment_keys = {}
    ad_keys = {}
    for table, fp, record, raw in parsed['records']:
        if table == 'payments':
            key = payment_key(record, raw)
            if key and key in payment_keys and payment_keys[key] != fp:
                raise ValueError('The file contains conflicting rows for the same payment identity. Separate or correct those rows before importing.')
            if key:
                payment_keys[key] = fp
        if table == 'ads' and record.get('campaign'):
            key = tuple(record[k] for k in ('campaign','spend_start','spend_end','deduction_date'))
            if key in ad_keys and ad_keys[key] != fp:
                raise ValueError('The file contains conflicting advertising rows for the same campaign and deduction period.')
            ad_keys[key] = fp
        duplicates += bool(matching_record(db, table, fp, record, raw))
    return {'name': parsed['name'], 'kind': parsed['kind'], 'counts': parsed['counts'],
            'already_imported': bool(existing), 'overlapping_records': duplicates,
            'new_records': len(parsed['records'])-duplicates, 'warnings': parsed['warnings'][:20]}


def commit_file(db, parsed):
    report = preview(db, parsed)
    existing = db.execute('SELECT id FROM imports WHERE digest=?', (parsed['digest'],)).fetchone()
    if existing:
        iid = existing['id']
    else:
        cur = db.execute('INSERT INTO imports(name,digest,kind,payload,summary) VALUES (?,?,?,?,?)',
                         (parsed['name'], parsed['digest'], parsed['kind'], parsed['payload'], json.dumps(report)))
        iid = cur.lastrowid
        db.executemany('INSERT INTO raw_rows(import_id,sheet,row_number,payload) VALUES (?,?,?,?)',
                       [(iid, *row) for row in parsed['rows']])
    added = updated = 0
    for table, fp, record, raw in parsed['records']:
        old_record = matching_record(db, table, fp, record, raw)
        updated += bool(old_record)
        added += not bool(old_record)
        if table in ('order_events', 'payments', 'product_versions') and text(record.get('sku')):
            key = sku_key(record['sku'])
            db.execute('INSERT OR IGNORE INTO products(sku,display_sku) VALUES (?,?)', (key, record.get('display_sku', record['sku'])))
        if table == 'product_versions':
            old = db.execute('SELECT * FROM product_versions WHERE sku=? AND effective_date<=? ORDER BY effective_date DESC,id DESC LIMIT 1',
                             (record['sku'], record['effective_date'])).fetchone()
            db.execute('INSERT INTO product_versions(sku,effective_date,cost,packaging,source) VALUES (?,?,?,?,?)',
                       (record['sku'], record['effective_date'], record['cost'], old['packaging'] if old else None, f'Import {iid}'))
        else:
            # Keep all source files, but count the latest version of a transaction once.
            target_fp = old_record['fingerprint'] if table != 'order_events' and old_record else fp
            data = dict(record, fingerprint=target_fp, import_id=iid, raw=raw)
            if table == 'payments' and old_record and not record['transaction_id']:
                previous = db.execute('SELECT transaction_id FROM payments WHERE fingerprint=?', (target_fp,)).fetchone()
                data['transaction_id'] = previous['transaction_id']
            assignments = ','.join(f'{k}=excluded.{k}' for k in data if k != 'fingerprint')
            db.execute(f'INSERT INTO {table} ({",".join(data)}) VALUES ({",".join("?" for _ in data)}) ON CONFLICT(fingerprint) DO UPDATE SET {assignments}', tuple(data.values()))
            if table == 'order_events':
                db.execute('INSERT INTO order_current VALUES (?,?) ON CONFLICT(sub_order_id) DO UPDATE SET fingerprint=excluded.fingerprint', (record['sub_order_id'], fp))
    report.update(added_records=added, updated_records=updated)
    db.execute('INSERT INTO import_runs(import_id,summary) VALUES (?,?)', (iid,json.dumps(report)))
    return report
