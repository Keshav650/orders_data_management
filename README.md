# Order reconciliation projects

These applications are developed separately:

- **Meesho:** `meesho/`. Existing application, database, frontend, styles,
  sample files, backups, tests and launch scripts are all here.
  Double-click `meesho/start.bat` to start it.
- **Flipkart:** `flipkart/`. Separate file-based application. Double-click
  `flipkart/start.bat` to start it at http://127.0.0.1:8770.
  Place downloaded orders and payments CSV files in `flipkart/input/`.
  Its database will live in `flipkart/data/`; its frontend and CSS will
  live in `flipkart/static/`.
- **Amazon:** `amazon/`. Reconciles Amazon orders, custom unified transactions,
  returns/RTOs, and advertising reports. Double-click `amazon/start.bat` to
  start it at http://127.0.0.1:8775. Features idempotent upserts, SKU ad spend
  attribution, wasted ad spend analytics, and 1-click downloads auto-import.

Flipkart will use downloaded CSV files. API integration is out of scope.
Flipkart also supports the original multi-sheet XLSX exports.
The repository metadata remains at this parent level.
