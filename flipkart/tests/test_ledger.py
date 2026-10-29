import csv
import io
import json
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request,build_opener,ProxyHandler

from core import connect,parse_file,commit_file,report,save_product,save_recovery,save_manual_return,money,rupees
from app import make_server

ROOT=Path(__file__).resolve().parents[1]
ORDER_HEADERS=['order_item_id','order_id','order_date','order_item_status','sku','product_title','quantity','order_delivery_date','order_return_approval_date']
PAY_HEADERS=['NEFT ID','Payment Date','Bank Settlement Value (Rs.)','Order item ID','Seller SKU','Order Date','Sale Amount (Rs.)','Refund (Rs.)','Protection Fund (Rs.)','Return Type','Item Return Status']

def csv_file(headers,rows):
    out=io.StringIO(newline='')
    writer=csv.writer(out)
    writer.writerow(headers)
    writer.writerows(rows)
    return out.getvalue().encode()

class LedgerTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'data'/'test-temp').mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'data'/'test-temp')
        self.db=connect(Path(self.temp.name)/'test.sqlite3')

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def order(self,iid='100',quantity=1,status='DELIVERED',ordered='2026-07-01',return_on=''):
        payload=csv_file(ORDER_HEADERS,[[f'OI:{iid}',f'OD{iid}',ordered,status,'"""SKU:A"""','Title',quantity,'2026-07-05',return_on]])
        return commit_file(self.db,parse_file('order.csv',payload),payload)

    def payment(self,net='120.123',paid='2026-07-10',sale='150',refund='0',rt='',neft='N1',iid='100',copies=1):
        row=[neft,paid,net,iid,'A','2026-07-01',sale,refund,'0',rt,'Product Delivered' if rt else 'NA']
        payload=csv_file(PAY_HEADERS,[row]*copies)
        return commit_file(self.db,parse_file('pay.csv',payload),payload)

    def cost(self,cost='40',packaging='5',effective='0001-01-01',policy='non_returnable',days=''):
        save_product(self.db,dict(sku='A',cost=cost,packaging=packaging,effective_on=effective,policy=policy,return_days=days))

    def result(self,on='2026-08-01'):
        return report(self.db,as_of=on)['orders'][0]

    def test_sku_totals_count_units_once_and_keep_unresolved_profit_separate(self):
        self.order(iid='100',quantity=2);self.payment(iid='100');self.cost()
        self.order(iid='200',quantity=3,status='RETURNED');self.payment(iid='200',rt='Logistics Return',net='-10',refund='-150')
        self.order(iid='300',quantity=4,status='RETURNED');self.payment(iid='300',rt='Customer Return',net='-20',refund='-150')
        self.order(iid='400',quantity=5,status='RETURNED')
        self.order(iid='500',quantity=6,status='CANCELLED')
        self.order(iid='600',quantity=7,status='APPROVED')
        report_data=report(self.db,as_of='2026-08-01')
        s=report_data['sku_totals'][0]
        self.assertEqual((s['units'],s['processed_units'],s['delivered_units'],s['rto_units'],s['rvp_units']),(27,14,2,3,4))
        self.assertEqual((s['unclassified_units'],s['cancelled_units'],s['other_units']),(5,6,7))
        self.assertEqual(s['received'],report_data['totals']['received'])
        self.assertEqual(s['profit'],money('35.123')-money('15')-money('25'))
        self.assertEqual(s['provisional'],s['profit'])
        self.assertEqual((s['recognized_orders'],s['review_orders'],s['unpaid_orders']),(3,0,2))
        self.assertEqual(s['profit_per_unit'],s['profit']/9)
        save_recovery(self.db,dict(item_id='200',state='sellable',quantity=2,effective_on='2026-07-15'))
        s=report(self.db,as_of='2026-08-01')['sku_totals'][0]
        self.assertEqual(s['recovered_units'],2)
        self.assertEqual(s['rto_units'],3)
        self.assertEqual(report(self.db,as_of='2026-07-09')['sku_totals'][0]['profit'],None)
        self.assertEqual(report(self.db,start='2026-08-01',as_of='2026-08-02')['sku_totals'],[])
        self.assertEqual(report(self.db,sku='missing')['sku_totals'],[])

    def test_sku_unknown_costs_do_not_become_zero_profit(self):
        self.order();self.payment()
        s=report(self.db,as_of='2026-08-01')['sku_totals'][0]
        self.assertIsNone(s['profit'])
        self.assertIsNone(s['provisional'])
        self.assertIsNone(s['margin'])
        self.assertEqual(s['missing_cost_orders'],1)

    def test_tracking_ids_are_exposed_in_orders_and_settlements(self):
        headers=ORDER_HEADERS+['delivery_tracking_id','return_id']
        payload=csv_file(headers,[['100','OD100','2026-07-01','DELIVERED','A','Title',1,'2026-07-05','','DTr:FMPC123456','R:123']])
        commit_file(self.db,parse_file('tracking.csv',payload),payload)
        self.payment();self.cost()
        r=report(self.db,as_of='2026-08-01')
        for row in [r['orders'][0],r['settlements'][0]]:
            self.assertEqual(row['forward_tracking_id'],'FMPC123456')
            self.assertEqual(row['reverse_tracking_id'],'')
            self.assertEqual(row['order_id'],'OD100')
        from core import tracking_details
        self.assertEqual(tracking_details({'1: delivery_tracking_id':'DTr:FMPC123','2: Tracking ID':'FMPC456','3: reverse_tracking_id':'RTr:FMPR789'}),
                         dict(forward_tracking_id='FMPC456',reverse_tracking_id='FMPR789'))
        # Settlements outside the selected order period still carry their shipment IDs.
        self.assertEqual(report(self.db,start='2026-08-01',as_of='2026-08-02')['settlements'][0]['forward_tracking_id'],'FMPC123456')

    def test_new_fee_heading_and_review_controls(self):
        from core import save_payment_review
        self.order();self.cost();self.split_payout()
        for row in self.db.execute('SELECT identity,components FROM transactions').fetchall():
            c=json.loads(row['components']);c['marketplacefeerssumuah']=c.pop('marketplacefeerssumvai')
            self.db.execute('UPDATE transactions SET components=? WHERE identity=?',(json.dumps(c),row['identity']))
        self.db.commit()
        r=self.result();self.assertEqual(r['state'],'Recognized')
        for status,expected in [('review','Review'),('confirmed','Recognized'),('auto','Recognized')]:
            save_payment_review(self.db,dict(item_id='100',status=status,fingerprint=r['payment_fingerprint'],as_of='2026-08-01'))
            self.assertEqual(self.result()['state'],expected)
        self.cost(cost='')
        self.assertIn('Purchase cost missing',self.result()['issues'])
        self.assertIsNone(self.result()['profit'])
        self.payment(net='5',sale='0',paid='2026-07-12',neft='NEW')
        self.assertFalse(self.result()['payment_review_applies'])
        with self.assertRaises(ValueError):
            save_payment_review(self.db,dict(item_id='100',status='confirmed',fingerprint=r['payment_fingerprint'],as_of='2026-08-01'))

    def test_confirmation_resolves_payment_rows_without_changing_money(self):
        from core import save_payment_review
        self.order();self.cost();self.split_payout(offer_net='10')
        r=self.result();self.assertEqual(r['state'],'Review')
        save_payment_review(self.db,dict(item_id='100',status='confirmed',fingerprint=r['payment_fingerprint'],as_of='2026-08-01'))
        self.assertEqual(self.result()['state'],'Recognized')
        self.assertEqual(self.result()['net'],r['net'])
        self.assertEqual(self.result()['profit'],r['provisional'])

    def test_delivered_correction_restores_cost_and_can_be_undone(self):
        from core import save_status_correction
        self.order(status='RETURN_REQUESTED');self.cost();self.payment()
        before=self.result();self.assertIn('Return still requested',before['issues'])
        v=dict(item_id='100',status='DELIVERED',effective_on='2026-07-15',delivered_on='2026-07-05',notes='Return cancelled; customer kept goods',source_import_id=before['source_import_id'])
        save_status_correction(self.db,v)
        r=self.result();self.assertEqual(r['reported_status'],'RETURN_REQUESTED')
        self.assertEqual(r['status'],'DELIVERED');self.assertFalse(r['returned'])
        self.assertEqual(r['purchase_total'],money('40'))
        self.assertEqual(r['profit'],money('75.123'))
        self.assertEqual(self.db.execute('select status from fk_orders').fetchone()[0],'RETURN_REQUESTED')
        self.assertEqual(self.result('2026-07-14')['status'],'RETURN_REQUESTED')
        save_status_correction(self.db,v|dict(status='',effective_on='2026-07-16'))
        self.assertEqual(self.result()['status'],'RETURN_REQUESTED')
        save_status_correction(self.db,v|dict(effective_on='2026-07-17'))
        self.payment(net='-20',sale='0',refund='-150',rt='Customer Return',paid='2026-07-20',neft='N2')
        r=self.result();self.assertTrue(r['returned'])
        self.assertIn('Delivered correction conflicts with return settlement',r['issues'])
        self.assertIsNone(r['profit'])
        with self.assertRaises(ValueError):save_status_correction(self.db,v|dict(notes=''))

    def test_new_order_report_supersedes_status_correction(self):
        from core import save_status_correction
        self.order(status='RETURN_REQUESTED');r=self.result()
        v=dict(item_id='100',status='DELIVERED',effective_on='2026-07-15',notes='Confirmed',source_import_id=r['source_import_id'])
        save_status_correction(self.db,v)
        self.order(status='RETURNED',return_on='2026-07-20')
        self.assertFalse(self.result()['status_correction_applies'])
        self.assertEqual(self.result()['status'],'RETURNED')
        with self.assertRaises(ValueError):save_status_correction(self.db,v)

    def test_prepaid_cancelled_refund_is_settled_without_stock_costs(self):
        self.order(status='CANCELLED');self.payment(net='150',sale='150')
        self.payment(net='-150',sale='0',refund='-150',paid='2026-07-10',neft='N1')
        r=self.result();self.assertTrue(r['cancellation_refunded'])
        self.assertEqual(r['state'],'Recognized');self.assertEqual(r['profit'],0)
        self.assertEqual(r['purchase_total'],0);self.assertEqual(r['packaging'],0)
        self.assertNotIn('Cancelled order has settlements',r['issues'])
        self.assertEqual(self.result('2026-07-09')['state'],'Excluded')
        self.payment(net='-20',sale='0',paid='2026-07-20',neft='FEE')
        self.assertEqual(self.result()['profit'],money('-20'))

    def test_cancelled_partial_refund_or_dispatched_order_still_reviewed(self):
        self.order(status='CANCELLED');self.payment(net='150',sale='150')
        self.payment(net='-100',sale='0',refund='-100',paid='2026-07-15',neft='N2')
        self.assertFalse(self.result()['cancellation_refunded'])
        self.assertEqual(self.result()['state'],'Review')
        self.payment(net='-50',sale='0',refund='-50',paid='2026-07-16',neft='N3')
        self.db.execute("UPDATE fk_orders SET dispatched_on='2026-07-05'");self.db.commit()
        self.assertFalse(self.result()['cancellation_refunded'])
        self.assertEqual(self.result()['state'],'Review')

    def split_payout(self,extra=False,refund='0',offer_net='9',sale_net='90'):
        headers=PAY_HEADERS+['Total Offer Amount (Rs.)','My share (Rs.)','Customer Addons Amount (Rs.)','Marketplace Fee (Rs.) (Sum V : AI)','Taxes (Rs.)','Offer Adjustments (Rs.)']
        sale=['N1','2026-07-10',sale_net,'100','A','2026-07-01','100','0','0','','NA','0','0','0','-10','0','0']
        offer=['N1','2026-07-10',offer_net,'100','A','2026-07-01','0',refund,'0','','NA','10','0','0','0','-1','0']
        payload=csv_file(headers,[sale,offer]+([offer] if extra else []))
        commit_file(self.db,parse_file('split.csv',payload),payload)

    def test_sale_offer_payout_is_settled_without_double_counting(self):
        self.order();self.cost();self.split_payout()
        r=self.result()
        self.assertEqual(r['state'],'Recognized')
        self.assertEqual(r['net'],money('99'))
        self.assertEqual(r['profit'],money('54'))
        self.assertTrue(r['payout_reviews'][0]['accepted'])
        self.assertEqual(self.result('2026-07-09')['state'],'Unpaid')
        self.cost(cost='')
        self.assertIn('Purchase cost missing',self.result()['issues'])
        self.assertEqual(self.result()['state'],'Review')

    def test_duplicate_offer_rows_are_counted_once(self):
        self.order();self.cost();self.split_payout(extra=True)
        r=self.result()
        self.assertNotIn('Multiple rows in one payout need review',r['issues'])
        self.assertTrue(r['payout_reviews'][0]['accepted'])
        self.assertEqual(r['profit'],money('54'))
        self.assertEqual(r['duplicate_payment_count'],1)

    def test_unbalanced_sale_offer_payout_still_needs_review(self):
        self.order();self.cost();self.split_payout(offer_net='10')
        self.assertIn('Multiple rows in one payout need review',self.result()['issues'])

    def test_refund_offer_pair_is_not_auto_accepted(self):
        self.order();self.cost();self.split_payout(refund='-1',offer_net='8')
        self.assertIn('Multiple rows in one payout need review',self.result()['issues'])

    def test_offers_are_informational_and_reversals_do_not_hide_application(self):
        self.order();self.payment();self.cost()
        before=self.result()
        def offer_row(paid,total,share,buyer_total='100',buyer_share='-50',net='0'):
            headers=PAY_HEADERS+['Total Offer Amount (Rs.)','My share (Rs.)','Offer Adjustments (Rs.)','Total Offer Amount','My Share']
            values=['offer-'+paid,paid,net,'100','A','2026-07-01','0','0','0','','NA',total,share,'0',buyer_total,buyer_share]
            payload=csv_file(headers,[values]);commit_file(self.db,parse_file('offer.csv',payload),payload)
        self.assertEqual(before['offer_status'],'Unknown')
        offer_row('2026-07-15','100','-50')
        current=self.result()
        for key in ('net','profit','provisional','state'):
            self.assertEqual(current[key],before[key])
        self.assertEqual((current['offer_total'],current['offer_my_share'],current['offer_flipkart_share']),(money('100'),money('-50'),money('50')))
        offer_row('2026-07-20','-100','50')
        current=self.result()
        self.assertEqual(current['offer_total'],0)
        self.assertEqual(current['offer_status'],'Applied')
        self.assertTrue(current['offer_seller_funded'])
        self.assertEqual(self.result('2026-07-16')['offer_total'],money('100'))
        self.assertEqual(report(self.db,as_of='2026-07-16')['sku_totals'][0]['offer_total'],money('100'))

    def test_buyer_offer_is_not_a_posted_amount_and_zero_is_distinct_from_unknown(self):
        self.order();self.cost()
        headers=PAY_HEADERS+['Total Offer Amount (Rs.)','My share (Rs.)','Total Offer Amount','My Share']
        row=['N1','2026-07-10','0','100','A','2026-07-01','0','0','0','','NA','0','0','100','-50']
        payload=csv_file(headers,[row]);commit_file(self.db,parse_file('offer.csv',payload),payload)
        result=self.result()
        self.assertEqual(result['offer_status'],'Applied')
        self.assertEqual(result['offer_total'],0)
        self.assertEqual(result['offer_my_share'],0)
        self.assertEqual(self.result('2026-07-09')['offer_status'],'Unknown')
        self.order(iid='200')
        row[0]='N2';row[3]='200';row[-2:]=['0','0']
        payload=csv_file(headers,[row]);commit_file(self.db,parse_file('none.csv',payload),payload)
        result=next(r for r in report(self.db,as_of='2026-08-01')['orders'] if r['item_id']=='200')
        self.assertEqual(result['offer_status'],'None')

    def test_inspected_rto_and_rvp_outcomes_and_claims(self):
        self.order(iid='100',quantity=4,status='RETURNED');self.payment(iid='100',rt='Logistics Return',net='-10');self.cost()
        self.order(iid='200',status='RETURNED');self.payment(iid='200',rt='Customer Return',net='-20')
        inspection=dict(item_id='100',sellable_units=1,damaged_units=1,missing_units=1,wrong_units=0,effective_on='2026-07-15',notes='Empty package',evidence='photo.jpg',claim_reference='C1',claim_status='Approved')
        save_recovery(self.db,inspection)
        r=report(self.db,as_of='2026-07-16');o=next(o for o in r['orders'] if o['item_id']=='100')
        self.assertEqual(o['awaiting_inspection_units'],1)
        self.assertEqual(o['profit'],money('-95'))
        self.assertEqual(o['provisional'],money('-95'))
        self.assertEqual(o['assumed_sellable_units'],1)
        self.assertEqual(r['return_totals']['RTO']['missing_units'],1)
        self.assertEqual(r['return_totals']['RTO']['loss_cost'],money('80'))
        self.assertEqual(r['return_totals']['RVP']['awaiting_units'],1)
        self.assertEqual(report(self.db,as_of='2026-07-14')['return_totals']['RTO']['missing_units'],0)
        inspection.update(wrong_units=1,effective_on='2026-07-17',claim_status='Paid')
        save_recovery(self.db,inspection)
        o=next(o for o in report(self.db,as_of='2026-07-18')['orders'] if o['item_id']=='100')
        self.assertEqual(o['profit'],money('-135'))
        self.assertEqual(o['net'],money('-10'))
        self.assertEqual(o['inspection']['notes'],'Empty package')
        self.assertEqual(o['inspection']['wrong'],1)
        inspection['wrong_units']=2
        with self.assertRaises(ValueError):save_recovery(self.db,inspection)

    def test_paid_nonreturnable_profit_and_quantity(self):
        self.order(quantity=2);self.payment();self.cost()
        self.assertEqual(self.result()['profit'],money('35.123'))

    def test_positive_settlement_can_be_less_than_saved_costs(self):
        self.order();self.payment(net='998.496',sale='1228');self.cost(cost='800',packaging='250')
        result=self.result()
        self.assertEqual(result['profit'],money('-51.504'))
        self.assertEqual(result['purchase_unit'],money('800'))
        self.assertEqual(result['purchase_total'],money('800'))
        self.assertEqual(result['packaging'],money('250'))

    def test_rto_rvp_and_unclassified_returns_are_separate(self):
        self.order(iid='100');self.payment(iid='100',rt='Logistics Return',net='0',refund='-150')
        self.order(iid='200');self.payment(iid='200',rt='Customer Return',net='-20',neft='N2',refund='-150')
        self.order(iid='300',status='RETURNED')
        data=report(self.db,as_of='2026-08-01')
        self.assertEqual({r['item_id']:r['return_category'] for r in data['orders']},
                         {'100':'RTO','200':'RVP','300':'Unclassified'})
        self.assertEqual(sum(t['orders'] for t in data['return_totals'].values()),3)
        self.assertEqual(data['return_totals']['RTO']['received'],0)
        self.assertEqual(data['return_totals']['RVP']['received'],money('-20'))

    def test_conflicting_return_types_are_mixed_and_respect_cutoff(self):
        self.order();self.payment(rt='Logistics Return',net='0')
        self.payment(rt='Customer Return',net='-10',paid='2026-08-10',neft='N2')
        self.assertEqual(self.result()['return_category'],'RTO')
        self.assertEqual(self.result('2026-08-20')['return_category'],'Mixed')

    def test_refund_without_type_is_unclassified(self):
        self.order();self.payment(net='-10',refund='-10',sale='0')
        self.assertEqual(self.result()['return_category'],'Unclassified')

    def test_missing_cost_is_unknown_and_zero_is_valid(self):
        self.order();self.payment()
        self.assertIsNone(self.result()['profit'])
        self.cost(cost='0',packaging='0')
        self.assertEqual(self.result()['profit'],money('120.123'))

    def test_partial_cost_does_not_look_like_zero(self):
        self.order();self.payment();self.cost(packaging='')
        self.assertIsNone(self.result()['profit'])
        self.assertIn('Packaging cost missing',self.result()['issues'])

    def test_unpaid_does_not_recognize_profit(self):
        self.order();self.cost()
        self.assertIsNone(self.result()['profit'])

    def test_customer_csv_merges_status_without_erasing_details_or_cost_sku(self):
        self.order();self.payment();self.cost()
        headers=['Ordered On','ORDER ITEM ID','Order Id','Order State','SKU','Product','Quantity','Buyer name','Ship to name','Address Line 1','City','State','PIN Code','Invoice No.']
        payload=csv_file(headers,[['Jul 01, 2026',"'100",'OD100','Returned','A-new','Product',1,'Buyer','Recipient','1 Street','City','State','110001','INV1'],['Jul 02, 2026',"'200",'OD200','Delivered','B','Other',2,'Other buyer','Other','2 Street','City','State','110002','INV2']])
        parsed=parse_file('Order-CSV.csv',payload)
        self.assertEqual(parsed['errors'],[])
        self.assertEqual(parsed['kind'],'orders_details')
        result=commit_file(self.db,parsed,payload)
        self.assertEqual((result['updated'],result['added']),(1,1))
        o=next(r for r in report(self.db,as_of='2026-08-01')['orders'] if r['item_id']=='100')
        self.assertEqual(o['sku'],'A')
        self.assertEqual(o['status'],'RETURNED')
        self.assertEqual(o['delivered_on'],'2026-07-05')
        self.assertEqual(o['customer']['address1'],'1 Street')
        self.assertEqual(o['net'],money('120.123'))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM transactions').fetchone()[0],1)
        self.assertTrue(commit_file(self.db,parsed,payload)['duplicate_file'])
        self.order(status='RETURNED')
        self.assertEqual(next(r for r in report(self.db)['orders'] if r['item_id']=='100')['customer']['pin'],'110001')
        bad=csv_file(headers,[['Jul 01, 2026',"'100",'WRONG','Delivered','A','Product',1,'','','','','','','']])
        before=self.db.execute('SELECT COUNT(*) FROM file_imports').fetchone()[0]
        with self.assertRaises(ValueError):commit_file(self.db,parse_file('conflict.csv',bad),bad)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM file_imports').fetchone()[0],before)

    def test_all_units_inspected_but_half_refunded_remains_provisional(self):
        self.order(quantity=2,status='RETURN_REQUESTED');self.cost(cost='250',packaging='10')
        self.payment(net='783.994',sale='788')
        self.payment(net='-632.717',sale='0',refund='-394',rt='Customer Return',paid='2026-07-11',neft='N2')
        save_recovery(self.db,dict(item_id='100',effective_on='2026-07-15',sellable_units=1,damaged_units=1,missing_units=0,wrong_units=0))
        r=self.result()
        self.assertEqual(r['provisional'],money('-108.723'))
        self.assertIn('Return refund appears incomplete',r['issues'])
        self.assertIsNone(r['profit'])
        self.payment(net='-394',sale='0',refund='-394',rt='Customer Return',paid='2026-07-20',neft='N3')
        r=self.result()
        self.assertEqual(r['profit'],money('-502.723'))
        self.assertNotIn('Return refund appears incomplete',r['issues'])

    def test_unpaid_damaged_return_records_loss_then_adds_settlement(self):
        self.order(status='RETURN_REQUESTED',quantity=2);self.cost(cost='800',packaging='25')
        save_recovery(self.db,dict(item_id='100',effective_on='2026-07-15',sellable_units=0,damaged_units=1,missing_units=0,wrong_units=0))
        o=self.result('2026-07-16')
        self.assertEqual(o['profit'],money('-825'))
        self.assertEqual(o['state'],'Loss recorded')
        self.assertEqual(o['payment_status'],'Unpaid')
        self.assertIsNone(self.result('2026-07-14')['profit'])
        self.assertEqual(report(self.db,as_of='2026-07-16')['sku_totals'][0]['profit'],money('-825'))
        self.payment(net='-100',paid='2026-07-20',sale='0',refund='-100',rt='Customer Return')
        self.assertEqual(self.result('2026-07-21')['profit'],money('-925'))
        self.payment(net='50',paid='2026-07-25',sale='0',neft='N2')
        self.assertEqual(self.result('2026-07-26')['profit'],money('-875'))
        self.assertEqual(self.result('2026-07-16')['profit'],money('-825'))

    def test_unpaid_loss_with_missing_cost_is_unknown(self):
        self.order(status='RETURNED')
        self.cost(cost='',packaging='25')
        save_recovery(self.db,dict(item_id='100',state='lost',effective_on='2026-07-15'))
        self.assertIsNone(self.result()['profit'])

    def test_later_refund_revises_nonreturnable(self):
        self.order();self.payment();self.cost()
        self.payment(net='-120.123',paid='2026-08-10',sale='0',refund='-150',rt='Customer Return',neft='N2')
        self.assertEqual(self.result()['profit'],money('75.123'))
        self.assertEqual(self.result('2026-08-20')['profit'],money('-5'))
        save_recovery(self.db,dict(item_id='100',state='sellable',effective_on='2026-08-15',quantity=1))
        self.assertEqual(self.result('2026-08-20')['profit'],money('-5'))

    def test_partial_sellable_recovery_only_removes_recovered_units(self):
        self.order(quantity=2,status='RETURNED',return_on='2026-07-15');self.payment(rt='Customer Return');self.cost()
        save_recovery(self.db,dict(item_id='100',state='sellable',effective_on='2026-07-20',quantity=1))
        self.assertEqual(self.result()['purchase_total'],0)
        self.assertEqual(self.result()['profit'],money('115.123'))
        self.assertEqual(self.result()['assumed_sellable_units'],1)

    def test_manual_return_retains_source_status_and_respects_date(self):
        self.order();self.payment();self.cost()
        save_manual_return(self.db,dict(item_id='100',effective_on='2026-07-15',category='Unclassified',notes='Reported return'))
        self.assertFalse(self.result('2026-07-14')['returned'])
        o=self.result('2026-07-16')
        self.assertTrue(o['returned'])
        self.assertEqual(o['status'],'DELIVERED')
        self.assertEqual(o['return_category'],'Unclassified')
        self.assertEqual(o['profit'],money('115.123'))
        self.assertEqual(o['net'],money('120.123'))
        save_manual_return(self.db,dict(item_id='100',effective_on='2026-07-17',category='RVP'))
        self.assertEqual(self.result('2026-07-18')['return_category'],'RVP')

    def test_lost_recovery_keeps_purchase_cost(self):
        self.order();self.payment(net='0',sale='150',refund='-150',rt='Logistics Return');self.cost()
        save_recovery(self.db,dict(item_id='100',state='lost',effective_on='2026-07-15'))
        self.assertEqual(self.result()['profit'],money('-45'))

    def test_returnable_window_boundary(self):
        self.order();self.payment();self.cost(policy='returnable',days=15)
        self.assertIsNone(self.result('2026-07-20')['profit'])
        self.assertEqual(self.result('2026-07-21')['profit'],money('75.123'))

    def test_dated_cost_is_based_on_order_date(self):
        self.order();self.payment();self.cost(cost='40',effective='2026-07-01')
        self.cost(cost='80',effective='2026-08-01')
        self.assertEqual(self.result()['profit'],money('75.123'))

    def test_residual_offer_credit_is_not_sale_payment(self):
        self.order();self.payment(sale='0',net='5');self.cost()
        self.assertIsNone(self.result()['profit'])
        self.assertIn('Sale payment not evidenced',self.result()['issues'])

    def test_cancelled_without_payment_is_excluded(self):
        self.order(status='CANCELLED')
        self.assertTrue(self.result()['excluded'])
        self.payment();self.cost()
        self.assertFalse(self.result()['excluded'])
        self.assertIsNone(self.result()['profit'])

    def test_same_neft_different_items_and_duplicate_file(self):
        self.order();self.order(iid='200')
        self.payment();result=self.payment()
        self.assertTrue(result['duplicate_file'])
        self.payment(iid='200')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM transactions').fetchone()[0],2)

    def test_identical_rows_preserve_audit_but_count_once(self):
        self.order();self.payment(copies=2)
        self.payment(copies=1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM transactions').fetchone()[0],2)
        self.assertEqual(self.result()['payment_count'],1)
        self.assertEqual(self.result()['net'],money('120.123'))

    def test_changed_neft_does_not_duplicate_payment(self):
        self.order();self.cost();self.payment(neft='DISBURSEMENT_CREATED')
        before=self.result()
        self.payment(neft='NFT-/REAL-UTR')
        after=self.result()
        for field in ('net','profit','payment_count'):
            self.assertEqual(after[field],before[field])
        self.assertEqual(after['duplicate_payment_count'],1)
        from core import order_detail
        d=order_detail(self.db,'100','2026-08-01')
        self.assertEqual(d['transactions'][0]['neft'],'NFT-/REAL-UTR')
        self.assertEqual(len(d['duplicate_transactions']),1)
        self.assertEqual(len(report(self.db,as_of='2026-08-01')['settlements']),1)
        self.assertEqual(report(self.db,as_of='2026-08-01')['totals']['bank_total'],money('120.123'))
        self.assertEqual(self.result('2026-07-09')['net'],0)
        self.payment(neft='OTHER',paid='2026-07-11')
        self.payment(neft='OTHER2',net='121.123')
        self.assertEqual(self.result()['payment_count'],3)

    def test_flipkart_column_suffix_changes_do_not_split_duplicate_settlement(self):
        self.order();self.cost();self.payment(neft='DISBURSEMENT_CREATED')
        self.payment(neft='NFT-/REAL-UTR')
        rows=self.db.execute('SELECT identity,components FROM transactions ORDER BY identity').fetchall()
        first=json.loads(rows[0]['components']);second=json.loads(rows[1]['components'])
        first['marketplacefeerssumuah']=-1000000
        second['marketplacefeerssumvai']=-1000000
        self.db.execute('UPDATE transactions SET components=? WHERE identity=?',(json.dumps(first),rows[0]['identity']))
        self.db.execute('UPDATE transactions SET components=? WHERE identity=?',(json.dumps(second),rows[1]['identity']))
        self.db.commit()
        result=self.result()
        self.assertEqual(result['payment_count'],1)
        self.assertEqual(result['duplicate_payment_count'],1)
        self.assertEqual(result['net'],money('120.123'))
        from core import order_detail
        detail=order_detail(self.db,'100','2026-08-01')
        self.assertEqual(len(detail['transactions']),1)
        self.assertEqual(detail['transactions'][0]['neft'],'NFT-/REAL-UTR')
        self.assertEqual(len(detail['duplicate_transactions']),1)

    def test_same_net_different_financial_breakdown_is_not_duplicate(self):
        self.order();self.cost();self.payment(net='120',sale='150')
        self.payment(net='120',sale='160',neft='N2')
        self.assertEqual(self.result()['payment_count'],2)
        self.assertEqual(self.result()['net'],money('240'))

    def test_unmatched_payment_not_lost_or_allocated(self):
        self.order();self.payment(iid='missing')
        r=report(self.db,as_of='2026-08-01')
        self.assertEqual(len(r['unmatched']),1)
        self.assertEqual(r['totals']['received'],0)
        self.assertEqual(r['totals']['bank_total'],money('120.123'))

    def test_invalid_import_saves_nothing(self):
        payload=csv_file(ORDER_HEADERS,[['OI:100','OD100','bad-date','DELIVERED','A','Title',1,'','']])
        result=parse_file('bad.csv',payload)
        with self.assertRaises(ValueError):
            commit_file(self.db,result,payload)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM file_imports').fetchone()[0],0)

    def test_provided_workbooks_match_source_controls(self):
        expected={'payment1.xlsx':'24775.763','payment2.xlsx':'31134.933','payment3.xlsx':'178879.014'}
        for p in sorted((ROOT/'input').glob('*.xlsx')):
            if p.name not in expected and not p.name.startswith('order'):
                continue
            payload=p.read_bytes();result=parse_file(p.name,payload)
            self.assertEqual(result['errors'],[],p.name)
            if p.name in expected:
                self.assertEqual(result['net'],money(expected[p.name]))
                self.assertEqual(result['summary_delta'],0)
            commit_file(self.db,result,payload)
        if len(list((ROOT/'input').glob('order[123].xlsx')))==3:
            r=report(self.db,as_of='2026-10-04')
            self.assertEqual(r['totals']['orders'],705)
            self.assertEqual(r['totals']['paid_orders'],343)
            self.assertEqual(r['totals']['bank_total'],money('234789.710'))
            self.assertEqual(len(r['unmatched']),0)
            self.assertEqual(len(r['account']),4)
            self.assertEqual(r['totals']['profit'],0)
            self.assertTrue(all(o['cancellation_refunded'] for o in r['orders'] if o['profit'] is not None))

class HttpTests(unittest.TestCase):
    def test_endpoints_and_cross_origin_protection(self):
        (ROOT/'data'/'test-temp').mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT/'data'/'test-temp') as temp:
            server=make_server(Path(temp)/'http.sqlite3',0)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            http=build_opener(ProxyHandler({}));url=f'http://127.0.0.1:{server.server_port}'
            try:
                for path in ('/','/app.js','/style.css','/api/meta','/api/report','/api/products','/api/imports','/api/export','/api/backup'):
                    with http.open(url+path,timeout=5) as response:
                        self.assertEqual(response.status,200)
                req=Request(url+'/api/product',data=b'{}',headers={'Content-Type':'application/json','Origin':'https://example.com'})
                with self.assertRaises(HTTPError) as error:
                    http.open(req,timeout=5)
                self.assertEqual(error.exception.code,403)
                error.exception.close()
                req=Request(url+'/api/import/preview',data=json.dumps({'input_name':'../core.py'}).encode(),headers={'Content-Type':'application/json'})
                with self.assertRaises(HTTPError) as error:
                    http.open(req,timeout=5)
                self.assertEqual(error.exception.code,400)
                error.exception.close()
            finally:
                server.shutdown();server.server_close();thread.join()

if __name__=='__main__':
    unittest.main()
