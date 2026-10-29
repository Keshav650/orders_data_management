"""Strict source readers and idempotent upsert engine for Amazon files."""
import csv
import hashlib
import io
import json
import re
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

import openpyxl

from .costing import sku_key


def text(value):
    return str(value).strip() if value is not None else ''


def money(value, optional=False):
    if value is None or text(value) == '':
        return None if optional else 0
    s = text(value).replace('₹', '').replace(',', '').replace('INR', '').strip()
    if s.startswith('(') and s.endswith(')'):
        s = '-' + s[1:-1]
    try:
        n = Decimal(s)
        if not n.is_finite():
            raise ValueError('Amount must be finite')
        return int((n * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f'Invalid amount: {value}') from exc


def day(value):
    if isinstance(value, (datetime, date)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    s = text(value)
    if not s:
        raise ValueError('Missing date value')
    s = re.sub(r'\bSept\b', 'Sep', s)
    s = re.sub(r' UTC$', '', s).strip()
    for fmt in (
        '%d %b %Y %I:%M:%S %p',
        '%d %b %Y %H:%M:%S',
        '%Y-%m-%dT%H:%M:%S%z',
        '%Y-%m-%dT%H:%M:%S',
        '%d-%b-%Y',
        '%d-%B-%Y',
        '%b %d, %Y',
        '%B %d, %Y',
        '%Y-%m-%d',
        '%d/%m/%Y',
        '%d-%m-%Y',
    ):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(s).date().isoformat()
    except Exception:
        pass
    raise ValueError(f'Invalid or unrecognized date format: {value}')


def parse_date_range(value):
    s = text(value)
    if ' - ' in s:
        parts = s.split(' - ')
        return day(parts[0]), day(parts[1])
    d = day(s)
    return d, d


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def normalize_status(value):
    s = text(value).upper()
    if 'DELIVERED' in s:
        return 'DELIVERED'
    if 'CANCEL' in s:
        return 'CANCELLED'
    if 'RETURN' in s or 'RTO' in s:
        return 'RTO'
    if 'SHIPPED' in s:
        return 'SHIPPED'
    return s or 'UNKNOWN'


def read_lines_from_bytes(payload):
    for enc in ('utf-8-sig', 'utf-8', 'cp1252', 'latin1'):
        try:
            return payload.decode(enc)
        except UnicodeDecodeError:
            continue
    return payload.decode('utf-8', errors='replace')


def detect_file_kind(name, sample_text):
    lower = name.lower()
    first_lines = sample_text[:4000].lower()
    if 'amazon-order-id' in first_lines or 'order-item-id' in first_lines:
        return 'orders'
    if 'settlement id' in first_lines or 'total sales tax liable' in first_lines:
        return 'transactions'
    if 'return request date' in first_lines or 'return type' in first_lines:
        return 'returns'
    if 'advertised product sku' in first_lines:
        return 'ads_product'
    if 'search term' in first_lines:
        return 'ads_search_term'
    if 'campaign id' in first_lines or 'campaign name' in first_lines:
        return 'ads_campaign'
    if lower.endswith(('.xlsx', '.xls')):
        return 'products'
    return 'orders'


def parse_file(name, payload, kind=None, effective_date='0001-01-01'):
    effective_date = day(effective_date)
    file_digest = hashlib.sha256(payload).hexdigest()
    
    parsed = {
        'name': name,
        'digest': file_digest,
        'payload': payload,
        'rows': [],
        'records': [],
        'warnings': []
    }

    # Product Excel Workbook
    if name.lower().endswith(('.xlsx', '.xlsm')):
        kind = 'products'
        parsed['kind'] = kind
        wb = openpyxl.load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
        try:
            for sheet in wb:
                rows = list(sheet.values)
                if not rows:
                    continue
                headers = [text(h).lower() for h in rows[0] if h is not None]
                if not any('cost' in h or 'price' in h for h in headers):
                    continue
                cost_idx = next(i for i, h in enumerate(headers) if 'cost' in h or 'price' in h)
                sku_idx = next(i for i, h in enumerate(headers) if i != cost_idx)
                for idx, r in enumerate(rows[1:], 2):
                    if not r or r[sku_idx] is None:
                        continue
                    sku_val = text(r[sku_idx])
                    if not sku_val:
                        continue
                    cost_val = money(r[cost_idx], optional=True)
                    if cost_val is None or cost_val < 0:
                        continue
                    record = {
                        'sku': sku_key(sku_val),
                        'display_sku': sku_val,
                        'cost': cost_val,
                        'effective_date': effective_date
                    }
                    fp = digest(['product_versions', record['sku'], effective_date])
                    parsed['records'].append(('product_versions', fp, record, json.dumps(record)))
        finally:
            wb.close()
        parsed['counts'] = dict(Counter(r[0] for r in parsed['records']))
        return parsed

    text_content = read_lines_from_bytes(payload)
    detected = detect_file_kind(name, text_content)
    if not kind or kind in ('auto', 'ads') or kind not in ('orders', 'transactions', 'returns', 'ads_product', 'ads_campaign', 'ads_search_term', 'products'):
        kind = detected
    elif kind.startswith('ads') and detected.startswith('ads'):
        kind = detected
    elif detected in ('ads_product', 'ads_campaign', 'ads_search_term', 'transactions', 'orders', 'returns'):
        kind = detected
    parsed['kind'] = kind

    lines = text_content.splitlines()
    if not lines:
        raise ValueError('The uploaded file is empty')

    # Determine delimiter
    sample = '\n'.join(lines[:20])
    delimiter = '\t' if sample.count('\t') > sample.count(',') else ','

    # Custom handling for Amazon Unified Transactions which has 13 comment lines
    header_line_idx = 0
    if kind == 'transactions':
        for idx, line in enumerate(lines[:30]):
            line_lower = line.lower()
            if 'date/time' in line_lower and 'settlement id' in line_lower:
                header_line_idx = idx
                delimiter = ','
                break

    reader = csv.reader(io.StringIO('\n'.join(lines[header_line_idx:])), delimiter=delimiter)
    try:
        raw_headers = next(reader)
    except StopIteration:
        raise ValueError('No header row found in file')

    headers = [text(h) for h in raw_headers]
    header_map = {h.lower(): idx for idx, h in enumerate(headers)}

    for row_idx, values in enumerate(reader, header_line_idx + 2):
        if not values or not any(text(v) for v in values):
            continue
        raw_dict = {headers[i]: values[i] if i < len(values) else '' for i in range(len(headers))}
        parsed['rows'].append((kind, row_idx, json.dumps(raw_dict, default=str)))

        table = None
        fp = None
        record = None

        try:
            if kind == 'orders':
                order_id = text(raw_dict.get('amazon-order-id', raw_dict.get('order-id')))
                item_id = text(raw_dict.get('order-item-id', order_id))
                if not order_id:
                    continue
                if not item_id:
                    item_id = order_id
                sku = text(raw_dict.get('sku', raw_dict.get('Merchant SKU')))
                qty = int(Decimal(text(raw_dict.get('quantity', '1')) or '1'))
                raw_status = text(raw_dict.get('order-status', ''))
                norm_status = normalize_status(raw_status)
                p_date = day(raw_dict.get('purchase-date', date.today().isoformat()))
                
                record = {
                    'order_item_id': item_id,
                    'amazon_order_id': order_id,
                    'purchase_date': p_date,
                    'last_updated_date': day(raw_dict.get('last-updated-date', p_date)),
                    'order_status': norm_status,
                    'fulfillment_channel': text(raw_dict.get('fulfillment-channel')),
                    'sales_channel': text(raw_dict.get('sales-channel')),
                    'product_name': text(raw_dict.get('product-name')),
                    'sku': sku,
                    'asin': text(raw_dict.get('asin')),
                    'quantity': qty,
                    'currency': text(raw_dict.get('currency', 'INR')),
                    'item_price': money(raw_dict.get('item-price')),
                    'item_tax': money(raw_dict.get('item-tax')),
                    'shipping_price': money(raw_dict.get('shipping-price')),
                    'shipping_tax': money(raw_dict.get('shipping-tax')),
                    'gift_wrap_price': money(raw_dict.get('gift-wrap-price')),
                    'gift_wrap_tax': money(raw_dict.get('gift-wrap-tax')),
                    'item_discount': money(raw_dict.get('item-promotion-discount')),
                    'ship_city': text(raw_dict.get('ship-city')),
                    'ship_state': text(raw_dict.get('ship-state')),
                    'ship_postal_code': text(raw_dict.get('ship-postal-code')),
                    'ship_country': text(raw_dict.get('ship-country')),
                    'fulfilled_by': text(raw_dict.get('fulfilled-by')),
                    'is_prime': text(raw_dict.get('is-prime'))
                }
                table = 'orders'
                fp = item_id

            elif kind == 'transactions':
                settlement_id = text(raw_dict.get('settlement id'))
                t_type = text(raw_dict.get('type'))
                order_id = text(raw_dict.get('order id'))
                sku = text(raw_dict.get('Sku'))
                description = text(raw_dict.get('description'))
                post_raw = raw_dict.get('date/time')
                if not post_raw or not settlement_id:
                    continue
                post_date = day(post_raw)
                total_paise = money(raw_dict.get('total'))
                
                rel_raw = raw_dict.get('Transaction Release Date')
                release_date = day(rel_raw) if rel_raw and text(rel_raw) else None

                record = {
                    'settlement_id': settlement_id,
                    'post_date': post_date,
                    'type': t_type,
                    'order_id': order_id,
                    'sku': sku,
                    'description': description,
                    'quantity': int(Decimal(text(raw_dict.get('quantity') or '0'))),
                    'marketplace': text(raw_dict.get('marketplace')),
                    'account_type': text(raw_dict.get('account type')),
                    'fulfillment': text(raw_dict.get('fulfillment')),
                    'city': text(raw_dict.get('order city')),
                    'state': text(raw_dict.get('order state')),
                    'postal': text(raw_dict.get('order postal')),
                    'product_sales': money(raw_dict.get('product sales')),
                    'shipping_credits': money(raw_dict.get('shipping credits')),
                    'gift_wrap_credits': money(raw_dict.get('gift wrap credits')),
                    'promotional_rebates': money(raw_dict.get('promotional rebates')),
                    'tax_liable': money(raw_dict.get('Total sales tax liable(GST before adjusting TCS)')),
                    'tcs_cgst': money(raw_dict.get('TCS-CGST')),
                    'tcs_sgst': money(raw_dict.get('TCS-SGST')),
                    'tcs_igst': money(raw_dict.get('TCS-IGST')),
                    'tds': money(raw_dict.get('TDS (Section 194-O)')),
                    'selling_fees': money(raw_dict.get('selling fees')),
                    'fba_fees': money(raw_dict.get('fba fees')),
                    'other_fees': money(raw_dict.get('other transaction fees')),
                    'other_amount': money(raw_dict.get('other')),
                    'total': total_paise,
                    'status': text(raw_dict.get('Transaction Status', 'Released')),
                    'release_date': release_date
                }
                table = 'transactions'
                # Uniquely identify the transaction row.
                # For order-level transactions, settlement_id changes when Amazon moves a transaction from 'Deferred'
                # to 'Released'. Exclude settlement_id for order transactions so the released payout updates the deferred record
                # instead of duplicating it.
                if order_id:
                    fp = digest(['transactions', t_type, order_id, sku or '', description or '', post_date or '', total_paise or 0])
                else:
                    fp = digest(['transactions', settlement_id or '', t_type or '', sku or '', description or '', post_date or '', total_paise or 0])

            elif kind == 'returns':
                order_id = text(raw_dict.get('Order ID', raw_dict.get('order id')))
                item_id = text(raw_dict.get('Order Item ID', raw_dict.get('order-item-id', '')))
                ret_date = day(raw_dict.get('Return request date', raw_dict.get('Order date', date.today().isoformat())))
                ret_type = text(raw_dict.get('Return type', 'Return'))
                sku = text(raw_dict.get('Merchant SKU', raw_dict.get('ASIN', '')))
                if not order_id:
                    continue

                del_raw = raw_dict.get('Return delivery date')
                delivery_date = day(del_raw) if del_raw and text(del_raw) else None

                record = {
                    'order_id': order_id,
                    'order_item_id': item_id,
                    'return_date': ret_date,
                    'return_status': text(raw_dict.get('Return request status')),
                    'return_type': ret_type,
                    'return_reason': text(raw_dict.get('Return reason')),
                    'tracking_id': text(raw_dict.get('Tracking ID')),
                    'sku': sku,
                    'return_qty': int(Decimal(text(raw_dict.get('Return quantity', '1')) or '1')),
                    'resolution': text(raw_dict.get('Resolution')),
                    'refund_amount': money(raw_dict.get('Refunded Amount')),
                    'delivery_date': delivery_date
                }
                table = 'returns'
                fp = digest(['returns', order_id, item_id, ret_date, ret_type])

            elif kind == 'ads_product':
                sku = text(raw_dict.get('Advertised product SKU'))
                date_range = text(raw_dict.get('Date range'))
                if not sku or not date_range:
                    continue
                start_date, end_date = parse_date_range(date_range)
                cost = money(raw_dict.get('Total cost'))
                sales = money(raw_dict.get('Sales'))
                impressions = int(Decimal(text(raw_dict.get('Impressions') or '0')))
                clicks = int(Decimal(text(raw_dict.get('Clicks') or '0')))
                units = int(Decimal(text(raw_dict.get('Units sold') or '0')))
                roas_val = float(text(raw_dict.get('ROAS') or '0').replace(',', '') or '0.0')

                record = {
                    'campaign_id': text(raw_dict.get('Campaign ID')).replace('="', '').replace('"', ''),
                    'campaign_name': text(raw_dict.get('Campaign name')),
                    'ad_group_id': text(raw_dict.get('Ad group ID')).replace('="', '').replace('"', ''),
                    'ad_group_name': text(raw_dict.get('Ad group name')),
                    'sku': sku,
                    'date_range': date_range,
                    'start_date': start_date,
                    'end_date': end_date,
                    'impressions': impressions,
                    'clicks': clicks,
                    'cost': cost,
                    'sales': sales,
                    'units': units,
                    'roas': roas_val
                }
                table = 'ads_product'
                fp = digest(['ads_product', record['campaign_id'], record['ad_group_id'], sku_key(sku), start_date, end_date])

            elif kind == 'ads_campaign':
                camp_id = text(raw_dict.get('Campaign ID')).replace('="', '').replace('"', '')
                camp_name = text(raw_dict.get('Campaign name'))
                date_range = text(raw_dict.get('Date range'))
                if not camp_name or not date_range:
                    continue
                start_date, end_date = parse_date_range(date_range)
                cost = money(raw_dict.get('Total cost'))
                sales = money(raw_dict.get('Sales'))
                impressions = int(Decimal(text(raw_dict.get('Impressions') or '0')))
                clicks = int(Decimal(text(raw_dict.get('Clicks') or '0')))
                roas_val = float(text(raw_dict.get('ROAS') or '0').replace(',', '') or '0.0')

                record = {
                    'campaign_id': camp_id,
                    'campaign_name': camp_name,
                    'date_range': date_range,
                    'start_date': start_date,
                    'end_date': end_date,
                    'impressions': impressions,
                    'clicks': clicks,
                    'cost': cost,
                    'sales': sales,
                    'roas': roas_val
                }
                table = 'ads_campaign'
                fp = digest(['ads_campaign', camp_id, start_date, end_date])

            elif kind == 'ads_search_term':
                st = text(raw_dict.get('Search term'))
                date_range = text(raw_dict.get('Date range'))
                if not st or not date_range:
                    continue
                start_date, end_date = parse_date_range(date_range)
                camp_id = text(raw_dict.get('Campaign ID')).replace('="', '').replace('"', '')
                ad_group_id = text(raw_dict.get('Ad group ID')).replace('="', '').replace('"', '')
                cost = money(raw_dict.get('Total cost'))
                sales = money(raw_dict.get('Sales'))
                impressions = int(Decimal(text(raw_dict.get('Impressions') or '0')))
                clicks = int(Decimal(text(raw_dict.get('Clicks') or '0')))
                units = int(Decimal(text(raw_dict.get('Units sold') or '0')))
                roas_val = float(text(raw_dict.get('ROAS') or '0').replace(',', '') or '0.0')

                record = {
                    'campaign_id': camp_id,
                    'campaign_name': text(raw_dict.get('Campaign name')),
                    'ad_group_id': ad_group_id,
                    'ad_group_name': text(raw_dict.get('Ad group name')),
                    'search_term': st,
                    'date_range': date_range,
                    'start_date': start_date,
                    'end_date': end_date,
                    'impressions': impressions,
                    'clicks': clicks,
                    'cost': cost,
                    'sales': sales,
                    'units': units,
                    'roas': roas_val
                }
                table = 'ads_search_term'
                fp = digest(['ads_search_term', camp_id, ad_group_id, st.casefold(), start_date, end_date])

            if table and fp and record:
                parsed['records'].append((table, fp, record, json.dumps(raw_dict, default=str)))

        except (ValueError, InvalidOperation, KeyError) as exc:
            parsed['warnings'].append(f'Row {row_idx}: {exc}')
            continue

    parsed['counts'] = dict(Counter(r[0] for r in parsed['records']))
    return parsed


def check_existing_record(db, table, fp, record):
    if table == 'orders':
        return db.execute('SELECT order_item_id FROM orders WHERE order_item_id=?', (record['order_item_id'],)).fetchone()
    if table == 'transactions':
        return db.execute('SELECT fingerprint FROM transactions WHERE fingerprint=?', (fp,)).fetchone()
    if table == 'returns':
        return db.execute(
            'SELECT id FROM returns WHERE order_id=? AND order_item_id=? AND return_date=? AND return_type=?',
            (record['order_id'], record['order_item_id'], record['return_date'], record['return_type'])
        ).fetchone()
    if table == 'ads_product':
        return db.execute('SELECT fingerprint FROM ads_product WHERE fingerprint=?', (fp,)).fetchone()
    if table == 'ads_campaign':
        return db.execute('SELECT fingerprint FROM ads_campaign WHERE fingerprint=?', (fp,)).fetchone()
    if table == 'ads_search_term':
        return db.execute('SELECT fingerprint FROM ads_search_term WHERE fingerprint=?', (fp,)).fetchone()
    if table == 'product_versions':
        return db.execute('SELECT id FROM product_versions WHERE sku=? AND effective_date=?', (record['sku'], record['effective_date'])).fetchone()
    return None


def preview(db, parsed):
    existing_file = db.execute('SELECT id FROM imports WHERE digest=?', (parsed['digest'],)).fetchone()
    overlapping = 0
    new_count = 0
    for table, fp, record, raw in parsed['records']:
        if check_existing_record(db, table, fp, record):
            overlapping += 1
        else:
            new_count += 1

    return {
        'name': parsed['name'],
        'kind': parsed['kind'],
        'counts': parsed['counts'],
        'total_records': len(parsed['records']),
        'already_imported': bool(existing_file),
        'overlapping_records': overlapping,
        'new_records': new_count,
        'warnings': parsed['warnings'][:20]
    }


def commit_file(db, parsed):
    p_info = preview(db, parsed)
    existing_file = db.execute('SELECT id FROM imports WHERE digest=?', (parsed['digest'],)).fetchone()
    if existing_file:
        import_id = existing_file['id']
    else:
        cur = db.execute(
            'INSERT INTO imports(name, digest, kind, payload, summary) VALUES (?,?,?,?,?)',
            (parsed['name'], parsed['digest'], parsed['kind'], parsed['payload'], json.dumps(p_info))
        )
        import_id = cur.lastrowid
        db.executemany(
            'INSERT INTO raw_rows(import_id, sheet, row_number, payload) VALUES (?,?,?,?)',
            [(import_id, *r) for r in parsed['rows']]
        )

    added = 0
    updated = 0

    for table, fp, record, raw in parsed['records']:
        old = check_existing_record(db, table, fp, record)
        if old:
            updated += 1
        else:
            added += 1

        # Register SKU in products catalogue
        if record.get('sku'):
            raw_sku = record['sku']
            k = sku_key(raw_sku)
            db.execute('INSERT OR IGNORE INTO products(sku, display_sku) VALUES (?,?)', (k, raw_sku))

        if table == 'orders':
            data = dict(record, import_id=import_id, raw=raw)
            keys = list(data.keys())
            placeholders = ','.join('?' for _ in keys)
            updates = ','.join(f'{k}=excluded.{k}' for k in keys if k != 'order_item_id')
            db.execute(
                f'INSERT INTO orders({",".join(keys)}) VALUES ({placeholders}) ON CONFLICT(order_item_id) DO UPDATE SET {updates}',
                tuple(data.values())
            )

        elif table == 'transactions':
            data = dict(record, fingerprint=fp, import_id=import_id, raw=raw)
            keys = list(data.keys())
            placeholders = ','.join('?' for _ in keys)
            update_clauses = []
            for k in keys:
                if k == 'fingerprint':
                    continue
                # Never downgrade an already 'Released' transaction back to 'Deferred' if an older report is re-imported
                if k in ('status', 'settlement_id', 'release_date'):
                    update_clauses.append(f'{k}=CASE WHEN transactions.status="Released" THEN transactions.{k} ELSE excluded.{k} END')
                else:
                    update_clauses.append(f'{k}=excluded.{k}')
            updates = ','.join(update_clauses)
            db.execute(
                f'INSERT INTO transactions({",".join(keys)}) VALUES ({placeholders}) ON CONFLICT(fingerprint) DO UPDATE SET {updates}',
                tuple(data.values())
            )

        elif table == 'returns':
            data = dict(record, import_id=import_id, raw=raw)
            keys = list(data.keys())
            placeholders = ','.join('?' for _ in keys)
            updates = ','.join(f'{k}=excluded.{k}' for k in keys if k not in ('order_id', 'order_item_id', 'return_date', 'return_type'))
            db.execute(
                f'INSERT INTO returns({",".join(keys)}) VALUES ({placeholders}) ON CONFLICT(order_id, order_item_id, return_date, return_type) DO UPDATE SET {updates}',
                tuple(data.values())
            )

        elif table == 'ads_product':
            # Remove narrower date-range records for the same campaign & sku if this report supersedes them
            db.execute(
                'DELETE FROM ads_product WHERE campaign_id=? AND sku=? AND start_date >= ? AND end_date <= ? AND fingerprint != ?',
                (record['campaign_id'], record['sku'], record['start_date'], record['end_date'], fp)
            )
            data = dict(record, fingerprint=fp, import_id=import_id, raw=raw)
            keys = list(data.keys())
            placeholders = ','.join('?' for _ in keys)
            updates = ','.join(f'{k}=excluded.{k}' for k in keys if k != 'fingerprint')
            db.execute(
                f'INSERT INTO ads_product({",".join(keys)}) VALUES ({placeholders}) ON CONFLICT(fingerprint) DO UPDATE SET {updates}',
                tuple(data.values())
            )

        elif table == 'ads_campaign':
            # Remove narrower date-range records for the same campaign if this report supersedes them
            db.execute(
                'DELETE FROM ads_campaign WHERE campaign_id=? AND start_date >= ? AND end_date <= ? AND fingerprint != ?',
                (record['campaign_id'], record['start_date'], record['end_date'], fp)
            )
            data = dict(record, fingerprint=fp, import_id=import_id, raw=raw)
            keys = list(data.keys())
            placeholders = ','.join('?' for _ in keys)
            updates = ','.join(f'{k}=excluded.{k}' for k in keys if k != 'fingerprint')
            db.execute(
                f'INSERT INTO ads_campaign({",".join(keys)}) VALUES ({placeholders}) ON CONFLICT(fingerprint) DO UPDATE SET {updates}',
                tuple(data.values())
            )

        elif table == 'ads_search_term':
            # Remove narrower date-range records for the same search term if this report supersedes them
            db.execute(
                'DELETE FROM ads_search_term WHERE campaign_id=? AND search_term=? AND start_date >= ? AND end_date <= ? AND fingerprint != ?',
                (record['campaign_id'], record['search_term'], record['start_date'], record['end_date'], fp)
            )
            data = dict(record, fingerprint=fp, import_id=import_id, raw=raw)
            keys = list(data.keys())
            placeholders = ','.join('?' for _ in keys)
            updates = ','.join(f'{k}=excluded.{k}' for k in keys if k != 'fingerprint')
            db.execute(
                f'INSERT INTO ads_search_term({",".join(keys)}) VALUES ({placeholders}) ON CONFLICT(fingerprint) DO UPDATE SET {updates}',
                tuple(data.values())
            )

        elif table == 'product_versions':
            old_pack = db.execute(
                'SELECT packaging FROM product_versions WHERE sku=? AND effective_date<=? ORDER BY effective_date DESC, id DESC LIMIT 1',
                (record['sku'], record['effective_date'])
            ).fetchone()
            pack_val = old_pack['packaging'] if old_pack else None
            db.execute(
                'INSERT INTO product_versions(sku, effective_date, cost, packaging, source) VALUES (?,?,?,?,?)',
                (record['sku'], record['effective_date'], record['cost'], pack_val, f'Import {import_id}')
            )

    p_info.update(added_records=added, updated_records=updated)
    db.execute('INSERT INTO import_runs(import_id, summary) VALUES (?,?)', (import_id, json.dumps(p_info)))
    return p_info


def scan_local_folder(folder_path):
    """Scan a local folder (e.g. amazon/input/) for candidate Amazon files."""
    p = Path(folder_path)
    if not p.exists() or not p.is_dir():
        return []
    matches = []
    patterns = ('*.csv', '*.tsv', '*.txt', '*.xlsx')
    for pattern in patterns:
        for file in p.glob(pattern):
            name_lower = file.name.lower()
            if 'input' in p.parts:
                matches.append(file)
            elif any(term in name_lower for term in ('unified', 'advertised', 'campaign', 'search_term', 'search term', 'report-', 'order', 'custom', 'amazon')) or re.match(r'^\d{15,20}\.txt$', file.name):
                matches.append(file)
    return sorted(matches, key=lambda f: f.stat().st_mtime, reverse=True)
