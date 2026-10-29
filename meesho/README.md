# Meesho Ledger

A local Python application for importing Meesho order CSVs, payment ZIPs/XLSX,
and a two-column product cost workbook. Data is stored in `data/meesho.sqlite3`.
Double-click **start.bat** to start the password-protected team web application
on port **8766**. It reuses an already running team server and opens the browser.
The startup window displays the local-network addresses and the saved login.
The username and password remain unchanged across restarts; keep
`data/team-access.json` to retain them. Everyone uses one shared database.

See [TEAM_SETUP.md](TEAM_SETUP.md) for the one-time firewall setup and team access.
Teammates can upload files from their own PCs and update costs in their browsers.
Use **stop-team.bat** on the host to stop the server. **start-team.bat** remains
an optional compatibility shortcut; it is no longer required.

To stop both team and personal mode, double-click **stop.bat**, or run
`bash stop.sh` from Git Bash in this folder. Saved orders and payments are
preserved. Personal mode must first be restarted once to load the new shutdown
endpoint; the script can stop an existing team-mode run immediately.

Logs are in `data/team-server.log`. The optional command **start.bat --local**
starts personal-only mode at http://127.0.0.1:8765. **Start.ps1** also defaults to
team mode. **start.bat --no-browser** starts team mode without opening a browser.

