# Flipkart Ledger

A separate local application for downloaded Flipkart orders and settlement
reports. Its database, frontend, CSS and launch scripts are independent of
Meesho. No API credentials or paid service are needed.

## Start and stop

Double-click **start.bat** in this folder. The app opens at
http://127.0.0.1:8770 and runs locally on this computer. Use **stop.bat** to
stop only Flipkart. Closing the browser does not stop the server.

The launcher uses this project's virtual environment when present, otherwise
the bundled Python runtime on this computer, then system Python. On another
computer, install Python 3.11+ and `pip install -r requirements.txt`.

## Your initial files

All six supplied XLSX files were imported into `data/flipkart.sqlite3`.
Original input files are untouched. See [INPUT_ANALYSIS.md](INPUT_ANALYSIS.md)
for counts, settlement totals and source limitations.

## Using the application

1. **Overview:** select order dates, payments-through date and optionally a SKU.
   Order profit includes every matching payment through that cutoff, even when
   the order and payment are in different months.
2. **Products & costs:** enter purchase price per unit and packaging per order
   item. Blank is unknown; an explicit zero is valid. Set the effective date to
   `0001-01-01` for initial costs covering all historical orders. Later price
   changes apply from their selected date, using the order date for selection.
   Use the **All historical orders** checkbox to apply initial costs without
   entering a date. Each product shows Saved or a specific error after submission;
   saving one product preserves unsaved entries in other products.
   Costs are kept separate from Meesho. The view date lets you inspect older
   or scheduled costs, and Cost history lists saved versions.
3. **Non-returnable SKU:** checked by default, as requested. Payment evidence
   makes profit eligible without a return-window wait once costs are known.
   Uncheck it and enter the return window for a returnable product.
4. **Orders:** search an order ID, item ID or SKU. Open an item to see all
   financial rows and original source fields.
5. **Returns & recovery:** a return overrides the non-returnable setting.
   Logistics Return is **RTO** and Customer Return is **RVP**, with separate
   totals and filters on Orders and Returns & recovery. The Overview also shows
   these totals. A return without an explicit payment type stays Unclassified;
   an item with both RTO and RVP rows is Mixed, so it is not counted twice.
   Confirm sellable, damaged or lost goods and the effective confirmation date.
   For sellable goods, enter the recovered unit count. Only those units' purchase
   cost is removed; packaging remains charged. A source status of Product
   Delivered does not establish sellable stock. A requested return stays under
   review until the order snapshot shows it completed.
6. **Settlement ledger:** view source-summary tie-outs, account transactions,
   unmatched payments, undated entries and all dated settlement rows.
7. **Import files:** place later reports in `input/`, or select CSV/XLSX files
   from your computer. Preview and save each report. Import newest order
   snapshots last; the imported snapshot updates the current order status.
8. **Export CSV** exports the selected order report. **Download database backup**
   saves a complete SQLite snapshot, including imports and original file bytes.

Order details shows settlement, dated purchase cost, charged unit count and
packaging as a calculation breakdown. Positive settlement can still produce a
loss when the saved costs exceed it; use Edit costs to correct a mistaken input.

## Calculation and import rules

Profit = signed bank settlement − purchase price × units retained − packaging.
This is cash-based order profit. GST-inclusive/otherwise cost values are used
exactly as entered. Tax credits are informational; no tax filing or tax-adjusted
profit calculation is made.

- Monetary values are stored as integer millionths of a rupee, preserving
  Flipkart's three-decimal settlement and tax values. Display rounds to at most
  three decimals; CSV export preserves up to six.
- Order item IDs remain text. `OI:` and the quoted `SKU:` export wrappers are
  normalized. SKU case and punctuation are preserved; discrepancies stay visible.
- NEFT identifies a bank payout, not a unique financial event. Original rows and
  file controls are retained for audit. Identical order rows are counted once by
  item, SKU, order/payment dates, sheet, full financial breakdown and available
  transaction/claim references, even when NEFT or return status changes. Repeats
  within a file are also counted once. Prefer the actual bank reference over a
  preliminary DISBURSEMENT status. Account-level rows retain their references.
  Different financial amounts, breakdowns, dates or explicit event IDs remain
  distinct. Dashboard, offers, profit, exports and settlement history use counted
  rows; order details separately show excluded duplicates. Raw file totals still
  describe each export and may overlap. No original imported data is deleted.
- All workbook financial sheets are parsed. Summary and GST_Details are not
  added as separate cash transactions. Bank totals are checked against the
  workbook's Net Bank Settlement summary; a mismatch blocks import.
- Ads wallet entries, non-order compensation and service charges stay separate
  from order profit. They enter the account bank totals when dated. No advertising
  cost is inferred from wallet transfers. Unknown worksheets are reported in
  preview; CSV files without a summary have no independent source tie-out.
- A settlement record can be zero or negative. A fee/offer-only credit is not
  taken as evidence of a paid sale. Normal sale eligibility requires a positive
  sale amount and positive bank amount on a row. Return settlements may have
  zero/negative bank values and are handled through recovery review.
- The files have no complete expected settlement value; paid does not mean a
  final payout is guaranteed. Later refunds, compensation and fees revise profit.
- Cancelled/rejected orders without settlements are excluded. Any financial
  rows attached to them remain visible as anomalies. Exchange/replacement costs
  require review rather than guessing additional product costs.
