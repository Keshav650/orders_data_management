import base64
import csv
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app import make_server
from recon.database import connect
from recon.engine import report, save_products, save_settings, save_recovery, create_product, products
from recon.importer import commit_file, digest, money, parse_file, sku_key
from recon.maintenance import backfill_initial_packaging
from recon.costing import purchase_cost


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = connect(Path(self.temp.name) / 'test.sqlite3')

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def order(self, sid='order_1', status='DELIVERED', qty=1, when='2026-07-01', sku='SKU A'):
        record = dict(sub_order_id=sid, order_date=when, sku=sku, quantity=qty,
                      listed_paise=15000, status=status, packet='packet')
        self.db.execute('INSERT INTO order_events(fingerprint,sub_order_id,order_date,sku,quantity,listed_paise,status,packet,raw) VALUES (?,?,?,?,?,?,?,?,?)',
                        (digest(record), sid, when, sku, qty, 15000, status, 'packet', '{}'))
        self.db.execute('INSERT OR IGNORE INTO products VALUES (?,?)', (sku_key(sku), sku))

    def cost(self, cost='118', packaging='5', when='0001-01-01'):
        save_products(self.db, [{'sku': 'SKU A', 'cost': cost, 'packaging': packaging}], when)

    def payment(self, amount='150', sid='order_1', when='2026-07-25', state='DELIVERED', reverse='0', txn='T1'):
        self.db.execute("INSERT OR IGNORE INTO imports(id,name,digest,kind,payload,summary) VALUES (1,'test','test','payments',X'00','{}')")
        record = dict(sub_order_id=sid, order_date='2026-07-01', payment_date=when, transaction_id=txn, status=state,
                      settlement=money(amount), shipping=-1000, reverse_shipping=money(reverse), tds=-10, tcs=-50,
                      sale=16060, sale_return=0, sku='SKU A', import_id=1, raw='{}')
        record['fingerprint'] = digest(record)
        self.db.execute(f'INSERT INTO payments({",".join(record)}) VALUES ({",".join("?" for _ in record)})', tuple(record.values()))

    def result(self, **kwargs):
        return report(self.db, **dict({'start':'2026-07-01','end':'2026-07-31','as_of':'2026-08-31','mode':'custom'}, **kwargs))

    def test_settlement_components_not_deducted_twice(self):
        self.order(); self.cost(); self.payment()
        summary = self.result()['summary']
        self.assertEqual(summary['net_profit'], 2700)
        self.assertEqual(summary['tds'], -10)
        self.assertEqual(summary['shipping'], -1000)

    def test_quantity_and_packaging_once(self):
        self.order(qty=3); self.cost(); self.payment(amount='400')
        self.assertEqual(self.result()['summary']['net_profit'], 4100)

    def test_packaging_snapshot_cannot_override_backdated_price(self):
        self.order(when='2026-09-17'); self.cost(cost='100')
        save_products(self.db, [{'sku':'SKU A','packaging':'7'}], '2026-09-17')
        save_products(self.db, [{'sku':'SKU A','cost':'80'}], '2026-08-01')
        r=self.result(start='2026-09-01',end='2026-09-17',as_of='2026-09-17')
        self.assertEqual(r['orders'][0]['product_cost'],8000)
        self.assertEqual(r['orders'][0]['packaging'],700)
        self.assertEqual(products(self.db,'2026-09-17')[0]['cost'],8000)

    def test_manual_sku_creation_and_duplicate_validation(self):
        create_product(self.db,dict(sku='Hand entered',cost='45',packaging='3',effective_date='2026-07-01'))
        self.order(sku='Hand entered')
        self.assertEqual(self.result()['orders'][0]['product_cost'],4500)
        with self.assertRaises(ValueError):
            create_product(self.db,dict(sku='HAND ENTERED',cost='50',effective_date='2026-07-01'))

    def test_multiple_sku_filter_matches_union_and_performance_totals(self):
        for sid,sku in [('order_1','SKU A'),('order_2','SKU B'),('order_3','SKU C')]:
            self.order(sid=sid,sku=sku)
            save_products(self.db,[dict(sku=sku,cost='100',packaging='5')],'2026-07-01')
            self.payment(sid=sid,txn=sid)
        r=self.result(skus=['sku a','SKU B','sku a'])
        self.assertEqual(r['summary']['orders'],2)
        self.assertEqual(r['summary']['settlement'],30000)
        self.assertEqual(len(r['sku_performance']),2)
        self.assertEqual(sum(v['contribution'] for v in r['sku_performance']),r['summary']['net_profit'])
        self.assertEqual(sum(v['product_cost'] for v in r['sku_performance']),20000)

    def test_sku_filter_scopes_financials_and_keeps_all_options(self):
        self.order(); self.cost(); self.payment()
        self.order(sid='other', sku='SKU B')
        self.payment(sid='other', amount='300', txn='T2')
        self.db.execute("INSERT INTO ads VALUES ('A','2026-07-01','2026-07-01','2026-07-10','C',3616,1,'{}')")
        r = self.result(sku='SKU A')
        self.assertEqual(len(r['orders']), 1)
        self.assertEqual(r['summary']['settlement'], 15000)
        self.assertEqual(r['summary']['net_profit'], 2700)
        self.assertTrue(r['summary']['profit_before_ads'])
        self.assertEqual(r['summary']['unallocated_ads'], 3616)
        self.assertEqual(r['summary']['ads'], 0)
        self.assertEqual(self.result()['summary']['ads'], 3616)
        self.assertEqual(r['timeline'][0]['orders'], 1)
        self.assertEqual(len(r['sku_options']), 2)
        self.assertEqual(self.result(sku='missing')['summary']['orders'], 0)
        self.assertFalse(self.result()['summary']['profit_before_ads'])

    def test_cancelled_excluded_even_with_payment(self):
        self.order(status='CANCELLED'); self.cost(); self.payment()
        s = self.result()['summary']
        self.assertEqual((s['orders'],s['cancelled'],s['settlement'],s['packaging'],s['net_profit']), (0,1,0,0,0))

    def test_zero_payment_is_recorded_not_pending(self):
        self.order(status='RTO'); self.cost(); self.payment(amount='0',state='RTO')
        r=self.result()
        self.assertEqual(r['summary']['awaiting'],0)
        self.assertEqual(r['orders'][0]['payment_state'],'Zero payment recorded')

    def test_recovery_sellable_and_damaged_are_dated(self):
        self.order(status='RETURN');self.cost();self.payment(amount='0',state='RETURN')
        save_recovery(self.db, {'sub_order_id':'order_1','effective_date':'2026-08-10','state':'sellable'})
        self.assertEqual(self.result(as_of='2026-08-09')['summary']['product_cost'],11800)
        self.assertEqual(self.result()['summary']['net_profit'],-500)
        save_recovery(self.db, {'sub_order_id':'order_1','effective_date':'2026-08-20','state':'damaged'})
        self.assertEqual(self.result()['summary']['product_cost'],11800)

    def test_rto_defaults_to_reusable_without_a_purchase_price(self):
        self.order(status='RTO', qty=3); self.cost(cost='', packaging='5')
        self.payment(amount='0', state='RTO')
        r = self.result()
        self.assertEqual(r['orders'][0]['recovery'], 'sellable')
        self.assertEqual(r['summary']['product_cost'], 0)
        self.assertEqual(r['summary']['missing_cost'], 0)
        self.assertEqual(r['summary']['recovery_pending'], 0)
        self.assertEqual(r['summary']['net_profit'], -500)

    def test_rto_explicit_outcomes_override_default_from_their_date(self):
        self.order(status='RTO', qty=2); self.cost(); self.payment(amount='0', state='RTO')
        for state in ('damaged', 'lost', 'awaiting'):
            with self.subTest(state=state):
                save_recovery(self.db, {'sub_order_id':'order_1', 'effective_date':'2026-08-10', 'state':state})
                self.assertEqual(self.result(as_of='2026-08-09')['summary']['product_cost'], 0)
                self.assertEqual(self.result()['summary']['product_cost'], 23600)
        save_recovery(self.db, {'sub_order_id':'order_1', 'effective_date':'2026-08-20', 'state':'sellable'})
        self.assertEqual(self.result()['summary']['product_cost'], 0)

    def test_rto_reverse_charge_is_flagged_and_payout_preserved(self):
        self.order(status='RTO'); self.cost(); self.payment(amount='-20',state='RTO',reverse='-20')
        r=self.result();self.assertEqual(r['summary']['settlement'],-2000)
        self.assertTrue(any('Unexpected RTO' in i['issue'] for i in r['issues']))

    def test_returns_separate_from_rto(self):
        self.order();self.order(sid='order_2',status='RTO');self.cost();self.payment(state='RETURN')
        s=self.result()['summary'];self.assertEqual(s['customer_return_rate'],50);self.assertEqual(s['rto_rate'],50)

    def test_multiple_transactions_and_cross_month_payment(self):
        self.order();self.cost();self.payment();self.payment(amount='-30',when='2026-08-15',txn='T2',state='RETURN')
        self.assertEqual(self.result()['summary']['settlement'],12000)
        self.assertEqual(self.result(as_of='2026-07-31')['summary']['settlement'],15000)

    def test_twenty_one_day_boundary(self):
        self.order(when='2026-08-10');self.order(sid='order_2',when='2026-08-11');self.cost()
        mature=report(self.db,as_of='2026-08-31')
        self.assertEqual(len(mature['orders']),1)
        custom=report(self.db,start='2026-08-01',end='2026-08-31',as_of='2026-08-31',mode='custom')
        self.assertEqual(custom['summary']['overdue'],1);self.assertEqual(custom['summary']['within_window'],1)

    def test_default_is_thirty_inclusive_days(self):
        self.order(when='2026-08-27')
        result=report(self.db,as_of='2026-09-17')
        self.assertEqual(result['filters']['start'],'2026-07-29')
        self.assertEqual(result['filters']['end'],'2026-08-27')
        custom=report(self.db,end='2026-08-27',as_of='2026-09-17',mode='custom')
        self.assertEqual(custom['filters']['start'],'2026-07-29')
        explicit=report(self.db,start='2026-07-01',end='2026-08-27',as_of='2026-09-17',mode='custom')
        self.assertEqual(explicit['filters']['start'],'2026-07-01')

    def test_packaging_repair_preserves_purchase_price_dates(self):
        self.order();self.cost(cost='100',packaging='')
        self.cost(cost='200',packaging='5',when='2026-09-16')
        self.assertEqual(backfill_initial_packaging(self.db),1)
        july=self.result()['orders'][0]
        self.assertEqual(july['product_cost'],10000)
        self.assertEqual(july['packaging'],500)
        self.assertEqual(backfill_initial_packaging(self.db),0)
        self.assertEqual(self.db.execute("SELECT cost FROM product_versions WHERE effective_date='2026-09-16'").fetchone()[0],20000)

    def test_packaging_repair_preserves_explicit_zero(self):
        self.order();self.cost(cost='100',packaging='0')
        self.cost(cost='200',packaging='5',when='2026-09-16')
        self.assertEqual(backfill_initial_packaging(self.db),0)
        self.assertEqual(self.result()['orders'][0]['packaging'],500)

    def test_packaging_updates_all_dates_without_changing_purchase_prices(self):
        self.order(); self.cost(cost='100', packaging='2')
        self.cost(cost='200', packaging='5', when='2026-09-16')
        self.assertEqual(self.result()['orders'][0]['packaging'], 500)
        self.assertEqual(self.result()['orders'][0]['product_cost'], 10000)
        save_products(self.db, [{'sku':'SKU A', 'packaging':'7'}], '2026-08-01')
        self.order(sid='older_import', when='2026-06-01', qty=3)
        rows = self.result(start='2026-06-01')['orders']
        self.assertTrue(all(r['packaging'] == 700 for r in rows))
        save_products(self.db, [{'sku':'SKU A', 'cost':'110'}], '2026-07-01')
        self.assertEqual(self.result()['orders'][0]['packaging'], 700)
        save_products(self.db, [{'sku':'SKU A', 'packaging':'0'}], '2026-09-17')
        self.assertEqual(self.result()['orders'][0]['packaging'], 0)

    def test_first_packaging_after_fresh_import_applies_to_old_orders(self):
        self.order(); self.cost(cost='100', packaging='')
        save_products(self.db, [{'sku':'SKU A', 'packaging':'5'}], '2026-09-17')
        self.assertEqual(self.result()['summary']['missing_packaging'], 0)
        self.assertEqual(self.result()['orders'][0]['packaging'], 500)
        # A later product import must not restore an older packaging snapshot.
        self.db.execute("INSERT INTO product_versions(sku,effective_date,cost,packaging,source) VALUES ('sku a','2026-07-01',12000,NULL,'Import 99')")
        self.assertEqual(self.result()['orders'][0]['packaging'], 500)

    def test_first_saved_price_fills_old_orders_and_later_changes_stay_dated(self):
        self.order();self.cost(cost='',packaging='5')
        self.cost(cost='27',packaging='5',when='2026-09-16')
        self.assertEqual(self.result()['orders'][0]['product_cost'],2700)
        self.assertTrue(self.result()['orders'][0]['initial_cost_applied'])
        self.cost(cost='30',packaging='5',when='2026-09-17')
        self.assertEqual(self.result()['orders'][0]['product_cost'],2700)
        self.order(sid='new',when='2026-09-17')
        rows={o['sub_order_id']:o for o in self.result(end='2026-09-17',as_of='2026-09-17')['orders']}
        self.assertEqual(rows['new']['product_cost'],3000)
        self.order(sid='older_import',when='2026-06-01')
        self.assertEqual(self.result(start='2026-06-01')['summary']['missing_cost'],0)

    def test_unique_export_separator_match_and_ambiguity(self):
        versions={'product – pack of 4':[{'effective_date':'0001-01-01','cost':7500}]}
        self.assertEqual(purchase_cost(versions,'product ? pack of 4','2026-07-01')[0],7500)
        self.assertIsNone(purchase_cost(versions,'product ? pack of 3','2026-07-01')[0])
        versions['product — pack of 4']=[{'effective_date':'0001-01-01','cost':8000}]
        self.assertIsNone(purchase_cost(versions,'product ? pack of 4','2026-07-01')[0])

    def test_first_price_does_not_overwrite_later_blank_or_zero(self):
        versions={'sku':[{'effective_date':'2026-08-01','cost':0},
                         {'effective_date':'2026-09-01','cost':None}]}
        self.assertEqual(purchase_cost(versions,'sku','2026-07-01')[0],0)
        self.assertIsNone(purchase_cost(versions,'sku','2026-09-15')[0])

    def test_gst_and_effective_cost_history(self):
        self.order();self.cost();self.payment()
        save_settings(self.db,{'gst_rate':'18','include_gst':False})
        self.assertEqual(self.result()['summary']['product_cost'],10000)
        self.cost(cost='236',when='2026-08-01')
        self.assertEqual(self.result()['summary']['product_cost'],10000)
        save_settings(self.db,{'gst_rate':'0','include_gst':False})
        self.assertEqual(self.result()['summary']['product_cost'],11800)

    def test_blank_cost_is_not_zero(self):
        self.order();self.payment()
        self.assertEqual(self.result()['summary']['net_profit'],15000)
        self.assertTrue(self.result()['summary']['provisional'])
        self.assertIsNone(self.result()['orders'][0]['product_cost'])
        self.cost(cost='0',packaging='0')
        self.assertEqual(self.result()['summary']['net_profit'],15000)

    def test_multiple_order_events_count_once(self):
        self.order();self.order(status='EXCHANGE');self.cost()
        s=self.result()['summary'];self.assertEqual(s['orders'],1);self.assertEqual(s['packaging'],500)
        self.assertTrue(s['provisional'])

    def test_ads_use_spend_period_and_credit_sign(self):
        self.order();self.cost();self.payment()
        self.db.execute("INSERT INTO ads VALUES ('A','2026-08-01','2026-08-01','2026-08-10','C',3616,1,'{}')")
        self.assertEqual(self.result()['summary']['ads'],0)
        august=self.result(start='2026-08-01',end='2026-08-31')
        self.assertEqual(august['summary']['ads'],3616)

    def test_unmatched_payments_not_in_profit(self):
        self.order();self.cost();self.payment(sid='unknown')
        result=self.result();self.assertEqual(result['summary']['settlement'],0)
        self.assertEqual(len(result['unmatched_payments']),1)

    def test_reimport_idempotent_and_source_retained(self):
        output=io.StringIO();writer=csv.writer(output)
        writer.writerow(['Sub Order No','Order Date','SKU','Quantity','Reason for Credit Entry'])
        writer.writerow(['123456789012345678_1','2026-07-01','A',1,'DELIVERED'])
        payload=output.getvalue().encode()
        parsed=parse_file('orders.csv',payload,'orders','0001-01-01')
        commit_file(self.db,parsed);commit_file(self.db,parsed)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM imports').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM order_events').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT payload FROM imports').fetchone()[0],payload)

    def test_reexport_updates_status_without_adding_payment(self):
        self.order();self.cost();self.payment()
        existing=dict(self.db.execute('SELECT * FROM payments').fetchone())
        fp=existing.pop('fingerprint');existing.pop('raw');existing.pop('import_id')
        existing['status']='RETURN'
        parsed={'name':'updated.xlsx','digest':'updated','kind':'payments','payload':b'updated','rows':[],
                'counts':{'payments':1},'warnings':[], 'records':[('payments',fp,existing,'{}')]}
        commit_file(self.db,parsed)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM payments').fetchone()[0],1)
        self.assertEqual(self.result()['orders'][0]['status'],'RETURN')
        self.assertEqual(self.result()['summary']['settlement'],15000)

    def test_multiple_month_order_imports_accumulate_without_duplicates(self):
        header='Sub Order No,Order Date,SKU,Quantity,Reason for Credit Entry\n'
        july=(header+'july_1,2026-07-31,SKU A,1,DELIVERED\n').encode()
        august=(header+'august_1,2026-08-01,SKU A,1,DELIVERED\n').encode()
        batches=[parse_file(name,payload,'orders','0001-01-01')
                 for name,payload in [('July.csv',july),('August.csv',august)]]
        for batch in batches: commit_file(self.db,batch)
        for batch in batches: commit_file(self.db,batch)
        self.cost(cost='100')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM order_events').fetchone()[0],2)
        self.assertEqual(self.result()['summary']['orders'],1)
        combined=self.result(end='2026-08-31')
        self.assertEqual(combined['summary']['orders'],2)

    def test_price_applies_on_order_date_boundary_and_retains_history(self):
        self.order(sid='july_1',when='2026-07-31')
        self.order(sid='august_1',when='2026-08-01',qty=2)
        self.order(sid='september_1',when='2026-09-01')
        self.cost(cost='100',when='2026-07-01')
        self.cost(cost='120',when='2026-08-01')
        self.cost(cost='140',when='2026-09-01')
        rows={r['sub_order_id']:r for r in self.result(end='2026-09-30',as_of='2026-10-31')['orders']}
        self.assertEqual(rows['july_1']['product_cost'],10000)
        self.assertEqual(rows['august_1']['product_cost'],24000)
        self.assertEqual(rows['september_1']['product_cost'],14000)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM product_versions').fetchone()[0],3)