On a new host with Python 3.11 or newer:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.\start.bat
```

## First use

1. Import Files: select one or more monthly order CSVs, payment ZIPs (multiple months allowed), and
   the two-column product XLSX. Preview, then save. Initial product costs can
   use 0001-01-01 as their effective date to cover all historical orders.
2. Products & Costs: choose the effective date, fill packaging per SKU or use
   the bulk update. Blank is unknown; an explicit zero is a valid zero cost.
3. Overview: choose an order period and payments-through date. The default
   shows 30 inclusive days ending at the 21-day settlement cutoff, or the
   last imported order date if earlier. A period ending August 27 starts
   July 29. Custom From dates remain editable. Changing the Through date
   resets From to a 30-day window; Last 30 days restores this default.
4. Click a sub-order ID for transaction history. Record return/RTO recovery
   only when you know whether the goods are sellable, damaged, or lost.

## Changing product prices

On Products & Costs, choose the date whose existing prices you want to view.
Edit a SKU's cost and its **Apply from** date, then save. For example, a ₹100
cost applied from July 1 and a ₹120 cost applied from August 1 give July
orders a ₹100 cost and August orders a ₹120 cost. Quantity is applied after
selecting the dated price. Payment dates do not change which cost is used.
Use **History** beside a product to see all saved prices. Future prices can
be scheduled in the same way. The view date also sets the bulk packaging
apply date. Product workbook imports have a separate effective-date field.

The first saved purchase price also fills previously unpriced older orders,
including older orders imported later. Later price changes still apply from
their selected dates; existing historical prices and explicit zero costs are
preserved. This first-price rule applies to both manual saves and workbook
imports. Order detail identifies when the first-price fallback is used.

An export replacing a spaced dash with `?` is matched to the priced SKU only
when the rest of the name is identical and the match is unique. Different
pack sizes, genuinely different names, and ambiguous matches remain missing.

## Calculation rules

Net profit = settlement received - advertising - product cost - packaging.

- One order per sub-order ID. Distinct order events are retained in history.
- Cancelled orders are excluded, even when financial transactions exist;
  those anomalies are visible in Reconciliation.
- All distinct matched payment transactions through the chosen date are
  included, including zero and negative settlements. A payment record is
  not proof that an order is fully settled: the source has no expected total.
- Payment files can settle orders from other months. Only matching selected
  order IDs enter order profit. Unmatched payments remain visible separately.
- Shipping, reverse shipping, TDS, and TCS are informational components
  already reflected in settlement, never deducted twice. Signs are preserved.
- RTO reverse shipping is expected to be zero. Actual unexpected deductions
  are flagged, not erased from the amount received.
- GST-inclusive costs are used by default. Unchecking Include GST divides
  cost by `1 + rate/100`. Rate defaults to 18%. This is a global report
  preference; it does not rewrite imported cost values.
- Product costs are multiplied by quantity. Packaging is once per
  non-cancelled sub-order, including returns/RTOs. The latest saved packaging
  amount applies to all previous and future orders for that SKU, including
  older orders imported later. Product prices retain their effective dates.
- RTOs default to reusable stock with zero product cost. Customer returns
  need sellable recovery confirmed to remove product cost. Explicit awaiting,
  damaged, or lost outcomes retain product cost. Recovery changes are dated.
- Exchanges currently count one product cost. They are explicitly flagged
  and make profit provisional because additional replacement costs cannot
  be inferred reliably from these files.
- Ads use signed net deductions including GST after credits/waivers,
  assigned to the spend period. Multi-day periods are allocated evenly by
  inclusive calendar days. Account-level referrals/compensation are stored
  separately and excluded from the agreed order-profit formula.
- Pending payout amount is unknown when no expected-settlement field exists;
  listed price is not presented as the amount the marketplace owes.
- Profit is calculated using known costs even when some costs are missing.
  Missing values remain unknown in the database and are excluded from the
  calculation; a dark-red warning identifies the affected orders and the
  profit is labelled provisional. Missing payments or recovery outcomes
  also produce provisional results.
- Hover, focus, or tap a point on Settlement over time to see the exact
  settlement, order count, listed value, and date.
- Monetary values are integer paise; tax calculations use Decimal, rounded
  half up at the per-order level.

## Source integrity and known limits

Original uploaded bytes and parsed source rows are stored in SQLite. File
hashes skip repeated uploads. Financial fingerprints exclude mutable payment
status/product-name fields so an otherwise identical re-export does not add
the same transaction twice. Different financial records for the same order
are retained. Identical repeated rows are kept in raw history but counted
once and flagged during preview; the source provides no unique line ID to
distinguish truly identical transactions.

Order status is resolved from all imported order events and payment evidence
through the report date. Explicit order cancellation takes priority, then
Return, RTO, Exchange, Lost, and the latest imported order status. Differences
are surfaced. Historical status snapshots cannot be fully reconstructed from
these exports. The payments-through date filters transactions, not the date
at which the business originally learned a status.

The displayed payment date coverage is the earliest/latest transaction date,
not a guarantee of complete statements. Import additional months as received.

## Backup

Use **Download database backup** in the sidebar. This creates a consistent
SQLite backup including original imported files, cost versions, and settings.
To restore, stop the app and replace `data/meesho.sqlite3` with that backup.
Keep backups outside this project. Do not run two app instances against
different copies of a cloud-synced database at the same time.

## Tests

```powershell
python -m unittest discover -s tests -v
```


Product Performance lists settlements, purchase cost, packaging, profit before
ads, returns, RTOs, and pending payments per SKU. Use the report SKU picker to
search names and select multiple SKUs; Apply selection updates reports and order
exports. Advertising remains unallocated at SKU level because source records do
not reliably identify a SKU.

Products & costs → Add SKU manually creates a product without Excel. Duplicate
SKU names are rejected. Packaging-only saves do not create price changes. Editing
a price or its Apply from date creates a dated price version; later genuine price
versions still take precedence. The first price fills earlier unpriced orders.


### Refreshing imports and managing SKUs

- Re-uploading an orders file updates the current row by Sub Order No, including
  status, date, SKU and quantity. Original source files and rows remain available.
- Payment corrections match sub-order, transaction ID, payment date and Price
  Type. Amounts and status are updated in place; distinct transactions are kept.
  Files with conflicting identities are rejected for review. Exact repeated
  records are counted once. A blank transaction ID can be matched to an identified payment when all
  other source details and financial amounts agree and the match is unique.
  Existing blank-ID duplicates are excluded from totals, with source history
  retained and labelled in order details. Distinct bank IDs remain separate. Advertising corrections match campaign, spend period and
  deduction date. Account adjustments without unique identity use exact matching.
- Import preview and completion report new versus refreshed records. A repeated
  source file is stored once; each successful application has an import-run log.
- The Overview retains its 21-day default. Imported date coverage explains which
  dates are excluded. Use Show all imported orders to include recent orders and
  Refresh report to fetch changes made by teammates. Report navigation also
  fetches current data. Import completion refreshes Payments through to today.
- SKUs in order and payment files automatically appear in Products & costs with
  missing price and packaging left blank. Existing manually entered costs remain
  intact when importing orders or payments.
- Disable/Enable controls and the Active / Disabled / All filter manage the
  product list. Disabled products retain their history and still count in profit.
  Bulk packaging updates apply to active SKUs only; reimports do not enable SKUs.
