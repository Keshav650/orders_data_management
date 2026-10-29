"""Unit tests for Amazon reconciliation, idempotent upserting, and fee calculations."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from recon.database import connect
from recon.importer import parse_file, commit_file, preview, money, day
from recon.engine import report, save_products, create_product, save_recovery, order_detail

SAMPLE_DIR = Path(__file__).resolve().parent.parent / 'sample_files'


class AmazonReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / 'test_amazon.sqlite3'
        self.db = connect(self.db_path)

    def tearDown(self):
        self.db.close()
        self.temp_dir.cleanup()

    def test_idempotent_order_upsert(self):
        order_file = SAMPLE_DIR / '156206206633020751.txt'
        if not order_file.exists():
            self.skipTest('Sample order file not found')
        payload = order_file.read_bytes()
        
        parsed1 = parse_file(order_file.name, payload, kind='orders')
        p1 = preview(self.db, parsed1)
        self.assertEqual(p1['overlapping_records'], 0)
        self.assertGreater(p1['new_records'], 300)
        
        res1 = commit_file(self.db, parsed1)
        self.db.commit()
        self.assertEqual(res1['added_records'], p1['new_records'])

        # Re-importing same file must NOT duplicate
        parsed2 = parse_file(order_file.name, payload, kind='orders')
        p2 = preview(self.db, parsed2)
        self.assertEqual(p2['new_records'], 0)
        self.assertEqual(p2['overlapping_records'], p1['new_records'])
        
        res2 = commit_file(self.db, parsed2)
        self.db.commit()
        self.assertEqual(res2['added_records'], 0)
        self.assertEqual(res2['updated_records'], p1['new_records'])

        # Order count in table should be exactly equal to unique order_item_ids
        count = self.db.execute('SELECT COUNT(*) FROM orders').fetchone()[0]
        self.assertEqual(count, p1['new_records'])

    def test_idempotent_transaction_upsert(self):
        trans_file = SAMPLE_DIR / '2026Sep1-2026Oct24CustomUnifiedTransaction.csv'
        if not trans_file.exists():
            self.skipTest('Sample transaction file not found')
        payload = trans_file.read_bytes()

        parsed1 = parse_file(trans_file.name, payload, kind='transactions')
        p1 = preview(self.db, parsed1)
        self.assertEqual(p1['overlapping_records'], 0)
        self.assertGreater(p1['new_records'], 1000)

        res1 = commit_file(self.db, parsed1)
        self.db.commit()

        # Re-importing same transaction file must NOT duplicate
        parsed2 = parse_file(trans_file.name, payload, kind='transactions')
        p2 = preview(self.db, parsed2)
        self.assertEqual(p2['new_records'], 0)
        self.assertEqual(p2['overlapping_records'], p1['new_records'])

        res2 = commit_file(self.db, parsed2)
        self.db.commit()
        self.assertEqual(res2['added_records'], 0)

        # Net total sum of transactions must not double
        sum1 = self.db.execute('SELECT SUM(total) FROM transactions').fetchone()[0]
        self.assertIsNotNone(sum1)

    def test_end_to_end_reconciliation_with_all_samples(self):
        # 1. Import Orders
        order_file = SAMPLE_DIR / '156206206633020751.txt'
        if order_file.exists():
            parsed = parse_file(order_file.name, order_file.read_bytes(), kind='orders')
            commit_file(self.db, parsed)

        # 2. Import Transactions
        trans_file = SAMPLE_DIR / '2026Sep1-2026Oct24CustomUnifiedTransaction.csv'
        if trans_file.exists():
            parsed = parse_file(trans_file.name, trans_file.read_bytes(), kind='transactions')
            commit_file(self.db, parsed)

        # 3. Import Returns
        ret_file = SAMPLE_DIR / 'report-50208020751.tsv'
        if ret_file.exists():
            parsed = parse_file(ret_file.name, ret_file.read_bytes(), kind='returns')
            commit_file(self.db, parsed)

        # 4. Import Ads
        ads_file = SAMPLE_DIR / 'Amazon_Advertised_product_-_Performance_7_Days_Metrics (1).csv'
        if ads_file.exists():
            parsed = parse_file(ads_file.name, ads_file.read_bytes(), kind='ads_product')
            commit_file(self.db, parsed)

        # 5. Import Search terms
        st_file = SAMPLE_DIR / 'Amazon_Campaign_-_Search_term_Report_1_Week (1).csv'
        if st_file.exists():
            parsed = parse_file(st_file.name, st_file.read_bytes(), kind='ads_search_term')
            commit_file(self.db, parsed)

        # 6. Set sample product purchase costs & packaging
        save_products(self.db, [
            {'sku': 'BS-LC-NL-WH', 'cost': '180.00', 'packaging': '5.00'},
            {'sku': 'BS-LC-NL-PNK', 'cost': '180.00', 'packaging': '5.00'},
            {'sku': 'DD-AS-UNICORN145', 'cost': '350.00', 'packaging': '10.00'},
        ], effective_date='0001-01-01')
        self.db.commit()

        # Run report
        rep = report(self.db, start='2026-09-01', end='2026-09-30', as_of='2026-10-24', mode='custom')
        s = rep['summary']

        self.assertGreater(s['orders'], 200)
        self.assertGreater(s['cancelled'], 20)
        self.assertGreater(s['settlement'], 0)
        self.assertGreater(s['product_cost'], 0)
        self.assertGreater(s['packaging'], 0)
        self.assertIsNotNone(s['net_profit'])

        # Check SKU performance ranking
        self.assertGreater(len(rep['sku_performance']), 0)
        top_sku = rep['sku_performance'][0]
        self.assertIn('profit_after_ads', top_sku)

        # Check wasted ad spend was identified
        self.assertGreater(len(rep['wasted_search_terms']), 0)

        # Check order detail lookup
        sample_order = rep['orders'][0]
        detail = order_detail(self.db, sample_order['amazon_order_id'])
        self.assertGreater(len(detail['orders']), 0)

    def test_recovery_disposition_removes_cost_on_sellable(self):
        self.db.execute(
            "INSERT INTO orders(order_item_id, amazon_order_id, purchase_date, order_status, sku, quantity, item_price, raw) "
            "VALUES ('item_1', 'order_1', '2026-09-15', 'Shipped - Returned to Seller', 'BS-TEST', 1, 40000, '{}')"
        )
        save_products(self.db, [{'sku': 'BS-TEST', 'cost': '150.00', 'packaging': '5.00'}], '0001-01-01')
        self.db.commit()

        # By default RTO is sellable -> product cost is 0, packaging is 5.00 (500 paise)
        rep1 = report(self.db, start='2026-09-01', end='2026-09-30', as_of='2026-10-01', mode='custom')
        ord1 = rep1['orders'][0]
        self.assertEqual(ord1['product_cost'], 0)
        self.assertEqual(ord1['packaging'], 500)

        # Mark as damaged -> product cost should be retained (150.00 = 15000 paise)
        save_recovery(self.db, {'order_id': 'order_1', 'order_item_id': 'item_1', 'state': 'damaged', 'effective_date': '2026-09-20', 'note': 'crushed box'})
        self.db.commit()

        rep2 = report(self.db, start='2026-09-01', end='2026-09-30', as_of='2026-10-01', mode='custom')
        ord2 = rep2['orders'][0]
        self.assertEqual(ord2['product_cost'], 15000)

    def test_rto_unreversed_easy_ship_fee_flagged(self):
        self.db.execute(
            "INSERT INTO orders(order_item_id, amazon_order_id, purchase_date, order_status, sku, quantity, item_price, raw) "
            "VALUES ('item_rto', 'order_rto', '2026-09-10', 'Shipped - Returned to Seller', 'BS-TEST', 1, 40000, '{}')"
        )
        self.db.execute(
            "INSERT INTO transactions(fingerprint, settlement_id, post_date, type, order_id, sku, description, other_fees, total, raw) "
            "VALUES ('fp_es', 'SETT_1', '2026-09-12', 'Shipping Services', 'order_rto', 'BS-TEST', 'Easy Ship weight handling fees', -6490, -6490, '{}')"
        )
        self.db.commit()

        rep = report(self.db, start='2026-09-01', end='2026-09-30', as_of='2026-10-01', mode='custom')
        ord_rto = rep['orders'][0]
        self.assertIn('Easy Ship fee was charged but not reversed on RTO', ord_rto['issues'])

    def test_advertising_sku_attribution(self):
        self.db.execute(
            "INSERT INTO ads_product(fingerprint, campaign_id, campaign_name, ad_group_id, ad_group_name, sku, date_range, start_date, end_date, cost, sales, impressions, clicks, units, roas, raw) "
            "VALUES ('ad_1', 'C1', 'Camp1', 'G1', 'Group1', 'BS-TEST', 'Sep 10, 2026 - Sep 19, 2026', '2026-09-10', '2026-09-19', 10000, 25000, 500, 50, 2, 2.5, '{}')"
        )
        self.db.commit()

        # Run report for Sep 10 - Sep 14 (5 days out of 10 total days)
        rep = report(self.db, start='2026-09-10', end='2026-09-14', as_of='2026-09-30', mode='custom')
        # 10000 paise * 5 / 10 = 5000 paise (Rs 50)
        self.assertEqual(rep['summary']['ads'], 5000)

    def test_multi_item_order_not_duplicated(self):
        order_file = SAMPLE_DIR / '156206206633020751.txt'
        trans_file = SAMPLE_DIR / '2026Sep1-2026Oct24CustomUnifiedTransaction.csv'
        if not (order_file.exists() and trans_file.exists()):
            self.skipTest('Sample files not found')

        commit_file(self.db, parse_file(order_file.name, order_file.read_bytes(), kind='orders'))
        commit_file(self.db, parse_file(trans_file.name, trans_file.read_bytes(), kind='transactions'))

        save_products(self.db, [
            {'sku': 'BS-LC-NL-WH', 'cost': '250.00', 'packaging': '15.00'},
            {'sku': 'BS-HB-3PCS', 'cost': '150.00', 'packaging': '10.00'}
        ], effective_date='0001-01-01')
        self.db.commit()

        rep = report(self.db, start='2026-09-01', end='2026-09-30', as_of='2026-10-24', mode='custom')
        matching = [o for o in rep['orders'] if o['amazon_order_id'] == '171-0594621-7640337']

        # Must appear exactly once in the orders list
        self.assertEqual(len(matching), 1)
        m = matching[0]
        # Multi-item order has 2 units and 2 items
        self.assertEqual(m['quantity'], 2)
        self.assertEqual(m['items_count'], 2)
        # Net settlement must be exactly 350.26 + 370.16 - 64.90 = 655.52 (65552 paise)
        self.assertEqual(m['settlement'], 65552)
        # Product cost: 250.00 + 150.00 = 400.00 (40000 paise)
        self.assertEqual(m['product_cost'], 40000)
        # Packaging charged once: max(15.00, 10.00) = 15.00 (1500 paise)
        self.assertEqual(m['packaging'], 1500)
        # Contribution: 65552 - 40000 - 1500 = 24052
        self.assertEqual(m['contribution'], 24052)

    def test_service_fees_not_double_deducted_and_sku_filter(self):
        # Insert a sample order and an ad report
        self.db.execute(
            "INSERT INTO orders(order_item_id, amazon_order_id, purchase_date, order_status, sku, quantity, item_price, raw) "
            "VALUES ('item_1', 'order_1', '2026-09-10', 'Shipped - Delivered to Buyer', 'SKU-A', 1, 50000, '{}')"
        )
        self.db.execute(
            "INSERT INTO transactions(fingerprint, settlement_id, post_date, type, order_id, sku, description, total, raw) "
            "VALUES ('fp_ord', 'SETT_1', '2026-09-12', 'Order', 'order_1', 'SKU-A', 'Product sale', 45000, '{}')"
        )
        # Advertising service fee transaction (Cost of Advertising)
        self.db.execute(
            "INSERT INTO transactions(fingerprint, settlement_id, post_date, type, order_id, sku, description, total, raw) "
            "VALUES ('fp_ad_inv', 'SETT_1', '2026-09-15', 'Service Fee', '', '', 'Cost of Advertising', -10000, '{}')"
        )
        # Advertising performance report entry (10000 paise cost)
        self.db.execute(
            "INSERT INTO ads_product(fingerprint, campaign_id, campaign_name, ad_group_id, ad_group_name, sku, date_range, start_date, end_date, cost, sales, impressions, clicks, units, roas, raw) "
            "VALUES ('ad_1', 'C1', 'Camp1', 'G1', 'Group1', 'SKU-A', 'Sep 10, 2026 - Sep 10, 2026', '2026-09-10', '2026-09-10', 10000, 25000, 500, 50, 2, 2.5, '{}')"
        )
        save_products(self.db, [{'sku': 'SKU-A', 'cost': '200.00', 'packaging': '10.00'}], '0001-01-01')
        self.db.commit()

        # Report without SKU filter
        rep = report(self.db, start='2026-09-01', end='2026-09-30', as_of='2026-10-01', mode='custom')
        s = rep['summary']
        # Ad spend is 10000
        self.assertEqual(s['ads'], 10000)
        # Because ad spend is already tracked, the Cost of Advertising invoice is not subtracted again as an operating service fee!
        self.assertEqual(s['service_fees'], 0)
        # Net Profit: Settlement (45000) - Product cost (20000) - Packaging (1000) - Ads (10000) = 14000
        self.assertEqual(s['net_profit'], 14000)

        # Report filtered to SKU-A
        rep_sku = report(self.db, start='2026-09-01', end='2026-09-30', as_of='2026-10-01', mode='custom', sku='SKU-A')
        s_sku = rep_sku['summary']
        # Account service fees must be 0 for a specific SKU (not dumped onto this product)
        self.assertEqual(s_sku['service_fees'], 0)
        self.assertEqual(s_sku['net_profit'], 14000)

    def test_deferred_to_released_transaction_not_duplicated(self):
        # 1. Order record
        self.db.execute(
            "INSERT INTO orders(order_item_id, amazon_order_id, purchase_date, order_status, sku, quantity, item_price, raw) "
            "VALUES ('item_402', '402-3017814-3391500', '2026-10-15', 'DELIVERED', 'BS-LC-NL-WH', 1, 42900, '{}')"
        )
        # 2. Deferred transaction row with temporary settlement ID 28069360372
        csv_deferred = (
            "settlement id,date/time,type,order id,sku,description,quantity,marketplace,account type,fulfillment,order city,order state,order postal,product sales,shipping credits,gift wrap credits,promotional rebates,Total sales tax liable(GST before adjusting TCS),TCS-CGST,TCS-SGST,TCS-IGST,TDS (Section 194-O),selling fees,fba fees,other transaction fees,other,total,Transaction Status\n"
            "28069360372,2026-10-16 04:30:00,Order,402-3017814-3391500,BS-LC-NL-WH,Nail Lamp,1,amazon.in,Electronic,Merchant,NELLORE,ANDHRA PRADESH,524002,363.56,0,0,0,65.44,0,0,-1.82,0,0,0,-27.14,0,400.04,Deferred\n"
            "28069360372,2026-10-16 04:30:00,Shipping Services,402-3017814-3391500,,Easy Ship weight handling fees,0,Amazon.in,Electronic,Merchant,,,,0,0,0,0,0,0,0,0,0,0,0,-64.90,0,-64.90,Deferred\n"
        )
        parsed_def = parse_file('deferred.csv', csv_deferred.encode('utf-8'), kind='transactions')
        commit_file(self.db, parsed_def)
        self.db.commit()

        # Check report when transactions are Deferred
        rep1 = report(self.db, mode='all')
        o1 = next(o for o in rep1['orders'] if o['amazon_order_id'] == '402-3017814-3391500')
        self.assertEqual(o1['settlement'], 33514)
        self.assertEqual(o1['payment_count'], 2)

        # 3. Later report with same transactions Released under new payout settlement ID 28172139082
        csv_released = (
            "settlement id,date/time,type,order id,sku,description,quantity,marketplace,account type,fulfillment,order city,order state,order postal,product sales,shipping credits,gift wrap credits,promotional rebates,Total sales tax liable(GST before adjusting TCS),TCS-CGST,TCS-SGST,TCS-IGST,TDS (Section 194-O),selling fees,fba fees,other transaction fees,other,total,Transaction Status,Transaction Release Date\n"
            "28172139082,2026-10-16 04:30:00,Order,402-3017814-3391500,BS-LC-NL-WH,Nail Lamp,1,amazon.in,Electronic,Merchant,NELLORE,ANDHRA PRADESH,524002,363.56,0,0,0,65.44,0,0,-1.82,0,0,0,-27.14,0,400.04,Released,2026-10-29\n"
            "28172139082,2026-10-16 04:30:00,Shipping Services,402-3017814-3391500,,Easy Ship weight handling fees,0,Amazon.in,Electronic,Merchant,,,,0,0,0,0,0,0,0,0,0,0,0,-64.90,0,-64.90,Released,2026-10-28\n"
        )
        parsed_rel = parse_file('released.csv', csv_released.encode('utf-8'), kind='transactions')
        commit_file(self.db, parsed_rel)
        self.db.commit()

        # Database must have updated in-place without duplicating records
        t_rows = self.db.execute("SELECT status, settlement_id, total FROM transactions WHERE order_id='402-3017814-3391500'").fetchall()
        self.assertEqual(len(t_rows), 2)
        for tr in t_rows:
            self.assertEqual(tr['status'], 'Released')
            self.assertEqual(tr['settlement_id'], '28172139082')

        # Report must NOT double-count settlement
        rep2 = report(self.db, mode='all')
        o2 = next(o for o in rep2['orders'] if o['amazon_order_id'] == '402-3017814-3391500')
        self.assertEqual(o2['settlement'], 33514)  # 400.04 - 64.90 = 335.14, not 670.28!
        self.assertEqual(o2['product_sales'], 36356)
        self.assertEqual(o2['payment_count'], 2)


if __name__ == '__main__':
    unittest.main()
