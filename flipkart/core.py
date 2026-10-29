"""Independent Flipkart ledger. Money is integer millionths of a rupee."""
import csv
import hashlib
import io
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

SCALE = 1_000_000

def today():
    return datetime.now(timezone(timedelta(hours=5, minutes=30))).date().isoformat()

def text(value):
    return '' if value is None else str(value).strip()

def clean(value, prefix=''):
    s = text(value).strip('"').strip()
    return s[len(prefix):].strip() if prefix and s.startswith(prefix) else s

def item_id(value):
    if isinstance(value, (float, int)):
        raise ValueError('Order item ID is numeric: export it as text to avoid lost digits')
    return clean(value, 'OI:').lstrip("'")

def sku_id(value):
    return clean(value, 'SKU:')

def money(value, blank=False):
    s = text(value).replace(',', '').replace('₹', '').replace('INR', '').strip()
    if not s and blank:
        return 0
    if s.startswith('(') and s.endswith(')'):
        s = '-' + s[1:-1]
    try:
        n = Decimal(s)
        if not n.is_finite():
            raise ValueError('Amount must be finite')
        return int((n * SCALE).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except (InvalidOperation, OverflowError):
        raise ValueError(f'Invalid amount: {value}') from None

def rupees(value):
    return None if value is None else value / SCALE

def day(value, optional=False):
    if not text(value) or text(value).upper() == 'NA':
        if optional:
            return ''
        raise ValueError('Date is missing')
    if isinstance(value, (date, datetime)):
        return value.strftime('%Y-%m-%d')
    s = text(value)
    try:
        return date.fromisoformat(s[:10]).isoformat()
    except ValueError:
        pass
    for fmt in ('%b %d, %Y', '%m/%d/%y', '%d/%m/%Y', '%d-%m-%Y', '%d-%b-%Y', '%d %b %Y'):
        try:
            return datetime.strptime(s if fmt in ('%d %b %Y','%b %d, %Y') else s.split(' ')[0], fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f'Invalid date: {s}; use YYYY-MM-DD or day/month/year')

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

def norm(value):
    return re.sub(r'[^a-z0-9]', '', text(value).lower())

def return_category(value):
    """Classify only an explicit financial return type, never guess from RETURNED."""
    label=norm(value)
    if label in ('logisticsreturn','rto','returntoorigin'):
        return 'RTO'
    if label in ('customerreturn','rvp','reversepickup'):
        return 'RVP'
    return 'Unclassified' if label and label!='na' else ''

def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS file_imports(id INTEGER PRIMARY KEY,name TEXT,digest TEXT UNIQUE,
      kind TEXT,imported_at TEXT DEFAULT CURRENT_TIMESTAMP,summary TEXT,payload BLOB);
    CREATE TABLE IF NOT EXISTS fk_orders(item_id TEXT PRIMARY KEY,order_id TEXT,sku TEXT,
      title TEXT,ordered_on TEXT,quantity INTEGER,status TEXT,delivered_on TEXT,
      dispatched_on TEXT,return_on TEXT,raw TEXT,import_id INTEGER REFERENCES file_imports(id));
    CREATE TABLE IF NOT EXISTS fk_order_history(id INTEGER PRIMARY KEY,item_id TEXT,
      import_id INTEGER REFERENCES file_imports(id),raw TEXT);
    CREATE TABLE IF NOT EXISTS transactions(identity TEXT PRIMARY KEY,item_id TEXT,sku TEXT,
      ordered_on TEXT,paid_on TEXT,neft TEXT,sheet TEXT,net INTEGER,refund INTEGER,
      sale INTEGER,protection INTEGER,return_type TEXT,return_status TEXT,components TEXT,
      raw TEXT,import_id INTEGER REFERENCES file_imports(id));
    CREATE TABLE IF NOT EXISTS transaction_sources(identity TEXT REFERENCES transactions(identity),
      import_id INTEGER REFERENCES file_imports(id),sheet TEXT,row_no INTEGER,
      PRIMARY KEY(identity,import_id,sheet,row_no));
    CREATE TABLE IF NOT EXISTS fk_products(sku TEXT PRIMARY KEY,title TEXT,
      policy TEXT DEFAULT 'non_returnable',return_days INTEGER);
    CREATE TABLE IF NOT EXISTS fk_costs(sku TEXT REFERENCES fk_products(sku),effective_on TEXT,
      purchase INTEGER,packaging INTEGER,PRIMARY KEY(sku,effective_on));
    CREATE TABLE IF NOT EXISTS fk_recovery(item_id TEXT,effective_on TEXT,state TEXT,quantity INTEGER,
      PRIMARY KEY(item_id,effective_on));
    CREATE TABLE IF NOT EXISTS fk_return_inspections(item_id TEXT,effective_on TEXT,
      sellable INTEGER,damaged INTEGER,missing INTEGER,wrong INTEGER,notes TEXT,evidence TEXT,
      claim_reference TEXT,claim_status TEXT,PRIMARY KEY(item_id,effective_on));
    CREATE TABLE IF NOT EXISTS fk_manual_returns(item_id TEXT,effective_on TEXT,category TEXT,notes TEXT,
      PRIMARY KEY(item_id,effective_on));
    CREATE TABLE IF NOT EXISTS fk_customer_details(item_id TEXT PRIMARY KEY,details TEXT,import_id INTEGER REFERENCES file_imports(id));
    CREATE TABLE IF NOT EXISTS fk_payment_reviews(item_id TEXT PRIMARY KEY,fingerprint TEXT,status TEXT,notes TEXT,effective_on TEXT);
    CREATE TABLE IF NOT EXISTS fk_status_corrections(item_id TEXT,effective_on TEXT,status TEXT,delivered_on TEXT,notes TEXT,source_import_id INTEGER,PRIMARY KEY(item_id,effective_on));
    CREATE INDEX IF NOT EXISTS tx_item_date ON transactions(item_id,paid_on);
    CREATE INDEX IF NOT EXISTS order_date ON fk_orders(ordered_on);
    ''')
    return db

def tables(name, payload):
    if name.lower().endswith('.csv'):
        try:
            decoded = payload.decode('utf-8-sig')
        except UnicodeDecodeError:
            decoded = payload.decode('cp1252')
        try:
            dialect = csv.Sniffer().sniff(decoded[:8192], delimiters=',;\t')
        except csv.Error:
            dialect = csv.excel
        return {'CSV': list(csv.reader(io.StringIO(decoded), dialect))}
    if name.lower().endswith('.xlsx'):
        import openpyxl
        book = openpyxl.load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
        try:
            result = {}
            for sheet in book:
                # Export dimensions are incorrect: A1/A1:AM1 despite hundreds of rows.
                sheet.reset_dimensions()
                result[sheet.title] = list(sheet.values)
            return result
        finally:
            book.close()
    raise ValueError('Use CSV or XLSX files. Extract ZIP downloads first.')

def columns(headers):
    return {norm(h): i for i, h in enumerate(headers) if text(h)}

def field(row, cols, *names, default=''):
    for name in names:
        index = cols.get(norm(name))
        if index is not None:
            return row[index] if index < len(row) else default
    return default

def header_index(rows):
    for i, row in enumerate(rows[:15]):
        c = columns(row)
        if 'orderitemid' in c and ('orderitemstatus' in c or 'orderstate' in c or 'neftid' in c or 'paymentdate' in c):
            return i
        if 'neftid' in c and any(k.startswith('settlementvalue') for k in c):
            return i
    return None

def parse_file(name, payload):
    sheets = tables(name, payload)
    orders, transactions, errors, warnings, counts = [], [], [], [], {}
    summary_net = None
    duration = ''
    for sn, rows in sheets.items():
        if sn == 'Summary of report':
            for row in rows:
                if len(row)>4 and text(row[1])=='Net Bank Settlement (A)':
                    summary_net=money(row[4])
                if len(row)>2 and text(row[1])=='Payment Duration':
                    duration=text(row[2])
            continue
        if sn in ('Help','Report Help','GST_Details'):
            continue
        hi=header_index(rows)
        if hi is None:
            if any(any(text(c) for c in row) for row in rows):
                warnings.append(f'{sn}: unrecognized sheet; not imported')
            continue
        headers=rows[hi]
        cols=columns(headers)
        enriched='orderstate' in cols and 'orderedon' in cols
        is_order='orderitemstatus' in cols or enriched
        counts[sn]=0
        get=lambda row,*names,default='': field(row,cols,*names,default=default)
        for rn,row in enumerate(rows[hi+1:],hi+2):
            if not any(text(c) for c in row):
                continue
            # Payment Orders has a third header row containing buyer-sale labels only.
            if not text(get(row,'order item id')) and not text(get(row,'NEFT ID')) and not any(text(c) for c in row[:6]):
                continue
            try:
                raw={f'{i+1}: {text(h)}':text(row[i]) if i<len(row) else '' for i,h in enumerate(headers) if text(h)}
                if is_order:
                    iid,sku=item_id(get(row,'order_item_id')),sku_id(get(row,'sku'))
                    if not iid or not sku:
                        raise ValueError('Missing order item ID or SKU')
                    q=Decimal(text(get(row,'quantity')))
                    if not q.is_finite() or q<=0 or q!=int(q):
                        raise ValueError('Quantity must be a positive whole number')
                    status=text(get(row,'order_item_status','Order State')).upper().replace(' ','_')
                    if not status:
                        raise ValueError('Order status missing')
                    orders.append(dict(item_id=iid,order_id=text(get(row,'order_id')),sku=sku,
                        title=clean(get(row,'product_title','Product')),ordered_on=day(get(row,'order_date','Ordered On')),
                        quantity=int(q),status=status,delivered_on=day(get(row,'order_delivery_date','delivered_on'),True),
                        dispatched_on=day(get(row,'dispatched_date'),True),return_on=day(get(row,'order_return_approval_date'),True),raw=raw,
                        enriched=enriched,
                        customer={key:text(get(row,label)) for key,label in (
                            ('buyer_name','Buyer name'),('recipient_name','Ship to name'),('address1','Address Line 1'),
                            ('address2','Address Line 2'),('city','City'),('state','State'),('pin','PIN Code'),
                            ('invoice_no','Invoice No.'),('invoice_date','Invoice Date (mm/dd/yy)'),
                            ('invoice_amount','Invoice Amount'),('selling_price','Selling Price Per Item'),
                            ('price_with_contribution','Price inc. FKMP Contribution & Subsidy'))} if enriched else {}))
                else:
                    net_cols=[i for k,i in cols.items() if k.startswith(('banksettlementvalue','settlementvalue'))]
                    if len(net_cols)!=1:
                        raise ValueError('A single bank/settlement value column is required')
                    net=money(row[net_cols[0]])
                    iid=item_id(get(row,'order item id'))
                    neft=text(get(row,'NEFT ID'))
                    if not neft:
                        raise ValueError('NEFT reference missing')
                    pd=day(get(row,'Payment Date'),True)
                    components={norm(h):money(row[i],True) for i,h in enumerate(headers)
                        if i<len(row) and re.search(r'\(\s*Rs\.?\s*\)',text(h),re.I) and not norm(h).startswith(('banksettlementvalue','settlementvalue'))}
                    bank=any(k.startswith('banksettlementvalue') for k in cols)
                    rt,rs=text(get(row,'Return Type')),text(get(row,'Item Return Status'))
                    semantic=dict(item_id=iid,sku=sku_id(get(row,'Seller SKU','SKU')),
                        ordered_on=day(get(row,'Order Date'),True),paid_on=pd,neft=neft,
                        sheet='Orders' if bank else sn,net=net,refund=money(get(row,'Refund (Rs.)'),True),
                        sale=money(get(row,'Sale Amount (Rs.)'),True),protection=money(get(row,'Protection Fund (Rs.)'),True),
                        return_type='' if rt.upper()=='NA' else rt,return_status='' if rs.upper()=='NA' else rs,components=components)
                    # Distinct financial rows share NEFT; NEFT alone is not a row ID.
                    reference=text(get(row,'Claim ID','Campaign / Transaction ID','Transaction ID','Service Order ID','Recall ID'))
                    event=text(get(row,'Type','Service Name','Transaction Name','Neft Type'))
                    fingerprint=digest(semantic|dict(reference=reference,event=event))
                    transactions.append(dict(**semantic,fingerprint=fingerprint,raw=raw,source_sheet=sn,row_no=rn))
                counts[sn]+=1
            except (ValueError,InvalidOperation,IndexError) as exc:
                errors.append(f'{sn}, row {rn}: {exc}')
    if orders and transactions:
        errors.append('Orders and settlements must be imported from separate files')
    if not orders and not transactions:
        errors.append('No recognized data rows found')
    repeated=[k for k,v in Counter(r['item_id'] for r in orders).items() if v>1]
    if repeated:
        errors.append('Repeated order item IDs in report: '+', '.join(repeated[:5]))
    seen=Counter()
    for r in transactions:
        base=r.pop('fingerprint')
        seen[base]+=1
        r['identity']=base+':'+str(seen[base])
    if any(n>1 for n in seen.values()):
        warnings.append('Identical order financial rows are stored for audit but counted once, including repeated rows within a file.')
    imported_net=sum(r['net'] for r in transactions)
    delta=imported_net-summary_net if summary_net is not None else None
    if delta:
        errors.append(f'Summary differs from imported bank rows by Rs. {rupees(delta):.6f}')
    no_dates=sum(not r['paid_on'] for r in transactions)
    if no_dates:
        warnings.append(f'{no_dates} account entries have no payment date and remain outside dated reports')
    if transactions:
        warnings.append('Identical order financial rows are counted once across overlapping files even if the NEFT reference or return status changes. Different dates, amounts or financial breakdowns remain distinct; review corrections. Account-level entries retain their references.')
    if any(r.get('enriched') for r in orders):
        warnings.append('Customer/invoice CSV: adds address details and updates statuses. Existing delivery, return and cancellation details are preserved. Import snapshots oldest first; this file has no export timestamp to establish freshness. Existing SKU labels and costs are retained when spelling differs.')
    return dict(name=name,kind='orders_details' if any(r.get('enriched') for r in orders) else 'orders' if orders else 'payments',orders=orders,transactions=transactions,
        errors=errors,warnings=warnings,sheets=counts,duration=duration,net=imported_net,
        summary_net=summary_net,summary_delta=delta,digest=hashlib.sha256(payload).hexdigest())

def preview(result):
    return {k:v for k,v in result.items() if k not in ('orders','transactions')}|dict(
        order_count=len(result['orders']),transaction_count=len(result['transactions']),
        sample_orders=[{k:v for k,v in r.items() if k not in ('raw','customer')} for r in result['orders'][:3]],
        sample_transactions=[{k:v for k,v in r.items() if k not in ('raw','components')} for r in result['transactions'][:3]])

def commit_file(db,result,payload):
    if result['errors']:
        raise ValueError('\n'.join(result['errors'][:20]))
    if db.execute('SELECT 1 FROM file_imports WHERE digest=?',(result['digest'],)).fetchone():
        return dict(duplicate_file=True,added=0,updated=0,skipped=0)
    added=updated=skipped=0
    with db:
        fid=db.execute('INSERT INTO file_imports(name,digest,kind,payload) VALUES (?,?,?,?)',
            (result['name'],result['digest'],result['kind'],payload)).lastrowid
        for r in result['orders']:
            r=dict(r)
            source_raw=json.dumps(r['raw'],ensure_ascii=False)
            old=db.execute('SELECT * FROM fk_orders WHERE item_id=?',(r['item_id'],)).fetchone()
            if old and any(old[k]!=r[k] for k in ('order_id','ordered_on','quantity')):
                raise ValueError('Order identity, date or quantity conflicts for '+r['item_id']+'; review the file before importing')
            if r.get('enriched'):
                previous=db.execute('SELECT details FROM fk_customer_details WHERE item_id=?',(r['item_id'],)).fetchone()
                customer=json.loads(previous['details']) if previous else {}
                customer.update({k:v for k,v in r['customer'].items() if v})
                db.execute('INSERT OR REPLACE INTO fk_customer_details VALUES (?,?,?)',(r['item_id'],json.dumps(customer,ensure_ascii=False),fid))
            if old:
                for key in ('delivered_on','dispatched_on','return_on','title'):
                    if not r[key]:r[key]=old[key]
                if r.get('enriched'):r['sku']=old['sku']
                r['raw']=json.loads(old['raw'])|{k:v for k,v in r['raw'].items() if v}
            db.execute('INSERT OR IGNORE INTO fk_products(sku,title) VALUES (?,?)',(r['sku'],r['title']))
            raw=json.dumps(r['raw'],ensure_ascii=False)
            if old and old['raw']==raw:
                skipped+=1
                continue
            names=['item_id','order_id','sku','title','ordered_on','quantity','status','delivered_on','dispatched_on','return_on']
            db.execute('INSERT OR REPLACE INTO fk_orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',tuple(r[k] for k in names)+(raw,fid))
            db.execute('INSERT INTO fk_order_history(item_id,import_id,raw) VALUES (?,?,?)',(r['item_id'],fid,source_raw))
            added+=not bool(old)
            updated+=bool(old)
        for r in result['transactions']:
            old=db.execute('SELECT 1 FROM transactions WHERE identity=?',(r['identity'],)).fetchone()
            if not old:
                db.execute('INSERT INTO transactions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    tuple(r[k] for k in ('identity','item_id','sku','ordered_on','paid_on','neft','sheet','net','refund','sale','protection','return_type','return_status'))+
                    (json.dumps(r['components']),json.dumps(r['raw'],ensure_ascii=False),fid))
                added+=1
            else:
                skipped+=1
            db.execute('INSERT OR IGNORE INTO transaction_sources VALUES (?,?,?,?)',(r['identity'],fid,r['source_sheet'],r['row_no']))
            if r['sku']:
                db.execute('INSERT OR IGNORE INTO fk_products(sku,title) VALUES (?,?)',(r['sku'],''))
        info=preview(result)|dict(added=added,updated=updated,skipped=skipped)
        db.execute('UPDATE file_imports SET summary=? WHERE id=?',(json.dumps(info),fid))
    return info

def products(db,on=None):
    on=day(on or today())
    out=[]
    for r in db.execute('SELECT p.*, (SELECT COUNT(*) FROM fk_orders o WHERE o.sku=p.sku) AS orders FROM fk_products p ORDER BY sku'):
        c=db.execute('SELECT * FROM fk_costs WHERE sku=? AND effective_on<=? ORDER BY effective_on DESC LIMIT 1',(r['sku'],on)).fetchone()
        out.append(dict(r)|dict(cost=rupees(c['purchase']) if c else None,packaging=rupees(c['packaging']) if c else None,
            effective_on=c['effective_on'] if c else '',history=[dict(h) for h in db.execute('SELECT * FROM fk_costs WHERE sku=? ORDER BY effective_on DESC',(r['sku'],))]))
    return out

def save_product(db,value):
    sku=sku_id(value.get('sku'))
    if not db.execute('SELECT 1 FROM fk_products WHERE sku=?',(sku,)).fetchone():
        raise ValueError('Unknown SKU')
    policy=value.get('policy','non_returnable')
    if policy not in ('unknown','non_returnable','returnable'):
        raise ValueError('Choose a valid return policy')
    days=None if text(value.get('return_days'))=='' else int(value['return_days'])
    if policy=='returnable' and (days is None or not 0<=days<=365):
        raise ValueError('Set a return window between 0 and 365 days')
    if days is not None and not 0<=days<=365:
        raise ValueError('Invalid return window')
    purchase=None if text(value.get('cost'))=='' else money(value['cost'])
    packaging=None if text(value.get('packaging'))=='' else money(value['packaging'])
    if any(v is not None and v<0 for v in (purchase,packaging)):
        raise ValueError('Costs cannot be negative')
    effective=day(value.get('effective_on') or '0001-01-01')
    with db:
        db.execute('UPDATE fk_products SET policy=?,return_days=? WHERE sku=?',(policy,days,sku))
        db.execute('INSERT OR REPLACE INTO fk_costs VALUES (?,?,?,?)',(sku,effective,purchase,packaging))

def save_manual_return(db,value):
    iid=item_id(value['item_id'])
    if not db.execute('SELECT 1 FROM fk_orders WHERE item_id=?',(iid,)).fetchone():
        raise ValueError('Unknown order item')
    category=value.get('category','Unclassified')
    if category not in ('RTO','RVP','Unclassified'):
        raise ValueError('Choose RTO, RVP or Unclassified')
    effective=day(value.get('effective_on') or today())
    with db:
        db.execute('INSERT OR REPLACE INTO fk_manual_returns VALUES (?,?,?,?)',(iid,effective,category,text(value.get('notes'))))


def save_recovery(db,value):
    iid=item_id(value['item_id'])
    order=db.execute('SELECT quantity FROM fk_orders WHERE item_id=?',(iid,)).fetchone()
    if not order:
        raise ValueError('Unknown order item')
    if 'sellable_units' in value:
        effective=day(value.get('effective_on') or today())
        counts=[int(value.get(k,0) or 0) for k in ('sellable_units','damaged_units','missing_units','wrong_units')]
        if any(n<0 for n in counts) or sum(counts)>order['quantity']:
            raise ValueError('Inspected units must be non-negative and cannot exceed order quantity')
        if not order_detail(db,iid,effective)['order']['returned']:
            raise ValueError('Inspection must be for a flagged return on or after its return date')
        claim=value.get('claim_status','Not filed')
        if claim not in ('Not filed','Pending','Approved','Rejected','Paid'):
            raise ValueError('Invalid claim status')
        with db:
            db.execute('INSERT OR REPLACE INTO fk_recovery VALUES (?,?,?,?)',(iid,effective,'mixed',counts[0]))
            db.execute('INSERT OR REPLACE INTO fk_return_inspections VALUES (?,?,?,?,?,?,?,?,?,?)',
                (iid,effective,*counts,text(value.get('notes')),text(value.get('evidence')),text(value.get('claim_reference')),claim))
        return
    state=value['state']
    if state not in ('unconfirmed','sellable','damaged','lost'):
        raise ValueError('Invalid recovery state')
    effective=day(value.get('effective_on') or today())
    quantity=int(value.get('quantity',order['quantity']))
    if not 0<=quantity<=order['quantity'] or (state=='sellable' and quantity==0):
        raise ValueError('Recovered units must be between 1 and the order quantity for sellable returns')
    with db:
        db.execute('INSERT OR REPLACE INTO fk_recovery VALUES (?,?,?,?)',(iid,effective,state,quantity))
        db.execute('DELETE FROM fk_return_inspections WHERE item_id=? AND effective_on=?',(iid,effective))

def offer_details(payments):
    totals={k:None for k in ('offer_total','offer_my_share','offer_adjustments')}
    names={'offer_total':'totalofferamountrs','offer_my_share':'mysharers','offer_adjustments':'offeradjustmentsrs'}
    evidence=False
    applied=False
    seller=False
    buyer_total=buyer_share=None
    for payment in payments:
        components=json.loads(payment['components'])
        raw=json.loads(payment['raw'])
        present={norm(label.split(': ',1)[-1]) for label,value in raw.items() if text(value).upper() not in ('','NA','NOT AVAILABLE')}
        for key,name in names.items():
            if name in components and name in present:
                totals[key]=(totals[key] or 0)+components[name]
                if key!='offer_adjustments':
                    evidence=True
                applied=applied or bool(components[name])
                seller=seller or (key=='offer_my_share' and components[name]<0)
        # Buyer details identify an applied offer even on a zero settlement row.
        # They describe the order and must never be added across payment rows.
        for label,value in raw.items():
            name=norm(label.split(': ',1)[-1])
            if name in ('totalofferamount','myshare') and text(value).upper() not in ('','NA','NOT AVAILABLE'):
                try:
                    amount=money(value)
                except (ValueError,InvalidOperation):
                    continue
                evidence=True
                applied=applied or bool(amount)
                seller=seller or (name=='myshare' and amount<0)
                if name=='totalofferamount':
                    buyer_total=amount
                else:
                    buyer_share=amount
    return totals|dict(offer_status='Applied' if applied else 'None' if evidence else 'Unknown',
        offer_seller_funded=seller,offer_buyer_total=buyer_total,offer_buyer_my_share=buyer_share,
        offer_flipkart_share=totals['offer_total']+totals['offer_my_share'] if totals['offer_total'] is not None and totals['offer_my_share'] is not None else None)


def reconciled_transactions(db):
    """Keep original imports; count an identical order financial event only once."""
    rows=[dict(r) for r in db.execute('SELECT t.*,i.name AS source FROM transactions t JOIN file_imports i ON i.id=t.import_id ORDER BY paid_on,import_id,identity')]
    def component_key(name):
        # Flipkart adds changing column-letter suffixes to these headings in
        # different exports (for example marketplacefeerssumuah/vai). They
        # are the same financial component and must not split a duplicate.
        for prefix in ('marketplacefeerssum','discountinmpfeesrs',
                       'gstondiscountrs18','totaldiscountinmpfeers',
                       'offeradjustmentrs'):
            if name.startswith(prefix):
                return prefix
        return name
    def key(row):
        if not row['item_id']:
            return row['identity']
        components={component_key(k):v for k,v in json.loads(row['components']).items() if v}
        raw=json.loads(row['raw'])
        fields={norm(k.split(': ',1)[-1]):text(v) for k,v in raw.items()}
        refs={k:fields[k] for k in ('claimid','campaigntransactionid','transactionid','serviceorderid','recallid') if fields.get(k)}
        return digest({k:row[k] for k in ('item_id','sku','ordered_on','paid_on','sheet','net','refund','sale','protection')}|dict(components=components,references=refs))
    groups=defaultdict(list)
    for row in rows:
        groups[key(row)].append(row)
    kept=[];duplicates=[]
    for group in groups.values():
        # Prefer the real bank reference to a preliminary disbursement status.
        canonical=max(group,key=lambda r:(not r['neft'].upper().startswith('DISBURSEMENT_'),r['import_id']))
        kept.append(canonical)
        duplicates.extend(r|dict(duplicate_of=canonical['identity']) for r in group if r['identity']!=canonical['identity'])
    return sorted(kept,key=lambda r:(r['paid_on'],r['identity'])),duplicates


def payment_fingerprint(payments):
    return digest(sorted([digest({k:r[k] for k in ('item_id','paid_on','net','sale','refund','protection','components')}) for r in payments]))


def save_status_correction(db,value):
    iid=item_id(value['item_id']);status=value.get('status','')
    allowed=('','APPROVED','READY_TO_DISPATCH','SHIPPED','DELIVERED','CANCELLED','REJECTED','RETURN_REQUESTED','RETURNED')
    if status not in allowed:
        raise ValueError('Invalid order status')
    order=db.execute('SELECT * FROM fk_orders WHERE item_id=?',(iid,)).fetchone()
    if not order:raise ValueError('Unknown order item')
    effective=day(value.get('effective_on') or today())
    if effective<order['ordered_on']:raise ValueError('Correction date cannot precede the order date')
    delivered=day(value.get('delivered_on'),True)
    if delivered and (delivered<order['ordered_on'] or delivered>effective):
        raise ValueError('Delivery date must be between order date and correction date')
    notes=text(value.get('notes'))
    if status and not notes:raise ValueError('Please give a reason for the correction')
    if str(value.get('source_import_id'))!=str(order['import_id']):
        raise ValueError('Order report changed. Reopen the order before correcting it.')
    with db:
        db.execute('INSERT OR REPLACE INTO fk_status_corrections VALUES (?,?,?,?,?,?)',(iid,effective,status,delivered,notes,order['import_id']))


def save_payment_review(db,value):
    iid=item_id(value['item_id']);status=value.get('status','auto')
    if status not in ('auto','confirmed','review'):
        raise ValueError('Invalid review choice')
    if not db.execute('SELECT 1 FROM fk_orders WHERE item_id=?',(iid,)).fetchone():
        raise ValueError('Unknown order item')
    effective=day(value.get('as_of') or today())
    payments=[r for r in reconciled_transactions(db)[0] if r['item_id']==iid and r['paid_on'] and r['paid_on']<=effective]
    if status=='confirmed' and not payments:
        raise ValueError('No imported settlement entries to confirm')
    fingerprint=payment_fingerprint(payments)
    if value.get('fingerprint')!=fingerprint:
        raise ValueError('Payments changed. Reopen this order before reviewing.')
    with db:
        db.execute('INSERT OR REPLACE INTO fk_payment_reviews VALUES (?,?,?,?,?)',(iid,fingerprint,status,text(value.get('notes')),effective))


def tracking_details(raw):
    fields={norm(k.split(': ',1)[-1]):text(v) for k,v in raw.items()}
    def value(*keys):
        for key in keys:
            v=fields.get(key,'')
            if v and v.upper() not in ('NA','N/A','NOT AVAILABLE'):
                return re.sub(r'^(?:DTr:|RTr:)', '',v,flags=re.I).strip()
        return ''
    return dict(forward_tracking_id=value('trackingid','deliverytrackingid','forwardtrackingid'),
                reverse_tracking_id=value('reversetrackingid','returntrackingid','reverseshippingtrackingid'))


def settlement_row_type(row):
    components=row['components']
    if isinstance(components,str):
        components=json.loads(components)
    if row['refund'] or row['protection'] or row['return_type']:
        return 'Return / adjustment'
    if row['sale']>0 and components.get('totalofferamountrs',0)==0:
        return 'Sale settlement'
    if row['sale']==0 and components.get('totalofferamountrs',0)>0:
        return 'Offer settlement'
    return 'Other settlement'


def payout_reviews(payments):
    groups=defaultdict(list)
    for row in payments:
        groups[(row['neft'],row['paid_on'],row['sheet'])].append(row)
    results=[]
    primary=('saleamountrs','totalofferamountrs','mysharers','customeraddonsamountrs',
             'marketplacefeerssumvai','taxesrs','offeradjustmentsrs','protectionfundrs','refundrs')
    for (reference,paid_on,sheet),rows in groups.items():
        if len(rows)<2:
            continue
        types=[settlement_row_type(r) for r in rows]
        balanced=True
        for row in rows:
            c=json.loads(row['components']) if isinstance(row['components'],str) else row['components']
            c=dict(c)
            fees=[v for k,v in c.items() if k.startswith('marketplacefeerssum')]
            if len(fees)==1:c['marketplacefeerssumvai']=fees[0]
            balanced=balanced and all(k in c for k in primary) and sum(c.get(k,0) for k in primary)==row['net']
        accepted=(len(rows)==2 and bool(reference) and bool(paid_on)
                  and sorted(types)==['Offer settlement','Sale settlement']
                  and all(r['net']>0 for r in rows) and balanced)
        results.append(dict(reference=reference,paid_on=paid_on,sheet=sheet,rows=len(rows),
                            net=sum(r['net'] for r in rows),accepted=accepted,
                            reason='Sale + offer payout verified' if accepted else 'Multiple rows in one payout need review'))
    return results


def offer_summary(rows):
    return dict(applied_orders=sum(r['offer_status']=='Applied' for r in rows),
        no_offer_orders=sum(r['offer_status']=='None' for r in rows),
        unknown_orders=sum(r['offer_status']=='Unknown' for r in rows),
        seller_funded_orders=sum(r['offer_seller_funded'] for r in rows),
        **{key:sum(r[key] for r in rows if r[key] is not None) if any(r[key] is not None for r in rows) else None
           for key in ('offer_total','offer_my_share','offer_flipkart_share','offer_adjustments')})


def sku_summary(rows):
    grouped=defaultdict(list)
    for row in rows:
        grouped[row['sku']].append(row)
    result=[]
    for sku,items in sorted(grouped.items()):
        units=Counter()
        for r in items:
            bucket=r['return_category'] if r['returned'] else 'Delivered' if r['status']=='DELIVERED' else 'Cancelled' if r['status'] in ('CANCELLED','REJECTED') else 'Other'
            units[bucket]+=r['quantity']
        recognized=[r for r in items if r['profit'] is not None]
        provisional=[r for r in items if r['provisional'] is not None]
        settled=[r for r in items if r['payment_count']]
        profit=sum(r['profit'] for r in recognized) if recognized else None
        recognized_units=sum(r['quantity'] for r in recognized)
        recognized_net=sum(r['net'] for r in recognized)
        result.append(dict(sku=sku,title=items[0]['title'],orders=len(items),units=sum(r['quantity'] for r in items),
            delivered_units=units['Delivered'],rvp_units=units['RVP'],rto_units=units['RTO'],
            unclassified_units=units['Unclassified'],mixed_units=units['Mixed'],cancelled_units=units['Cancelled'],other_units=units['Other'],
            processed_units=sum(units[k] for k in ('Delivered','RVP','RTO','Unclassified','Mixed')),
            received=sum(r['net'] for r in items),profit=profit,
            provisional=sum(r['provisional'] for r in provisional) if provisional else None,
            recognized_orders=len(recognized),recognized_units=recognized_units,
            profit_per_unit=profit/recognized_units if recognized_units else None,
            margin=profit/recognized_net*100 if profit is not None and recognized_net>0 else None,
            paid_orders=len(settled),paid_units=sum(r['quantity'] for r in settled),
            unpaid_orders=sum(not r['payment_count'] and not r['excluded'] for r in items),review_orders=sum(r['state']=='Review' for r in items),
            excluded_orders=sum(r['excluded'] for r in items),
            missing_cost_orders=sum('Purchase cost missing' in r['issues'] or 'Packaging cost missing' in r['issues'] for r in items),
            recovered_units=sum(r['recovered_quantity'] for r in items if r['returned']),
            charged_purchase=sum(r['purchase_total'] for r in recognized),charged_packaging=sum(r['packaging'] for r in recognized),
            last_payment=max((r['last_payment'] for r in items),default=''))|offer_summary(items))
    return result


def report(db,start='',end='',as_of=None,sku=''):
    as_of=day(as_of or today())
    start=day(start) if start else '0001-01-01'
    end=day(end) if end else as_of
    if start>end:
        raise ValueError('Start date must be before end date')
    policies={r['sku']:dict(r) for r in db.execute('SELECT * FROM fk_products')}
    customers={r['item_id']:json.loads(r['details'])|dict(source=r['source']) for r in db.execute('SELECT c.*,i.name AS source FROM fk_customer_details c JOIN file_imports i ON i.id=c.import_id')}
    costs=defaultdict(list)
    for r in db.execute('SELECT * FROM fk_costs ORDER BY effective_on'):
        costs[r['sku']].append(dict(r))
    recoveries={r['item_id']:dict(r) for r in db.execute('SELECT * FROM fk_recovery WHERE effective_on<=? ORDER BY effective_on',(as_of,))}
    manual_returns={r['item_id']:dict(r) for r in db.execute('SELECT * FROM fk_manual_returns WHERE effective_on<=? ORDER BY effective_on',(as_of,))}
    unique_tx,duplicate_tx=reconciled_transactions(db)
    tx=[r for r in unique_tx if r['paid_on'] and r['paid_on']<=as_of]
    grouped=defaultdict(list)
    for r in tx:
        if r['item_id']:
            grouped[r['item_id']].append(r)
    rows=[]
    reasons=Counter()
    for record in db.execute('SELECT * FROM fk_orders WHERE ordered_on BETWEEN ? AND ? ORDER BY ordered_on DESC,item_id',(start,end)):
        o=dict(record)
        reported_status=o['status']
        correction_row=db.execute('SELECT * FROM fk_status_corrections WHERE item_id=? AND effective_on<=? ORDER BY effective_on DESC LIMIT 1',(o['item_id'],as_of)).fetchone()
        correction=dict(correction_row) if correction_row else None
        correction_applies=bool(correction and correction['status'] and correction['source_import_id']==o['import_id'])
        if correction_applies:
            o['status']=correction['status']
            if correction['status']=='DELIVERED' and correction['delivered_on']:o['delivered_on']=correction['delivered_on']
        if sku and o['sku']!=sku:
            continue
        p=grouped[o['item_id']]
        fingerprint=payment_fingerprint(p)
        saved_review=db.execute('SELECT * FROM fk_payment_reviews WHERE item_id=?',(o['item_id'],)).fetchone()
        payment_review=dict(saved_review) if saved_review else None
        review_applies=bool(payment_review and payment_review['fingerprint']==fingerprint and payment_review['effective_on']<=as_of)
        payout_review=payout_reviews(p)
        policy=policies[o['sku']]
        c=next((r for r in reversed(costs[o['sku']]) if r['effective_on']<=o['ordered_on']),None)
        net=sum(r['net'] for r in p)
        status_return='RETURN' in o['status'] and (correction_applies or not o['return_on'] or o['return_on']<=as_of)
        ret=[r for r in p if r['return_type'] or r['refund']<0]
        manual=manual_returns.get(o['item_id'])
        if correction_applies and o['status']=='DELIVERED':manual=None
        returned=bool(ret) or status_return or bool(manual)
        return_kind=next((r['return_type'] for r in reversed(ret) if r['return_type']),'Order return' if status_return else '')
        categories={return_category(r['return_type']) for r in ret if return_category(r['return_type']) in ('RTO','RVP')}
        if manual and manual['category'] in ('RTO','RVP'):
            categories.add(manual['category'])
        category='Mixed' if len(categories)>1 else next(iter(categories)) if categories else 'Unclassified' if returned else ''
        recovery_row=recoveries.get(o['item_id'],{})
        recovery=recovery_row.get('state','unconfirmed')
        recovered_quantity=recovery_row.get('quantity',0) if recovery in ('sellable','mixed') else 0
        inspection=db.execute('SELECT * FROM fk_return_inspections WHERE item_id=? AND effective_on=?',
            (o['item_id'],recovery_row.get('effective_on',''))).fetchone()
        inspected=dict(inspection) if inspection else dict(sellable=recovered_quantity,
            damaged=o['quantity'] if recovery=='damaged' else 0,
            missing=o['quantity'] if recovery=='lost' else 0,wrong=0,notes='',evidence='',claim_reference='',claim_status='Not filed')
        awaiting=o['quantity']-sum(inspected[k] for k in ('sellable','damaged','missing','wrong'))
        assumed_sellable=awaiting if returned else 0
        charged_units=o['quantity']-recovered_quantity-assumed_sellable if returned else o['quantity']
        cancelled=o['status'] in ('CANCELLED','REJECTED')
        credited_sale=sum(r['sale'] for r in p if r['sale']>0)
        refunded_sale=-sum(r['refund'] for r in p)
        cancellation_refunded=bool(cancelled and not o['dispatched_on'] and credited_sale>0 and refunded_sale==credited_sale and net<=0)
        if cancellation_refunded:charged_units=0
        issues=[]
        if correction_applies and o['status']=='DELIVERED' and ret:
            issues.append('Delivered correction conflicts with return settlement')
        purchase=c['purchase'] if c else None
        packaging=c['packaging'] if c else None
        if cancellation_refunded:purchase=0;packaging=0
        excluded=cancelled and not p
        sale_paid=any(r['sale']>0 and r['net']>0 for r in p)
        if not excluded:
            if not p:
                issues.append('Awaiting settlement')
            elif not returned and not sale_paid and not cancellation_refunded:
                issues.append('Sale payment not evidenced')
            if purchase is None and charged_units:
                issues.append('Purchase cost missing')
            if packaging is None:
                issues.append('Packaging cost missing')
            if cancelled and not cancellation_refunded:
                issues.append('Cancelled order has settlements')
            if any(r['sku'] and r['sku']!=o['sku'] for r in p):
                issues.append('Payment SKU differs from order')
            if any(not r['accepted'] for r in payout_review) and not cancellation_refunded:
                issues.append('Multiple rows in one payout need review')
            if 'REPLACE' in o['status'] or 'EXCHANGE' in o['status']:
                issues.append('Replacement costs need review')
            if returned:
                if o['status']=='RETURN_REQUESTED':
                    issues.append('Return still requested')
            else:
                if policy['policy']=='unknown':
                    issues.append('Return policy unconfirmed')
                elif policy['policy']=='returnable':
                    if not o['delivered_on'] or o['delivered_on']>as_of:
                        issues.append('Delivery date needed')
                    elif (date.fromisoformat(o['delivered_on'])+timedelta(days=policy['return_days'])).isoformat()>=as_of:
                        issues.append('Return window open')
        if returned and sum(inspected[k] for k in ('sellable','damaged','missing','wrong'))==o['quantity']:
            sale_total=sum(r['sale'] for r in p if r['sale']>0 and r['net']>0)
            refunded=-sum(r['refund'] for r in p)
            if sale_total>0 and refunded<sale_total:
                issues.append('Return refund appears incomplete')
        purchase_total=0 if charged_units==0 else None if purchase is None else purchase*charged_units
        loss_evidenced=returned and any(inspected[k]>0 for k in ('damaged','missing','wrong'))
        estimated=net-purchase_total-packaging if (p or loss_evidenced) and purchase_total is not None and packaging is not None and (not cancelled or cancellation_refunded) else None
        if review_applies and payment_review['status']=='confirmed':
            issues=[issue for issue in issues if issue!='Multiple rows in one payout need review' and not (issue=='Sale payment not evidenced' and sum(r['net'] for r in p)>0)]
        if review_applies and payment_review['status']=='review':
            issues.append('Payments manually flagged for review')
        blocking_issues=[issue for issue in issues if not (loss_evidenced and issue in ('Awaiting settlement','Return still requested'))]
        recognized=estimated if not blocking_issues and not excluded else None
        reasons.update(issues)
        last_return=ret[-1] if ret else None
        rows.append({k:v for k,v in o.items() if k not in ('raw','import_id')}|tracking_details(json.loads(o['raw']))|dict(reported_status=reported_status,status_correction=correction,status_correction_applies=correction_applies,source_import_id=o['import_id'],cancellation_refunded=cancellation_refunded,net=net,
            duplicate_payment_count=sum(r['item_id']==o['item_id'] and bool(r['paid_on']) and r['paid_on']<=as_of for r in duplicate_tx),payment_review=payment_review,payment_review_applies=review_applies,payment_fingerprint=fingerprint,payout_reviews=payout_review,payment_count=len(p),last_payment=p[-1]['paid_on'] if p else '',profit=recognized,provisional=estimated,
            purchase_total=purchase_total,packaging=packaging,policy=policy['policy'],returned=returned,
            return_type=return_kind,return_status=last_return['return_status'] if last_return else '',
            return_category=category,purchase_unit=purchase,cost_effective_on=c['effective_on'] if c else '',
            cost_units_charged=charged_units,assumed_sellable_units=assumed_sellable,manual_return=manual,
            recovery=recovery,recovered_quantity=recovered_quantity,issues=issues,excluded=excluded,
            inspection=inspected,awaiting_inspection_units=awaiting if returned else 0,
            inspection_on=recovery_row.get('effective_on',''),
            payment_status='Settlements recorded' if p else 'Unpaid',customer=customers.get(o['item_id'],{}),
            state='Excluded' if excluded else 'Loss recorded' if recognized is not None and not p else 'Recognized' if recognized is not None else 'Review' if p else 'Unpaid')|offer_details(p))
    shipment_details={r['item_id']:dict(order_id=r['order_id'])|tracking_details(json.loads(r['raw'])) for r in db.execute('SELECT item_id,order_id,raw FROM fk_orders')}
    all_ids=set(shipment_details)
    unmatched=[r for r in tx if r['item_id'] and r['item_id'] not in all_ids]
    account=[r for r in tx if not r['item_id']]
    undated=[r for r in unique_tx if not r['paid_on']]
    dates=db.execute('SELECT MIN(ordered_on),MAX(ordered_on) FROM fk_orders').fetchone()
    return_totals={}
    for category in ('RTO','RVP','Unclassified','Mixed'):
        subset=[r for r in rows if r['return_category']==category]
        recognized_subset=[r for r in subset if r['profit'] is not None]
        return_totals[category]=dict(orders=len(subset),received=sum(r['net'] for r in subset),
            recognized_orders=len(recognized_subset),review_orders=sum(bool(r['issues']) for r in subset),
            profit=sum(r['profit'] for r in recognized_subset) if recognized_subset else None,
            units=sum(r['quantity'] for r in subset),
            **{k+'_units':sum(r['inspection'][k] for r in subset) for k in ('sellable','damaged','missing','wrong')},
            awaiting_units=sum(r['awaiting_inspection_units'] for r in subset),
            assumed_sellable_units=sum(r['assumed_sellable_units'] for r in subset),
            loss_cost=sum(r['purchase_unit']*sum(r['inspection'][k] for k in ('damaged','missing','wrong')) for r in subset if r['purchase_unit'] is not None),
            loss_cost_unknown=sum(r['purchase_unit'] is None and any(r['inspection'][k] for k in ('damaged','missing','wrong')) for r in subset),
            pending_claims=sum(r['inspection']['claim_status'] in ('Pending','Approved') for r in subset))
    return dict(orders=rows,sku_totals=sku_summary(rows),offers=offer_summary(rows),unmatched=unmatched,account=account,undated=undated,
        settlements=[{k:v for k,v in r.items() if k not in ('raw','components')}|shipment_details.get(r['item_id'],{})|dict(return_category=return_category(r['return_type']) or ('Unclassified' if r['refund']<0 else '')) for r in tx],issues=dict(reasons),return_totals=return_totals,
        as_of=as_of,start=start,end=end,first_order=dates[0],last_order=dates[1],
        totals=dict(orders=len(rows),paid_orders=sum(bool(r['payment_count']) for r in rows),
            unpaid_orders=sum(not r['payment_count'] and not r['excluded'] for r in rows),
            excluded_orders=sum(r['excluded'] for r in rows),recognized_orders=sum(r['profit'] is not None for r in rows),
            review_orders=sum(bool(r['issues']) for r in rows),received=sum(r['net'] for r in rows),
            profit=sum(r['profit'] for r in rows if r['profit'] is not None) if any(r['profit'] is not None for r in rows) else None,
            bank_total=sum(r['net'] for r in tx),account_net=sum(r['net'] for r in account),
            unmatched_net=sum(r['net'] for r in unmatched),
            tax_credits=sum(sum(v for k,v in json.loads(r['components']).items() if k.startswith(('inputgsttcscredits','incometaxcredits'))) for r in tx)))

def order_detail(db,iid,as_of=None):
    o=db.execute('SELECT * FROM fk_orders WHERE item_id=?',(item_id(iid),)).fetchone()
    if not o:
        raise ValueError('Unknown order item')
    rows=report(db,o['ordered_on'],o['ordered_on'],as_of)['orders']
    order=next(r for r in rows if r['item_id']==o['item_id'])
    unique_tx,duplicates=reconciled_transactions(db)
    payments=[]
    for r in unique_tx:
        if r['item_id']!=o['item_id']:
            continue
        value=dict(r)
        value['settlement_type']=settlement_row_type(value)
        value['raw']=json.loads(value['raw'])
        value['components']=json.loads(value['components'])
        value['return_category']=return_category(value['return_type']) or ('Unclassified' if value['refund']<0 else '')
        payments.append(value)
    return dict(order=order,transactions=payments,duplicate_transactions=[r for r in duplicates if r['item_id']==o['item_id']],raw=json.loads(o['raw']),
        inspections=[dict(r) for r in db.execute('SELECT * FROM fk_return_inspections WHERE item_id=? ORDER BY effective_on DESC',(o['item_id'],))],
        history=[dict(r) for r in db.execute('SELECT h.*,i.name AS source FROM fk_order_history h JOIN file_imports i ON i.id=h.import_id WHERE item_id=? ORDER BY h.id',(o['item_id'],))])
