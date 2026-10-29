import json
from pathlib import Path
import tempfile
import unittest

from recon.database import connect
from recon.engine import products, report, save_products, set_product_disabled
from recon.importer import commit_file, digest, parse_file


class ImportUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=connect(Path(self.temp.name)/'test.sqlite3')

    def tearDown(self):
        self.db.close();self.temp.cleanup()

    def orders(self, state='DELIVERED', qty=1, sku='Test SKU', when='2026-07-01'):
        data=f'Sub Order No,Order Date,SKU,Quantity,Reason for Credit Entry\norder1,{when},{sku},{qty},{state}\n'.encode()
        return parse_file('orders.csv',data,'orders','0001-01-01')

    def payment(self, amount=10000, txn='T1', status='DELIVERED', sku='Test SKU'):
        record=dict(sub_order_id='order1',order_date='2026-07-01',payment_date='2026-07-25',transaction_id=txn,status=status,sku=sku,
                    settlement=amount,shipping=0,reverse_shipping=0,tds=0,tcs=0,sale=amount,sale_return=0)
        fp=digest(record)
        return dict(name='payments.xlsx',digest=fp,kind='payments',payload=fp.encode(),rows=[],counts={'payments':1},warnings=[],records=[('payments',fp,record,'{}')])

    def result(self):
        return report(self.db,start='2026-07-01',end='2026-07-31',as_of='2026-09-18',mode='custom')

    def test_orders_latest_upload_replaces_status_quantity_and_date(self):
        commit_file(self.db,self.orders('CANCELLED'))
        r=commit_file(self.db,self.orders('DELIVERED',2))
        self.assertEqual(r['updated_records'],1)
        self.assertEqual(self.result()['summary']['cancelled'],0)
        self.assertEqual(self.result()['orders'][0]['quantity'],2)
        commit_file(self.db,self.orders('DELIVERED',2,when='2026-08-01'))
        self.assertEqual(self.result()['summary']['orders'],0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM order_current').fetchone()[0],1)

    def test_identical_file_is_reapplied_not_skipped(self):
        original=self.orders('DELIVERED')
        commit_file(self.db,original)
        commit_file(self.db,self.orders('CANCELLED'))
        r=commit_file(self.db,original)
        self.assertTrue(r['already_imported'])
        self.assertEqual(r['updated_records'],1)
        self.assertEqual(self.result()['orders'][0]['status'],'DELIVERED')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM import_runs').fetchone()[0],3)

    def test_revised_payment_updates_amount_and_preserves_distinct_transactions(self):
        commit_file(self.db,self.orders())
        commit_file(self.db,self.payment())
        commit_file(self.db,self.payment(12000))
        commit_file(self.db,self.payment(-2000,txn='T2',status='RETURN'))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM payments').fetchone()[0],2)
        self.assertEqual(self.result()['summary']['settlement'],10000)
        commit_file(self.db,self.payment(12000))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM payments').fetchone()[0],2)

    def test_missing_transaction_id_is_enriched_without_double_counting(self):
        commit_file(self.db,self.orders())
        blank=self.payment(txn='')
        known=self.payment(txn='BANK1')
        commit_file(self.db,blank)
        commit_file(self.db,known)
        self.assertEqual(self.result()['summary']['settlement'],10000)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM payments').fetchone()[0],1)
        commit_file(self.db,blank)
        self.assertEqual(self.db.execute('SELECT transaction_id FROM payments').fetchone()[0],'BANK1')
        self.assertEqual(self.result()['orders'][0]['payment_count'],1)

    def test_existing_unidentified_duplicate_is_excluded_from_report(self):
        commit_file(self.db,self.orders())
        commit_file(self.db,self.payment(txn='BANK1'))
        self.db.execute("INSERT INTO payments SELECT 'legacy',sub_order_id,order_date,payment_date,'',status,settlement,shipping,reverse_shipping,tds,tcs,sale,sale_return,sku,import_id,raw FROM payments")
        self.assertEqual(self.result()['summary']['settlement'],10000)
        self.assertEqual(self.result()['orders'][0]['payment_count'],1)
        # A second real bank transaction must stay distinct; the unknown row
        # can no longer be uniquely identified, so it must also be retained.
        commit_file(self.db,self.payment(txn='BANK2'))
        self.assertEqual(self.result()['summary']['settlement'],30000)

    def test_unknown_adjustment_with_different_amount_is_retained(self):
        commit_file(self.db,self.orders())
        commit_file(self.db,self.payment(txn='BANK1'))
        commit_file(self.db,self.payment(-2000,txn='',status='RETURN'))
        self.assertEqual(self.result()['summary']['settlement'],8000)

    def test_payment_only_sku_discovered_missing_cost_and_disabled_preserved(self):
        commit_file(self.db,self.payment(sku='Payment Only'))
        p=products(self.db,'2026-09-18')[0]
        self.assertEqual(p['display_sku'],'Payment Only')
        self.assertIsNone(p['cost'])
        set_product_disabled(self.db,'Payment Only',True)
        commit_file(self.db,self.payment(12000,sku='Payment Only'))
        self.assertEqual(products(self.db,'2026-09-18')[0]['disabled'],1)
        set_product_disabled(self.db,'Payment Only',False)
        self.assertEqual(products(self.db,'2026-09-18')[0]['disabled'],0)

    def test_disabling_keeps_historical_profit(self):
        commit_file(self.db,self.orders());commit_file(self.db,self.payment())
        save_products(self.db,[dict(sku='Test SKU',cost='50',packaging='5')],'2026-07-01')
        before=self.result()['summary']
        set_product_disabled(self.db,'Test SKU',True)
        self.assertEqual(self.result()['summary'],before)

    def test_revised_ads_updates_campaign_period_once(self):
        def batch(amount):
            record=dict(spend_start='2026-07-01',spend_end='2026-07-01',deduction_date='2026-07-25',campaign='A',cost=amount)
            fp=digest(record)
            return dict(name='ads.xlsx',digest=fp,kind='payments',payload=fp.encode(),rows=[],counts={'ads':1},warnings=[],records=[('ads',fp,record,'{}')])
        commit_file(self.db,batch(500));commit_file(self.db,batch(700))
        self.assertEqual(self.result()['summary']['ads'],700)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM ads').fetchone()[0],1)
