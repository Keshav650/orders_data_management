"""Single financial reconciliation calculation engine for Amazon Ledger."""
import json
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from .importer import day, money
from .costing import sku_key, purchase_cost, packaging_cost


def rounded(value):
    return int(Decimal(value).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def get_settings(db):
    return dict(db.execute('SELECT key, value FROM settings').fetchall())


def save_settings(db, values):
    rate = Decimal(str(values.get('gst_rate', '18')))
    if not rate.is_finite() or rate < 0 or rate > 100:
        raise ValueError('GST rate must be between 0 and 100 percent')
    include_gst = 'true' if values.get('include_gst', True) in (True, 'true', '1') else 'false'
    cutoff = int(values.get('cutoff_days', 14))
    db.executemany(
        'INSERT INTO settings(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
        [('gst_rate', str(rate)), ('include_gst', include_gst), ('cutoff_days', str(cutoff))]
    )


def save_products(db, updates, effective_date):
    effective_date = day(effective_date)
    if not updates:
        raise ValueError('No products specified')
    for u in updates:
        raw_sku = u.get('sku', '')
        key = sku_key(raw_sku)
        if not db.execute('SELECT 1 FROM products WHERE sku=?', (key,)).fetchone():
            db.execute('INSERT INTO products(sku, display_sku) VALUES (?,?)', (key, raw_sku))
        old = db.execute(
            "SELECT * FROM product_versions WHERE sku=? AND effective_date<=? AND source!='Packaging update' ORDER BY effective_date DESC, id DESC LIMIT 1",
            (key, effective_date)
        ).fetchone()

        c_val = money(u['cost'], optional=True) if 'cost' in u else (old['cost'] if old else None)
        p_val = money(u['packaging'], optional=True) if 'packaging' in u else (old['packaging'] if old else None)

        if c_val is not None and c_val < 0:
            raise ValueError('Product cost cannot be negative')
        if p_val is not None and p_val < 0:
            raise ValueError('Packaging cost cannot be negative')

        if 'packaging' not in u:
            p_val = packaging_cost(list(db.execute('SELECT * FROM product_versions WHERE sku=?', (key,))))

        db.execute(
            'INSERT INTO product_versions(sku, effective_date, cost, packaging, source) VALUES (?,?,?,?,?)',
            (key, effective_date, c_val, p_val, 'Manual update' if 'cost' in u else 'Packaging update')
        )


def create_product(db, values):
    display = str(values.get('sku', '')).strip()
    if not display:
        raise ValueError('Enter a valid SKU name')
    key = sku_key(display)
    if db.execute('SELECT 1 FROM products WHERE sku=?', (key,)).fetchone():
        raise ValueError('This SKU already exists.')
    cost_val = money(values.get('cost'), optional=True)
    if cost_val is None:
        raise ValueError('Enter a valid product cost (or 0)')
    db.execute('INSERT INTO products(sku, display_sku) VALUES (?,?)', (key, display))
    save_products(db, [{'sku': display, 'cost': values['cost'], 'packaging': values.get('packaging', '')}], values.get('effective_date', '0001-01-01'))


def set_product_disabled(db, sku, disabled):
    key = sku_key(sku)
    db.execute(
        'INSERT INTO product_preferences(sku, disabled) VALUES (?,?) ON CONFLICT(sku) DO UPDATE SET disabled=excluded.disabled',
        (key, 1 if disabled else 0)
    )


def save_recovery(db, values):
    state = values.get('state')
    if state not in ('awaiting', 'sellable', 'damaged', 'lost'):
        raise ValueError('Unknown recovery state')
    order_id = str(values.get('order_id', ''))
    item_id = str(values.get('order_item_id', ''))
    eff_date = day(values.get('effective_date', date.today().isoformat()))
    note = str(values.get('note', ''))[:1000]
    db.execute(
        'INSERT INTO recovery_events(order_id, order_item_id, effective_date, state, note) VALUES (?,?,?,?,?)',
        (order_id, item_id, eff_date, state, note)
    )


def report(db, start=None, end=None, as_of=None, mode='mature', sku=None, skus=None):
    selected_skus = set(sku_key(s) for s in (skus or ([sku] if sku else [])) if s)
    settings = get_settings(db)
    cutoff_days = int(settings.get('cutoff_days', '14'))
    
    as_of = day(as_of or date.today().isoformat())
    cutoff = (date.fromisoformat(as_of) - timedelta(days=cutoff_days)).isoformat()
    
    earliest_order, latest_order = db.execute('SELECT MIN(purchase_date), MAX(purchase_date) FROM orders').fetchone()
    
    if mode == 'mature':
        end = min(cutoff, latest_order or cutoff)
    else:
        end = day(end or as_of)
    start = day(start) if start else (date.fromisoformat(end) - timedelta(days=29)).isoformat()
    
    if start > end and mode != 'mature':
        raise ValueError('Start date cannot be after end date')

    include_gst = settings.get('include_gst', 'true') == 'true'
    gst_factor = Decimal(1) + (Decimal(settings.get('gst_rate', '18')) / Decimal(100))

    # Fetch orders in period
    order_query = 'SELECT * FROM orders WHERE purchase_date BETWEEN ? AND ? ORDER BY purchase_date DESC, amazon_order_id'
    order_rows = [dict(r) for r in db.execute(order_query, (start, end))]

    # Group transactions by order_id
    trans_query = 'SELECT * FROM transactions WHERE post_date <= ? ORDER BY post_date, fingerprint'
    all_trans = [dict(r) for r in db.execute(trans_query, (as_of,))]
    order_trans = defaultdict(list)
    unmatched_trans = []
    service_fees = []
    disbursements = []

    order_ids = {o['amazon_order_id'] for o in order_rows}
    all_db_order_ids = {r[0] for r in db.execute('SELECT DISTINCT amazon_order_id FROM orders').fetchall()}

    for t in all_trans:
        oid = t['order_id']
        t_type = t['type']
        if t_type == 'Transfer':
            disbursements.append(t)
        elif t_type in ('Service Fee', 'FBA Inventory Fee'):
            if start <= t['post_date'] <= as_of:
                service_fees.append(t)
        elif oid:
            order_trans[oid].append(t)
            if oid not in all_db_order_ids:
                unmatched_trans.append(t)

    # Returns lookup
    returns_rows = [dict(r) for r in db.execute('SELECT * FROM returns ORDER BY return_date')]
    order_returns = defaultdict(list)
    for r in returns_rows:
        order_returns[r['order_id']].append(r)

    # Recoveries lookup
    recovery_rows = [dict(r) for r in db.execute('SELECT * FROM recovery_events WHERE effective_date<=? ORDER BY effective_date, id', (as_of,))]
    order_recoveries = {}
    for r in recovery_rows:
        order_recoveries[r['order_id']] = r

    # Cost versions lookup
    versions = defaultdict(list)
    for r in db.execute('SELECT * FROM product_versions ORDER BY effective_date, id'):
        versions[r['sku']].append(dict(r))

    # SKU options & preferences
    sku_options = {}
    for r in db.execute('SELECT sku, display_sku FROM products'):
        sku_options[r['sku']] = r['display_sku']
    for o in order_rows:
        k = sku_key(o['sku'])
        if k not in sku_options:
            sku_options[k] = o['sku']

    # Aggregators
    orders_list = []
    issues = []
    totals = Counter()
    timeline = defaultdict(lambda: Counter())
    sku_totals = defaultdict(lambda: Counter())

    # Group orders by amazon_order_id to handle multi-item orders accurately
    grouped_orders = defaultdict(list)
    for o in order_rows:
        grouped_orders[o['amazon_order_id']].append(o)

    for oid, items in grouped_orders.items():
        if selected_skus and not any(sku_key(it['sku']) in selected_skus for it in items):
            continue

        trans_list = order_trans.get(oid, [])
        ret_list = order_returns.get(oid, [])
        rec_data = order_recoveries.get(oid, {})

        # Status resolution across items
        statuses = [(it['order_status'] or '').upper() for it in items]
        raw_status = ', '.join(dict.fromkeys(it['order_status'] or '' for it in items))
        has_refund = any(t['type'] == 'Refund' for t in trans_list)
        is_cancelled = all('CANCEL' in s for s in statuses)

        if is_cancelled:
            final_status = 'CANCELLED'
        elif any(r['return_type'] in ('Undelivered', 'Rejected') for r in ret_list) or any('RTO' in s or 'RETURNED TO SELLER' in s for s in statuses):
            final_status = 'RTO'
        elif any(r['return_type'] in ('C-Returns', 'Amazon CS') for r in ret_list) or has_refund or any('RETURN' in s for s in statuses):
            final_status = 'RETURN'
        else:
            final_status = items[0]['order_status']

        row_issues = []
        if is_cancelled:
            if trans_list and any(t['total'] != 0 for t in trans_list):
                row_issues.append('Cancelled order has financial settlement records')

        if final_status == 'RTO':
            easy_ship_net = sum(t['total'] for t in trans_list if t['type'] == 'Shipping Services')
            if easy_ship_net < 0:
                row_issues.append('Easy Ship fee was charged but not reversed on RTO')

        default_rec = 'sellable'
        rec_state = rec_data.get('state', default_rec) if final_status in ('RTO', 'RETURN') else None

        # Packaging cost: charged once per order shipment
        if is_cancelled:
            order_packaging = 0
            order_packaging_val = 0
        elif any(packaging_cost(versions[sku_key(it['sku'])]) is None for it in items):
            order_packaging = None
            order_packaging_val = 0
            row_issues.append('Missing packaging cost')
        else:
            order_packaging = max((packaging_cost(versions[sku_key(it['sku'])]) or 0) for it in items)
            order_packaging_val = order_packaging

        # Product purchase costs for each item
        item_costs = []
        for it in items:
            key_sku = sku_key(it['sku'])
            u_cost, c_source, initial_cost = purchase_cost(versions, key_sku, it['purchase_date'])
            if is_cancelled or (rec_state == 'sellable' and final_status in ('RTO', 'RETURN')):
                it_pc = 0
            elif u_cost is None:
                it_pc = None
            else:
                cost_val = Decimal(u_cost * it['quantity'])
                if not include_gst:
                    cost_val = cost_val / gst_factor
                it_pc = rounded(cost_val)
            item_costs.append(it_pc)

        if any(c is None for c in item_costs):
            order_product_cost = None
            order_product_cost_val = sum(c or 0 for c in item_costs)
            row_issues.append('Missing purchase cost for order date')
        else:
            order_product_cost = sum(item_costs)
            order_product_cost_val = order_product_cost

        # Total listed value and weights for allocating shared charges
        total_order_listed = sum((it['item_price'] or 0) * it['quantity'] for it in items)
        n_items = len(items)
        if total_order_listed > 0:
            weights = [Decimal((it['item_price'] or 0) * it['quantity']) / Decimal(total_order_listed) for it in items]
        else:
            weights = [Decimal(1) / Decimal(n_items) for _ in items]

        # Allocate packaging across items for SKU performance
        it_packaging_shares = []
        if n_items == 1:
            it_packaging_shares = [order_packaging_val]
        else:
            allocated_so_far = 0
            for i in range(n_items - 1):
                share = rounded(Decimal(order_packaging_val) * weights[i])
                it_packaging_shares.append(share)
                allocated_so_far += share
            it_packaging_shares.append(order_packaging_val - allocated_so_far)

        # Full order financial totals directly from trans_list
        settlement_paise = sum(t['total'] for t in trans_list)
        product_sales = sum(t['product_sales'] for t in trans_list)
        shipping_credits = sum(t['shipping_credits'] for t in trans_list)
        easy_ship_fees = sum(t['other_fees'] for t in trans_list if t['type'] == 'Shipping Services' or 'easy ship' in (t['description'] or '').lower())
        selling_fees = sum(t['selling_fees'] for t in trans_list)
        fba_fees = sum(t['fba_fees'] for t in trans_list)
        refunds_total = sum(t['total'] for t in trans_list if t['type'] == 'Refund')
        safet_claims = sum(t['total'] for t in trans_list if t['type'] == 'SAFE-T Reimbursement')
        taxes = sum(t['tax_liable'] for t in trans_list)
        tcs_tds = sum(t['tcs_cgst'] + t['tcs_sgst'] + t['tcs_igst'] + t['tds'] for t in trans_list)

        # Item-level breakdown
        if n_items == 1:
            it_trans_totals = [settlement_paise]
            it_sales = [product_sales]
            it_shipping_credits = [shipping_credits]
            it_easy_ship = [easy_ship_fees]
            it_selling_fees = [selling_fees]
            it_fba_fees = [fba_fees]
            it_refunds = [refunds_total]
            it_safet = [safet_claims]
            it_taxes = [taxes]
            it_tcs_tds = [tcs_tds]
            it_contributions = [0 if is_cancelled else (settlement_paise - order_product_cost_val - order_packaging_val)]
        else:
            item_sku_map = defaultdict(list)
            for idx, it in enumerate(items):
                item_sku_map[sku_key(it['sku'])].append(idx)

            item_trans = defaultdict(list)
            shared_trans = []
            for t in trans_list:
                t_sku = sku_key(t['sku']) if t['sku'] else ''
                matching_indices = item_sku_map.get(t_sku, [])
                if len(matching_indices) == 1:
                    item_trans[matching_indices[0]].append(t)
                else:
                    shared_trans.append(t)

            shared_fields = ['total', 'product_sales', 'shipping_credits', 'selling_fees', 'fba_fees', 'tax_liable', 'other_fees']
            item_shared = [defaultdict(int) for _ in items]
            for t in shared_trans:
                is_easy_ship = (t['type'] == 'Shipping Services' or 'easy ship' in (t['description'] or '').lower())
                tcs_tds_val = t['tcs_cgst'] + t['tcs_sgst'] + t['tcs_igst'] + t['tds']
                is_refund = (t['type'] == 'Refund')
                is_safet = (t['type'] == 'SAFE-T Reimbursement')

                for field in shared_fields:
                    val = t[field]
                    so_far = 0
                    for i in range(n_items - 1):
                        part = rounded(Decimal(val) * weights[i])
                        item_shared[i][field] += part
                        so_far += part
                    item_shared[n_items - 1][field] += (val - so_far)

                for name, val in [('easy_ship_fees', t['other_fees'] if is_easy_ship else 0),
                                  ('tcs_tds', tcs_tds_val),
                                  ('refunds', t['total'] if is_refund else 0),
                                  ('safet', t['total'] if is_safet else 0)]:
                    so_far = 0
                    for i in range(n_items - 1):
                        part = rounded(Decimal(val) * weights[i])
                        item_shared[i][name] += part
                        so_far += part
                    item_shared[n_items - 1][name] += (val - so_far)

            it_trans_totals = []
            it_sales = []
            it_shipping_credits = []
            it_easy_ship = []
            it_selling_fees = []
            it_fba_fees = []
            it_refunds = []
            it_safet = []
            it_taxes = []
            it_tcs_tds = []
            it_contributions = []

            for i, it in enumerate(items):
                direct = item_trans[i]
                tot = sum(t['total'] for t in direct) + item_shared[i]['total']
                sales = sum(t['product_sales'] for t in direct) + item_shared[i]['product_sales']
                ship_cred = sum(t['shipping_credits'] for t in direct) + item_shared[i]['shipping_credits']
                es_fees = sum(t['other_fees'] for t in direct if t['type'] == 'Shipping Services' or 'easy ship' in (t['description'] or '').lower()) + item_shared[i]['easy_ship_fees']
                sell_fees = sum(t['selling_fees'] for t in direct) + item_shared[i]['selling_fees']
                fba = sum(t['fba_fees'] for t in direct) + item_shared[i]['fba_fees']
                ref = sum(t['total'] for t in direct if t['type'] == 'Refund') + item_shared[i]['refunds']
                saf = sum(t['total'] for t in direct if t['type'] == 'SAFE-T Reimbursement') + item_shared[i]['safet']
                tax = sum(t['tax_liable'] for t in direct) + item_shared[i]['tax_liable']
                tt = sum(t['tcs_cgst'] + t['tcs_sgst'] + t['tcs_igst'] + t['tds'] for t in direct) + item_shared[i]['tcs_tds']
                contr = 0 if is_cancelled else (tot - (item_costs[i] or 0) - it_packaging_shares[i])

                it_trans_totals.append(tot)
                it_sales.append(sales)
                it_shipping_credits.append(ship_cred)
                it_easy_ship.append(es_fees)
                it_selling_fees.append(sell_fees)
                it_fba_fees.append(fba)
                it_refunds.append(ref)
                it_safet.append(saf)
                it_taxes.append(tax)
                it_tcs_tds.append(tt)
                it_contributions.append(contr)

        # Update SKU totals (for Product Performance tab)
        for i, it in enumerate(items):
            k = sku_key(it['sku'])
            s_acc = sku_totals[k]
            s_acc['orders'] += 1
            s_acc['units'] += it['quantity']
            s_acc['settlement'] += it_trans_totals[i]
            s_acc['product_cost'] += item_costs[i] or 0
            s_acc['packaging'] += it_packaging_shares[i]
            s_acc['contribution'] += it_contributions[i]
            s_acc['returns'] += (final_status == 'RETURN')
            s_acc['rto'] += (final_status == 'RTO')
            s_acc['awaiting'] += (not trans_list)
            s_acc['provisional'] += bool(item_costs[i] is None or order_packaging is None or not trans_list)

        due_date = (date.fromisoformat(items[0]['purchase_date']) + timedelta(days=cutoff_days)).isoformat()
        if trans_list:
            payment_state = 'Settlement recorded' if settlement_paise > 0 else ('Zero settlement' if settlement_paise == 0 else 'Negative settlement')
        elif is_cancelled:
            payment_state = 'Cancelled / Excluded'
        elif due_date <= as_of:
            payment_state = 'Awaiting payment (overdue)'
        else:
            payment_state = 'Within settlement window'

        # Filtered vs Unfiltered view of order
        if selected_skus:
            active_indices = [i for i, it in enumerate(items) if sku_key(it['sku']) in selected_skus]
            order_row_units = sum(items[i]['quantity'] for i in active_indices)
            order_row_listed = sum((items[i]['item_price'] or 0) * items[i]['quantity'] for i in active_indices)
            order_row_settlement = sum(it_trans_totals[i] for i in active_indices)
            order_row_product_cost = sum(item_costs[i] or 0 for i in active_indices)
            order_row_packaging = sum(it_packaging_shares[i] for i in active_indices)
            order_row_contribution = sum(it_contributions[i] for i in active_indices)
            order_row_skus = ', '.join(items[i]['sku'] for i in active_indices)
            order_row_item_ids = ', '.join(items[i]['order_item_id'] for i in active_indices)
            order_costs_missing = any(item_costs[i] is None for i in active_indices) or (order_packaging is None)
        else:
            active_indices = list(range(n_items))
            order_row_units = sum(it['quantity'] for it in items)
            order_row_listed = total_order_listed
            order_row_settlement = settlement_paise
            order_row_product_cost = order_product_cost
            order_row_packaging = order_packaging
            order_row_contribution = 0 if is_cancelled else (settlement_paise - order_product_cost_val - order_packaging_val)
            order_row_skus = ', '.join(dict.fromkeys(it['sku'] for it in items))
            order_row_item_ids = ', '.join(dict.fromkeys(it['order_item_id'] for it in items))
            order_costs_missing = (order_product_cost is None) or (order_packaging is None)

        order_record = {
            'amazon_order_id': oid,
            'order_item_id': order_row_item_ids,
            'sku': order_row_skus,
            'sku_key': ', '.join(dict.fromkeys(sku_key(items[i]['sku']) for i in active_indices)),
            'quantity': order_row_units,
            'purchase_date': items[0]['purchase_date'],
            'due_date': due_date,
            'status': final_status,
            'raw_status': raw_status,
            'payment_state': payment_state,
            'payment_count': len(trans_list),
            'latest_trans_date': max((t['post_date'] for t in trans_list), default=None),
            'settlement': order_row_settlement,
            'listed_value': order_row_listed,
            'product_sales': product_sales if not selected_skus else sum(it_sales[i] for i in active_indices),
            'shipping_credits': shipping_credits if not selected_skus else sum(it_shipping_credits[i] for i in active_indices),
            'easy_ship_fees': easy_ship_fees if not selected_skus else sum(it_easy_ship[i] for i in active_indices),
            'selling_fees': selling_fees if not selected_skus else sum(it_selling_fees[i] for i in active_indices),
            'fba_fees': fba_fees,
            'refunds': refunds_total,
            'safet': safet_claims,
            'taxes': taxes if not selected_skus else sum(it_taxes[i] for i in active_indices),
            'tcs_tds': tcs_tds if not selected_skus else sum(it_tcs_tds[i] for i in active_indices),
            'unit_cost': purchase_cost(versions, sku_key(items[0]['sku']), items[0]['purchase_date'])[0] if n_items == 1 else None,
            'product_cost': order_row_product_cost,
            'packaging': order_row_packaging,
            'contribution': order_row_contribution,
            'costs_missing': order_costs_missing,
            'initial_cost_applied': any(purchase_cost(versions, sku_key(it['sku']), it['purchase_date'])[2] for it in items),
            'recovery': rec_state,
            'recovery_note': rec_data.get('note', ''),
            'returns_info': ret_list,
            'ship_city': items[0].get('ship_city', ''),
            'ship_state': items[0].get('ship_state', ''),
            'issues': row_issues,
            'items_count': n_items,
            'items': [
                {
                    'order_item_id': it['order_item_id'],
                    'sku': it['sku'],
                    'sku_key': sku_key(it['sku']),
                    'product_name': it.get('product_name', ''),
                    'quantity': it['quantity'],
                    'item_price': it['item_price'],
                    'product_cost': item_costs[i],
                    'packaging': it_packaging_shares[i],
                    'settlement': it_trans_totals[i],
                    'contribution': it_contributions[i]
                }
                for i, it in enumerate(items)
            ]
        }
        orders_list.append(order_record)

        for iss in row_issues:
            issues.append({'order_id': oid, 'sku': order_row_skus, 'issue': iss})

        # Summary accumulation
        if is_cancelled:
            totals['cancelled'] += 1
        else:
            totals['orders'] += 1
            totals['units'] += order_row_units
            totals[final_status] += 1
            totals['settlement'] += order_row_settlement
            totals['product_sales'] += product_sales if not selected_skus else sum(it_sales[i] for i in active_indices)
            totals['shipping_credits'] += shipping_credits if not selected_skus else sum(it_shipping_credits[i] for i in active_indices)
            totals['easy_ship_fees'] += easy_ship_fees if not selected_skus else sum(it_easy_ship[i] for i in active_indices)
            totals['selling_fees'] += selling_fees if not selected_skus else sum(it_selling_fees[i] for i in active_indices)
            totals['fba_fees'] += fba_fees
            totals['refunds'] += refunds_total
            totals['safet'] += safet_claims
            totals['taxes'] += taxes if not selected_skus else sum(it_taxes[i] for i in active_indices)
            totals['tcs_tds'] += tcs_tds if not selected_skus else sum(it_tcs_tds[i] for i in active_indices)
            totals['product_cost'] += (order_row_product_cost or 0)
            totals['packaging'] += (order_row_packaging or 0)
            totals['listed_value'] += order_row_listed
            totals['missing_cost'] += bool(order_costs_missing and order_row_product_cost is None)
            totals['missing_packaging'] += bool(order_packaging is None)
            totals['awaiting'] += not trans_list
            totals['overdue'] += not trans_list and due_date <= as_of
            totals['recorded'] += bool(trans_list)

            # Timeline
            t_day = timeline[items[0]['purchase_date']]
            t_day['orders'] += 1
            t_day['settlement'] += order_row_settlement
            t_day['listed_value'] += order_row_listed

    # Advertising allocation
    ads_product_rows = [dict(r) for r in db.execute('SELECT * FROM ads_product WHERE start_date <= ? AND end_date >= ?', (end, start))]
    allocated_ads = []
    sku_ad_spend = defaultdict(int)

    for ad in ads_product_rows:
        lower = max(ad['start_date'], start)
        upper = min(ad['end_date'], end, as_of)
        if lower <= upper:
            total_days = max(1, (date.fromisoformat(ad['end_date']) - date.fromisoformat(ad['start_date'])).days + 1)
            overlap_days = max(1, (date.fromisoformat(upper) - date.fromisoformat(lower)).days + 1)
            alloc_cost = rounded(Decimal(ad['cost']) * overlap_days / total_days)
            ad['allocated_cost'] = alloc_cost
            allocated_ads.append(ad)
            sku_ad_spend[sku_key(ad['sku'])] += alloc_cost

    # If SKU filter active, ad spend is filtered to selected SKUs
    if selected_skus:
        total_ad_spend = sum(sku_ad_spend[k] for k in selected_skus)
    else:
        total_ad_spend = sum(a['allocated_cost'] for a in allocated_ads)
        # Fallback to campaign ads if no product ads exist
        if not total_ad_spend:
            camp_rows = [dict(r) for r in db.execute('SELECT * FROM ads_campaign WHERE start_date <= ? AND end_date >= ?', (end, start))]
            for c in camp_rows:
                lower = max(c['start_date'], start)
                upper = min(c['end_date'], end, as_of)
                if lower <= upper:
                    total_days = max(1, (date.fromisoformat(c['end_date']) - date.fromisoformat(c['start_date'])).days + 1)
                    overlap_days = max(1, (date.fromisoformat(upper) - date.fromisoformat(lower)).days + 1)
                    total_ad_spend += rounded(Decimal(c['cost']) * overlap_days / total_days)

    # All-time imported ad spend for coverage notice
    all_ads_cost = db.execute('SELECT SUM(cost) FROM ads_product').fetchone()[0] or 0
    if not all_ads_cost:
        all_ads_cost = db.execute('SELECT SUM(cost) FROM ads_campaign').fetchone()[0] or 0

    # Separate service fees into:
    # 1. Advertising Invoices: billed to Seller Central balance for Amazon Ads (description contains 'advertising')
    # 2. Operating Service Fees: non-advertising account overhead (Subscription, FBA storage, removal, etc.)
    ad_invoice_fees = [t for t in service_fees if 'advertising' in (t['description'] or '').lower()]
    operating_service_fees = [t for t in service_fees if 'advertising' not in (t['description'] or '').lower()]

    total_ad_invoices = sum(t['total'] for t in ad_invoice_fees)
    total_operating_service_fees = sum(t['total'] for t in operating_service_fees)

    # If no advertising reports were ever imported, fallback to ad invoices from transactions so ad spend is not lost
    if not all_ads_cost and not selected_skus and not total_ad_spend:
        total_ad_spend = abs(total_ad_invoices)

    # When a SKU filter is active, account-level unassigned service fees do not apply to that single SKU.
    # For overall account P&L, only operating service fees (non-advertising) apply, preventing double-counting of ad spend.
    if selected_skus:
        applicable_service_fees = 0
    else:
        applicable_service_fees = total_operating_service_fees

    # Net Profit: Settlement - Product Cost - Packaging - Ad Spend + Operating Service Fees (operating fees are negative signed)
    net_profit = totals['settlement'] - totals['product_cost'] - totals['packaging'] - total_ad_spend + applicable_service_fees

    summary = {
        'orders': totals['orders'],
        'cancelled': totals['cancelled'],
        'units': totals['units'],
        'delivered': totals['DELIVERED'],
        'rto': totals['RTO'],
        'returns': totals['RETURN'],
        'shipped': totals['SHIPPED'],
        'settlement': totals['settlement'],
        'product_sales': totals['product_sales'],
        'shipping_credits': totals['shipping_credits'],
        'easy_ship_fees': totals['easy_ship_fees'],
        'selling_fees': totals['selling_fees'],
        'fba_fees': totals['fba_fees'],
        'refunds': totals['refunds'],
        'safet': totals['safet'],
        'taxes': totals['taxes'],
        'tcs_tds': totals['tcs_tds'],
        'product_cost': totals['product_cost'],
        'packaging': totals['packaging'],
        'ads': total_ad_spend,
        'total_imported_ads': all_ads_cost,
        'ads_outside_period': max(0, all_ads_cost - total_ad_spend),
        'service_fees': applicable_service_fees,
        'operating_service_fees': total_operating_service_fees,
        'ad_invoices': total_ad_invoices,
        'net_profit': net_profit,
        'missing_cost': totals['missing_cost'],
        'missing_packaging': totals['missing_packaging'],
        'awaiting': totals['awaiting'],
        'overdue': totals['overdue'],
        'recorded': totals['recorded'],
        'listed_value': totals['listed_value'],
        'provisional': bool(totals['missing_cost'] or totals['missing_packaging'] or totals['awaiting'] or len(issues) > 0),
        'customer_return_rate': round(100 * totals['RETURN'] / totals['orders'], 2) if totals['orders'] else 0.0,
        'rto_rate': round(100 * totals['RTO'] / totals['orders'], 2) if totals['orders'] else 0.0,
    }

    # Search terms wasted spend & winners
    wasted_search_terms = [
        dict(r) for r in db.execute(
            'SELECT search_term, campaign_name, clicks, cost, sales FROM ads_search_term WHERE cost > 0 AND sales = 0 ORDER BY cost DESC LIMIT 50'
        )
    ]
    top_search_terms = [
        dict(r) for r in db.execute(
            'SELECT search_term, campaign_name, clicks, cost, sales, roas FROM ads_search_term WHERE sales > 0 ORDER BY roas DESC LIMIT 50'
        )
    ]

    # SKU performance table
    sku_performance = []
    for k, v in sku_totals.items():
        ad_cost = sku_ad_spend.get(k, 0)
        profit_after_ads = v['contribution'] - ad_cost
        disp = sku_options.get(k, k)
        sku_performance.append({
            'sku': disp,
            'sku_key': k,
            'orders': v['orders'],
            'units': v['units'],
            'settlement': v['settlement'],
            'product_cost': v['product_cost'],
            'packaging': v['packaging'],
            'contribution': v['contribution'],
            'ad_spend': ad_cost,
            'profit_after_ads': profit_after_ads,
            'returns': v['returns'],
            'rto': v['rto'],
            'awaiting': v['awaiting'],
            'provisional': v['provisional']
        })
    sku_performance.sort(key=lambda x: x['profit_after_ads'], reverse=True)

    # Coverage metadata
    cov_first_p, cov_last_p = db.execute('SELECT MIN(post_date), MAX(post_date) FROM transactions').fetchone()
    cov_first_ad, cov_last_ad = db.execute('SELECT MIN(start_date), MAX(end_date) FROM ads_product').fetchone()
    if not cov_first_ad:
        cov_first_ad, cov_last_ad = db.execute('SELECT MIN(start_date), MAX(end_date) FROM ads_campaign').fetchone()
    last_imp = db.execute('SELECT MAX(applied_at) FROM import_runs').fetchone()

    coverage = {
        'first_order': earliest_order,
        'last_order': latest_order,
        'first_payment': cov_first_p,
        'last_payment': cov_last_p,
        'first_ad': cov_first_ad,
        'last_ad': cov_last_ad,
        'total_imported_ads': all_ads_cost,
        'last_import': last_imp[0] if last_imp else None
    }

    return {
        'filters': {
            'start': start,
            'end': end,
            'as_of': as_of,
            'mode': mode,
            'cutoff': cutoff,
            'sku': next(iter(selected_skus)) if len(selected_skus) == 1 else '',
            'skus': sorted(selected_skus)
        },
        'settings': settings,
        'sku_options': [{'value': k, 'label': v} for k, v in sorted(sku_options.items(), key=lambda kv: kv[1])],
        'summary': summary,
        'orders': orders_list,
        'issues': issues,
        'timeline': [{'date': k, **v} for k, v in sorted(timeline.items())],
        'sku_performance': sku_performance,
        'unmatched_transactions': unmatched_trans[:100],
        'service_fees': operating_service_fees,
        'ad_invoices': ad_invoice_fees,
        'disbursements': disbursements,
        'wasted_search_terms': wasted_search_terms,
        'top_search_terms': top_search_terms,
        'coverage': coverage
    }


def order_detail(db, order_id):
    order_rows = [dict(r) for r in db.execute('SELECT * FROM orders WHERE amazon_order_id=?', (order_id,))]
    trans_rows = [dict(r) for r in db.execute('SELECT * FROM transactions WHERE order_id=? ORDER BY post_date', (order_id,))]
    returns_rows = [dict(r) for r in db.execute('SELECT * FROM returns WHERE order_id=? ORDER BY return_date', (order_id,))]
    recoveries = [dict(r) for r in db.execute('SELECT * FROM recovery_events WHERE order_id=? ORDER BY effective_date, id', (order_id,))]
    return {
        'orders': order_rows,
        'transactions': trans_rows,
        'returns': returns_rows,
        'recoveries': recoveries
    }


def products_list(db, effective_date):
    effective_date = day(effective_date)
    result = []
    versions = defaultdict(list)
    for r in db.execute('SELECT * FROM product_versions ORDER BY effective_date, id'):
        versions[r['sku']].append(dict(r))

    for p in db.execute('SELECT p.*, COALESCE(pref.disabled, 0) disabled FROM products p LEFT JOIN product_preferences pref ON p.sku=pref.sku ORDER BY display_sku COLLATE NOCASE'):
        v = db.execute(
            "SELECT * FROM product_versions WHERE sku=? AND effective_date<=? AND source!='Packaging update' ORDER BY effective_date DESC, id DESC LIMIT 1",
            (p['sku'], effective_date)
        ).fetchone()
        cost, source, initial = purchase_cost(versions, p['sku'], effective_date)
        pack = packaging_cost(versions[p['sku']])
        result.append({
            'sku': p['sku'],
            'display_sku': p['display_sku'],
            'disabled': bool(p['disabled']),
            'cost': cost,
            'packaging': pack,
            'effective_date': v['effective_date'] if v else None,
            'initial_cost_applied': initial,
            'cost_source_sku': source
        })
    return result