class SampleFileTests(unittest.TestCase):
    def test_real_files_and_overlapping_uploads(self):
        samples=Path(__file__).resolve().parents[1]/'sample_files'
        if not samples.exists(): self.skipTest('Local sample files unavailable')
        with tempfile.TemporaryDirectory() as temp:
            db=connect(Path(temp)/'samples.sqlite3')
            try:
                for path in sorted(samples.iterdir()):
                    kind='payments' if path.suffix=='.zip' else 'orders' if path.suffix=='.csv' else 'products'
                    parsed=parse_file(path.name,path.read_bytes(),kind,'0001-01-01')
                    commit_file(db,parsed);commit_file(db,parsed)
                r=report(db,start='2026-07-01',end='2026-07-31',as_of='2026-09-16',mode='custom')
                self.assertEqual(len(r['orders']),3828)
                self.assertEqual(r['summary']['ads'],0)
                self.assertEqual(report(db,as_of='2026-09-16')['summary']['ads'],0)
                self.assertEqual(db.execute('SELECT SUM(cost) FROM ads').fetchone()[0],3616)
                self.assertEqual(r['summary']['packaging'],0)
                self.assertIsInstance(r['summary']['net_profit'],int)
                self.assertTrue(r['summary']['provisional'])
                expected=db.execute("SELECT SUM(settlement) FROM payments WHERE sub_order_id IN (SELECT sub_order_id FROM order_events WHERE status!='CANCELLED') AND sub_order_id NOT IN (SELECT sub_order_id FROM order_events WHERE status='CANCELLED')").fetchone()[0]
                self.assertEqual(r['summary']['settlement'],expected)
                self.assertGreater(r['summary']['statuses']['RETURN'],0)
            finally: db.close()


