# Amazon Ledger

A fast, local Python application for reconciling Amazon orders, settlements, returns/RTOs, and advertising data. Data is stored locally in `data/amazon.sqlite3`.

Double-click **start.bat** to start the application on port **8775** (http://127.0.0.1:8775).

---

## Key Features

1. **Overview & Cash Profit**:
   - **Net Profit** = Net Settlement Received − Product Purchase Cost − Packaging Cost − Attributed Advertising Spend − Account Service Fees.
   - **Daily Settlement Trend**: Interactive SVG chart grouped by order purchase date.
   - **Fee Breakdown**: Separate transparent totals for Easy Ship weight handling fees, referral/closing fees, refunds, and SAFE-T claims.

2. **Product Performance**:
   - Compares settlements, purchase costs, packaging, and advertising spend per SKU.
   - Shows **Profit Before Ads** and **Profit After Ads** per SKU.
   - Filterable by multi-SKU search picker.

3. **Orders Financial Detail**:
   - Inspect any Amazon Order ID to view all linked transactions (`Order`, `Shipping Services`, `Refund`, `SAFE-T Reimbursement`), exact fee breakdown, and customer location.
   - Separate view for **Awaiting Payment** (orders awaiting settlement release).

4. **Returns & RTO Management**:
   - Clear distinction between **RTO (Courier Undelivered / Buyer Rejected)** and **Customer Returns (C-Returns)**.
   - **Resellable by Default**: All returned and RTO products default to **Resellable / In Stock** (`sellable`), incurring **₹0 product cost** deduction from profit (the item is back in inventory).
   - Packaging is retained (as packaging materials were consumed).
   - You only need to mark an item if it was returned **Damaged** or **Lost** (via a 1-click button), which then charges the purchase cost as a loss.
   - Flags fee discrepancies (e.g. unreversed Easy Ship fees on courier returns).

5. **Advertising & Keyword Intelligence**:
   - SKU-level ad spend attribution.
   - Identifies **Wasted Ad Spend** (search terms with clicks and cost but 0 sales) so you can add them as negative keywords.
   - Highlights **Top Winning Keywords** with high ROAS.

6. **Idempotent Upsert & Deduplication**:
   - Uploading overlapping date ranges or re-uploading reports **never duplicates** order counts or settlement amounts.
   - Live import preview shows exactly how many records are new vs refreshed.

7. **⚡ Auto-Detect Downloads**:
   - Click "Auto-Import All Found Files" on the Import page to instantly pull Amazon files downloaded from Gmail or Seller Central into your `Downloads` or `amazon/input/` folder.

---

## File Types Supported

- **Orders Report**: `.txt`, `.tsv`, `.csv` (tab-delimited flat file from All Orders)
- **Settlement Report**: `.csv` (Amazon Custom Date Range Unified Transaction report)
- **Returns Report**: `.tsv`, `.txt`, `.csv` (Amazon Customer Returns report)
- **Advertising Reports**: `.csv` (Advertised Product, Campaign, or Search Term reports)
- **Product Cost Book**: `.xlsx` (two-column Excel with SKU and GST-inclusive Cost Price)

---

## Starting and Stopping

- **Start**: Double-click `start.bat` (opens `http://127.0.0.1:8775`).
- **Stop**: Double-click `stop.bat`.

---

## Running Unit Tests

```powershell
python -m unittest discover -s tests -v
```