- Payments-through filters financial dates and dated recovery decisions.
  Order statuses and return policies come from the latest imported/saved state;
  this is not a historical reconstruction of every status or policy change.
- File imports retain source row provenance and original bytes. Invalid rows
  block the entire file; the importer does not silently save a partial file.

## Checks

Run from this folder:

```powershell
python -m unittest discover -s tests
```

Tests cover the supplied XLSX totals, CSV parsing, quantity-based costs, dated
prices, unpaid/zero/negative payments, later refunds, partial recovery, payout
deduplication, unmatched payments, import errors and HTTP access protections.

The SKU dashboard groups the selected order period and payment cutoff by order SKU. It shows recognized and provisional profit, profit coverage, per-unit profit, margin against recognized net settlement, delivered/RVP/RTO units, other status groups, recovered stock and review counts. Units use order quantities; return groups take precedence over delivered status. Payment files do not establish partial-return quantities. Click a SKU to see its orders, or export the filtered SKU summary as CSV.

Offer information is display-only. Overview and SKU summaries show signed posted offer amounts, seller share and derived Flipkart remainder. Orders/Returns support applied, no offer reported, seller contribution and unknown filters. Application checks transaction and buyer details through the payment cutoff; reversals do not erase application history. Buyer details are not summed, and blank or absent fields remain unknown. Settlement, costs and profit calculations are unchanged.

Return inspection: RTO and RVP have separate outcome totals. Enter cumulative sellable, damaged, missing/suspected-theft and wrong-product unit counts per order, with an update date, notes, evidence reference and claim status. Unassigned units remain pending inspection and are assumed resellable for profit until an actual outcome is recorded. Only sellable units remove purchase cost; all other units retain it, with packaging charged once. Claim status never adds compensation; imported settlements remain the financial source. Dated inspections preserve earlier payment-cutoff views. Damaged and lost recovery entries retain their purchase cost; any unassigned units in partial sellable recovery are assumed resellable.

Navigation now has one Returns tab with Payments & profit and Inspection & claims views. Switching views preserves the selected RTO/RVP category and search. Both views share the date and SKU filters and the same order details.
`nManually reported returns can be added or classified from order details when an imported snapshot still says Delivered. The effective reporting date controls visibility, and original status and settlements remain intact. Order and item IDs are both searchable in both Returns views.
Inspected damaged, missing or wrong-product returns record their calculable purchase and packaging cost loss before the first payment. They show Loss recorded while payment remains Unpaid. Later signed settlements revise that same result; cost is never deducted twice. Missing costs and other reconciliation issues still require review.

Order-CSV support: the customer/invoice export is detected as orders_details in the same upload and input-folder preview flow. It strips the item ID apostrophe, parses named-month order dates and stores customer/address/PIN/invoice data separately. Matching items retain original SKU labels, detailed dates and all source snapshots; the CSV status becomes current. New items are added without guessed delivery/return dates. Identity/date/quantity conflicts reject the whole import. Blank fields never erase existing detail. Import snapshots oldest first, newest last; export timestamps are absent, so freshness cannot be established automatically. Invoice amounts never enter settlement calculations. Addresses appear in both Returns views and order details.

Payout review: Orders display Recognized reconciliation as Settled. Exactly two rows under the same order item, date, sheet and nonempty payout reference are automatically accepted when one is a sale settlement and one is a separate offer settlement, both have positive net amounts, neither carries a return/refund/protection entry, and each primary exported financial breakdown exactly equals its net bank value. All other multiple-row groups remain in Review, and missing costs or other issues remain blocking. Order details show entry types, sale and offer components, and the payout review result. Net bank amounts remain the only settlement total; offer components are never added a second time. This checks imported report consistency, not bank receipt or full expected payout completeness.

Daily orders: one row per order-placement date, following the shared order period, SKU and payment cutoff. Unique order IDs count once per metric, while money sums all their matching items. Pickup uses recorded dispatch through the cutoff. Daily counts and financial totals link to Orders using the same date and category predicate, with clear/back controls. Return net settlements and refund entries are informational subsets of total cash. Pending counts active items without settlement rows; final expected payout is unavailable and no pending cash estimate is invented. Counts overlap across lifecycle stages.

Payment review controls: order details allow Automatic checks, Confirm payments received or Keep in review, with notes. Confirmation clears only multiple-payout-row warnings (and sale-evidence warnings when imported net cash is positive), never missing costs or return/other blockers. Reviews match the exact deduplicated financial entry set and apply from the selected cutoff; changed payments invalidate the saved review. Imported amounts remain unchanged. Marketplace fee sum headings are recognized independently of shifting workbook column letters for automatic sale-plus-offer verification.

Order status correction: order details provide Delivered to customer and other lifecycle statuses with an effective date, optional actual delivery date and required reason. Dated corrections preserve original order rows and are superseded when a new order report updates that item. Use imported status records an undo. Delivered removes order-report/manual return flags and charges purchase cost for all units; actual return settlement evidence is preserved and triggers a conflict review. Historical cutoff views and stale-source validation are supported.

Cancelled prepaid orders with no recorded dispatch are automatically settled when their positive sale amount is exactly offset by signed refunds and total net settlement is zero or negative. Purchase and packaging cost are zero for these cancellations; residual fees remain a recognized loss. Missing/incomplete refunds, positive residual net cash or dispatched cancellations stay in review. Financial rows and original statuses remain intact.