class HTTPTests(unittest.TestCase):
    def test_api_validation_and_transaction_rollback(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'api.sqlite3'
            server=make_server(path,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            url=f'http://127.0.0.1:{server.server_port}'
            try:
                with urlopen(url+'/api/report') as response:
                    self.assertEqual(response.status,200)
                request=Request(url+'/api/settings',data=json.dumps({'gst_rate':'-1','include_gst':True}).encode(),headers={'Content-Type':'application/json'})
                with self.assertRaises(HTTPError) as error:urlopen(request)
                self.assertEqual(error.exception.code,400)
                request=Request(url+'/api/settings',data=b'{}',headers={'Content-Type':'application/json','Origin':'https://example.com'})
                with self.assertRaises(HTTPError) as error:urlopen(request)
                self.assertEqual(error.exception.code,403)
                with urlopen(url+'/') as response:self.assertIn(b'Meesho Ledger',response.read())
                def post(path, payload):
                    request=Request(url+path,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
                    with urlopen(request) as response:return json.load(response)
                csv_bytes=b'Sub Order No,Order Date,SKU,Quantity,Reason for Credit Entry\n999999999999999999_1,2026-07-01,Test SKU,1,DELIVERED\n'
                upload={'name':'test.csv','kind':'orders','payload':base64.b64encode(csv_bytes).decode(),'effective_date':'0001-01-01'}
                preview=post('/api/import/preview',upload)
                self.assertEqual(preview['counts']['order_events'],1)
                post('/api/import/commit',{'tokens':[preview['token']]})
                again=post('/api/import/preview',upload)
                self.assertTrue(again['already_imported'])
                post('/api/products',{'effective_date':'0001-01-01','updates':[{'sku':'Test SKU','cost':'100','packaging':'5'}]})
                with self.assertRaises(HTTPError) as failed:
                    post('/api/products',{'effective_date':'0001-01-01','updates':[{'sku':'Test SKU','cost':'200'},{'sku':'does not exist','cost':'1'}]})
                failed.exception.close()
                with urlopen(url+'/api/products?date=2026-07-01') as response:
                    self.assertEqual(json.load(response)[0]['cost'],10000)
                post('/api/products/create',dict(sku='Manual SKU',cost='45',packaging='3',effective_date='2026-07-01'))
                with self.assertRaises(HTTPError):
                    post('/api/products/create',dict(sku='Manual SKU',cost='50',packaging='3',effective_date='2026-07-01'))
                with self.assertRaises(HTTPError):
                    post('/api/products/create',dict(sku='Invalid SKU',cost='-5',packaging='3',effective_date='2026-07-01'))
                with urlopen(url+'/api/products?date=2026-07-01') as response:
                    values=json.load(response)
                    self.assertEqual(len(values),2)
                    self.assertEqual(next(v for v in values if v['sku']=='manual sku')['cost'],4500)
                with urlopen(url+'/api/report?mode=custom&start=2026-07-01&end=2026-07-31&as_of=2026-08-31&skus=test%20sku&skus=manual%20sku') as response:
                    filtered=json.load(response)
                    self.assertEqual(filtered['filters']['skus'],['manual sku','test sku'])
                    self.assertEqual(len(filtered['orders']),1)
                with urlopen(url+'/api/backup') as response:
                    self.assertTrue(response.read().startswith(b'SQLite format 3'))
                with urlopen(url+'/api/export?start=2026-07-01&end=2026-07-31&mode=custom&as_of=2026-08-31') as response:
                    self.assertIn(b'999999999999999999_1',response.read())
            finally:
                server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
