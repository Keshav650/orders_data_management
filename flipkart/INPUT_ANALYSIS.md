# Supplied Flipkart files

Analyzed and imported on 4 October 2026. Source workbooks are unchanged.

## Orders

| Source | Order period | Order items |
| --- | --- | ---: |
| input/order3.xlsx, Orders | 14–31 July 2026 | 119 |
| input/order2.xlsx, Orders | 1–31 August 2026 | 66 |
| input/order1.xlsx, Orders | 1–30 September 2026 | 520 |
| Total unique items | | 705 |

There are 17 SKUs in the orders data. The initial July orders start on 14 July;
this does not establish coverage for the earlier part of that month.

## Payment controls

| Source | Settlement period | Order financial rows | Account rows | Net bank settlement (₹) | Difference from workbook summary (₹) |
| --- | --- | ---: | ---: | ---: | ---: |
| input/payment1.xlsx | July 2026 | 57 | 0 | 24,775.763 | 0.000 |
| input/payment2.xlsx | August 2026 | 87 | 0 | 31,134.933 | 0.000 |
| input/payment3.xlsx | September 2026 | 269 | 4 | 178,879.014 | 0.000 |
| Total | | 413 | 4 | 234,789.710 | 0.000 |

Each payment control compares the imported signed cash rows with **Net Bank
Settlement (A)** on the workbook's Summary of report. All 413 order-related
rows match an imported order item. They cover 343 distinct items; some orders
have multiple transactions across payment dates/months. No bank statement was
provided, so this verifies the exports, not actual bank receipt.

The four September Ads rows have zero bank settlement. Their wallet redeem,
reversal and GST components cancel within each row. They are kept as account
entries; an advertising expense is not guessed from these values.

## Initial reconciliation

- 343 order items have one or more settlement rows, including zero-value rows.
- 280 active items have no settlement record.
- 82 cancelled/rejected items have no financial rows and are excluded.
- 137 items have a return indication from an order or payment report.
- 36 order snapshots show RETURN_REQUESTED.
- Two cancelled orders have zero-value settlement rows; both remain anomalies.
- One order/payment SKU differs: order item `338116890658480100` is `LC1 2 4`
  in orders and `LC1,2,4` in payments. It is matched by item ID, with the SKU
  difference flagged. Both source SKU labels are preserved; the catalog therefore
  contains 18 labels, including the payment-only spelling.
- No purchase or packaging cost file was provided. Recognized profit remains
  unavailable until the separate Flipkart costs are entered.

## Export details handled by the importer

The workbooks declare incorrect worksheet dimensions. Reading only the
declared range would miss all order rows and nearly all settlement data. The
importer resets these dimensions and reads the actual worksheet rows.

Orders have `OI:`-prefixed item IDs and quoted `SKU:` labels. Payments use plain
item IDs and plain SKUs. The importer strips those wrappers while keeping the
18-digit identifiers as text and retaining original source fields.

Payment Orders has a grouped heading, the actual header on row 2, then a third
buyer-sale heading. The importer reads the correct header and does not treat
the third heading as a financial transaction. NEFT values are shared across
different items; deduplication therefore includes financial row content.

Customer Return, Logistics Return, refunds and SPF amounts are present in the
payment data. A Non-returnable product setting does not suppress these events.
Product Delivered in Item Return Status does not prove that stock was returned
in sellable condition; recovery remains an explicit confirmation.
