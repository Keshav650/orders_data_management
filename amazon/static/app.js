'use strict';

const $ = s => document.querySelector(s);
const escapeHTML = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const inr = v => v === null || v === undefined ? 'Not set' : new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 2 }).format((v || 0) / 100);
const number = v => new Intl.NumberFormat('en-IN').format(v || 0);
const localToday = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
const fmtDate = v => !v ? '—' : v === '0001-01-01' ? 'All order dates' : new Date(v + 'T00:00:00').toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });

let data, currentPage = 'dashboard', productRows = [], previews = [], tablePage = 0, productPage = 0, returnsPage = 0;
let search = '', statusFilter = '', paymentFilter = '', orderFilter = '', productSearch = '', productCompleteness = '', productStatus = 'active';
let returnDeliveryFilter = '', returnSearch = '';
let selectedSkus = [], draftSkus = new Set(), lastImportSummary = '';

const pageNames = {
  dashboard: ['Your Amazon business, at a glance.', 'Understand your net settlements, fee deductions, advertising spend, and true cash profit.', 'Overview'],
  performance: ['Product Performance', 'Compare settlements, purchase costs, packaging, ad spend, and net profit per SKU.', 'Product Performance'],
  orders: ['Every order. Every settlement.', 'Follow orders from purchase to settlement, with full fee breakdown and recovery disposition.', 'Orders'],
  awaiting: ['Awaiting settlement', 'Orders placed that have not yet settled or are deferred in Amazon unified transactions.', 'Awaiting payment'],
  returns: ['Returns & RTO management', 'Track courier undelivered items, customer rejections, returns, tracking IDs, and recovery states.', 'Returns & RTO'],
  products: ['Your products & costs', 'Manage GST-inclusive purchase costs and packaging for each SKU.', 'Products & costs'],
  reconciliation: ['Reconciliation & Advertising Intelligence', 'Audit anomalies, unreversed fees, wasted ad spend, and high-ROAS keywords.', 'Reconciliation & Ads'],
  imports: ['Bring your records together', 'Preview files, check idempotent upserts, and import with zero duplicate count risk.', 'Import files']
};

async function api(path, body) {
  const opts = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  const res = await fetch(path, opts);
  const result = await res.json();
  if (!res.ok) throw new Error(result.error || 'Request failed');
  return result;
}

function error(e) { $('#error').textContent = e.message || e; $('#error').hidden = false; }
function clearError() { $('#error').hidden = true; }
function toast(msg) { $('#toast').textContent = msg; $('#toast').hidden = false; setTimeout(() => $('#toast').hidden = true, 4500); }
async function run(fn) { clearError(); try { await fn(); } catch (e) { error(e); } }

function query() {
  const q = new URLSearchParams({
    start: $('#start').value,
    end: $('#end').value,
    as_of: $('#as-of').value,
    mode: $('#mode').value
  });
  selectedSkus.forEach(s => q.append('skus', s));
  return q.toString();
}

async function refresh() {
  data = await api('/api/report?' + query());
  selectedSkus = data.filters.skus || [];
  draftSkus = new Set(selectedSkus);
  renderSkuPicker();
  $('#start').value = data.filters.start;
  $('#end').value = data.filters.end;
  $('#as-of').value = data.filters.as_of;
  $('#end').disabled = $('#mode').value === 'mature';
  $('#nav-pending').textContent = number(data.summary.awaiting);
  await renderPage();
}

async function navigate(page) {
  currentPage = page;
  tablePage = 0;
  returnsPage = 0;
  orderFilter = '';
  search = '';
  statusFilter = '';
  paymentFilter = '';
  returnDeliveryFilter = '';
  returnSearch = '';
  history.replaceState(null, '', '#' + page);
  if (['dashboard', 'performance', 'orders', 'awaiting', 'returns', 'reconciliation'].includes(page)) {
    await refresh();
  } else {
    await renderPage();
  }
}

async function renderPage() {
  const titles = pageNames[currentPage] || pageNames.dashboard;
  $('#page-title').textContent = titles[0];
  $('#page-description').textContent = titles[1];
  $('#breadcrumb').textContent = titles[2];
  document.querySelectorAll('nav button').forEach(b => b.classList.toggle('active', b.dataset.page === currentPage));
  $('#report-filters').hidden = ['products', 'imports'].includes(currentPage);

  if (currentPage === 'dashboard') renderDashboard();
  else if (currentPage === 'performance') renderPerformance();
  else if (currentPage === 'orders' || currentPage === 'awaiting') renderOrders();
  else if (currentPage === 'returns') renderReturns();
  else if (currentPage === 'products') await renderProducts();
  else if (currentPage === 'reconciliation') renderReconciliation();
  else if (currentPage === 'imports') await renderImports();
}

function panel(title, subtitle, body, extra = '') {
  return `<section class="panel"><div class="panel-head"><div><h2>${title}</h2>${subtitle ? `<p>${subtitle}</p>` : ''}</div>${extra}</div><div class="panel-body">${body}</div></section>`;
}

function context() {
  const adsCov = (data.coverage.first_ad && data.coverage.last_ad) ?
    `<span>Ad spend imported: ${fmtDate(data.coverage.first_ad)} – ${fmtDate(data.coverage.last_ad)} (${inr(data.coverage.total_imported_ads)})</span>` : '';
  return `<div class="notice info">` +
    `Imported orders: ${fmtDate(data.coverage.first_order)} – ${fmtDate(data.coverage.last_order)}. ` +
    `${data.filters.mode === 'mature' ? 'Default view excludes orders newer than ' + fmtDate(data.filters.cutoff) + ' (14-day settlement cutoff).' : ''} ` +
    `Last import: ${escapeHTML(data.coverage.last_import || 'None')} UTC. ` +
    `<button class="secondary-btn" id="show-all-orders">Show all imported records</button> ` +
    `<button class="secondary-btn" id="refresh-report">Refresh report</button>` +
    `</div>` +
    (selectedSkus.length ? `<div class="notice info">Selected SKUs: <strong>${selectedSkus.map(k => escapeHTML(data.sku_options.find(o => o.value === k)?.label || k)).join(', ')}</strong> · <button class="secondary-btn" id="clear-sku">Show all SKUs</button></div>` : '') +
    `<div class="context-line">` +
    `<span>${fmtDate(data.filters.start)} – ${fmtDate(data.filters.end)} orders · Settlements through ${fmtDate(data.filters.as_of)}</span>` +
    `<span>Settlement transactions imported: ${fmtDate(data.coverage.first_payment)} – ${fmtDate(data.coverage.last_payment)}</span>` +
    adsCov +
    `</div>`;
}

function badge(val) {
  const s = String(val || '');
  let cls = '';
  if (/RTO|EXCHANGE|lost/i.test(s)) cls = 'purple';
  else if (/RETURN|Negative|Rejected|damaged/i.test(s)) cls = 'red';
  else if (/Awaiting|Overdue|Within|Deferred/i.test(s)) cls = 'warn';
  else if (/CANCEL|Excluded/i.test(s)) cls = 'neutral';
  else if (/Delivered|Positive|Settlement recorded|sellable|resellable/i.test(s)) cls = 'green';
  let label = s.replaceAll('_', ' ');
  if (s.toLowerCase() === 'sellable') label = 'Resellable (In Stock)';
  else if (s.toLowerCase() === 'damaged') label = 'Damaged (Cost Retained)';
  else if (s.toLowerCase() === 'lost') label = 'Lost (Cost Retained)';
  return `<span class="badge ${cls}">${escapeHTML(label)}</span>`;
}

function metric(label, value, note, filter, hero = false) {
  return `<button class="metric ${hero ? 'hero' : ''}" data-drill="${filter}">` +
    `<span class="label">${label}<span>↗</span></span>` +
    `<strong class="value">${value}</strong>` +
    `<span class="note">${note}</span>` +
    `</button>`;
}

function costRow(label, value, filter = '', total = false) {
  return `<div class="cost-row ${total ? 'total' : ''}">` +
    `<button ${filter ? `data-drill="${filter}"` : ''}>${label}</button>` +
    `<span class="${value < 0 ? 'negative' : 'positive'}">${value === null ? 'Incomplete' : inr(value)}</span>` +
    `</div>`;
}

function trend() {
  const rows = data.timeline;
  if (!rows.length) return '<div class="empty">Import orders and settlements to see daily trends.</div>';
  const high = Math.max(...rows.map(r => r.settlement), 1);
  const low = Math.min(0, ...rows.map(r => r.settlement));
  const x = i => 75 + i * 655 / Math.max(rows.length - 1, 1);
  const y = v => 165 - (v - low) / (high - low) * 140;
  const pts = rows.map((r, i) => [x(i), y(r.settlement)]);
  const line = pts.map(p => p.join(',')).join(' ');

  const ticks = [0, 1, 2, 3].map(i => {
    const value = low + (high - low) * (3 - i) / 3;
    const cy = y(value);
    return `<line x1="75" x2="745" y1="${cy}" y2="${cy}" stroke="#e2e8f0" stroke-dasharray="3 4"/>` +
      `<text x="68" y="${cy + 4}" text-anchor="end">${escapeHTML(inr(Math.round(value)))}</text>`;
  }).join('');

  const labels = rows.filter((r, i) => i % Math.max(1, Math.ceil(rows.length / 7)) === 0).map(r =>
    `<text x="${x(rows.indexOf(r))}" y="198" text-anchor="middle">${r.date.slice(8)}/${r.date.slice(5, 7)}</text>`
  ).join('');

  const points = rows.map((r, i) => {
    const label = `${fmtDate(r.date)}: ${inr(r.settlement)} settlement, ${number(r.orders)} orders`;
    return `<g class="chart-point" tabindex="0" role="button" aria-label="${escapeHTML(label)}" data-chart-index="${i}">` +
      `<title>${escapeHTML(label)}</title>` +
      `<rect x="${x(i) - 14}" y="15" width="28" height="160" fill="transparent"/>` +
      `<circle cx="${x(i)}" cy="${y(r.settlement)}" r="3.5" fill="#ea580c"/>` +
      `</g>`;
  }).join('');

  return `<svg class="chart" viewBox="0 0 780 215">${ticks}<polyline points="${line}" fill="none" stroke="#ea580c" stroke-width="2.5"/>${labels}${points}</svg>` +
    `<div id="chart-detail" class="chart-detail">Hover or tap any day to see its settlement and order volume.</div>` +
    `<div class="legend"><span><i></i>Net settlement received, grouped by order purchase date</span></div>`;
}

function showChartValue(e) {
  const point = e.target.closest('[data-chart-index]');
  if (!point) return;
  const r = data.timeline[Number(point.dataset.chartIndex)];
  if (r && $('#chart-detail')) {
    $('#chart-detail').textContent = `${fmtDate(r.date)} · Net Settlement: ${inr(r.settlement)} · Orders: ${number(r.orders)} · Listed Value: ${inr(r.listed_value)}`;
  }
}
for (const ev of ['pointerover', 'focusin', 'click']) document.addEventListener(ev, showChartValue);

function renderDashboard() {
  const s = data.summary;
  if (!data.orders.length) {
    $('#content').innerHTML = context() +
      `<div class="panel empty"><h2>${data.coverage.last_order ? 'No orders in this period.' : 'Start with your Amazon reports.'}</h2>` +
      `<p>${data.coverage.last_order ? 'Change dates to view other orders.' : 'Import your Orders report, Custom Unified Transactions CSV, Returns report, and Ads performance.'}</p>` +
      `<button class="primary" data-nav="imports">Go to Import files</button></div>`;
    return;
  }

  const incomplete = s.missing_cost || s.missing_packaging;
  const alerts = incomplete ?
    `<div class="notice cost-warning" role="alert">Provisional profit: ${number(s.missing_cost)} orders need product costs and ${number(s.missing_packaging)} need packaging costs. <a href="#products" data-nav="products">Set your costs →</a></div>` : '';

  const adsAlert = s.ads_outside_period > 0 ?
    `<div class="notice info">📢 <strong>${inr(s.ads_outside_period)} of imported advertising spend</strong> is for ${fmtDate(data.coverage.first_ad)} – ${fmtDate(data.coverage.last_ad)}, which is outside your currently viewed order dates (${fmtDate(data.filters.start)} – ${fmtDate(data.filters.end)}). <button class="primary" id="include-ads-range">Include ad dates</button></div>` : '';

  const fin = costRow('Net settlement received', s.settlement, 'recorded') +
    costRow('Advertising spend (attributed)', -s.ads, 'ads') +
    (s.service_fees ? costRow('Account service & FBA fees', s.service_fees, 'fees') : '') +
    costRow('Product purchase cost' + (s.missing_cost ? ' (known only)' : ''), -s.product_cost, 'cost') +
    costRow('Packaging cost' + (s.missing_packaging ? ' (known only)' : ''), -s.packaging, 'packaging') +
    costRow('Net Profit', s.net_profit, 'all', true);

  const states = `<div><strong>${number(s.delivered)}</strong><small>Delivered</small></div>` +
    `<div><strong>${number(s.rto)}</strong><small>RTO (Courier/Rejected)</small></div>` +
    `<div><strong>${number(s.returns)}</strong><small>Customer Returns</small></div>` +
    `<div><strong>${number(s.shipped)}</strong><small>In-transit / Shipped</small></div>`;

  const top = data.sku_performance.slice(0, 5);
  const topmax = Math.max(...top.map(r => Math.abs(r.profit_after_ads)), 1);
  const bars = top.length ? top.map(r =>
    `<div class="cost-row"><span>${escapeHTML(r.sku)}</span><b>${inr(r.profit_after_ads)}</b></div>`
  ).join('') : '<p class="empty">Set costs to view SKU ranking.</p>';

  const deductions = costRow('Gross product sales', s.product_sales) +
    costRow('Shipping credits', s.shipping_credits) +
    costRow('Easy Ship weight handling fees', s.easy_ship_fees) +
    costRow('Selling & referral fees', s.selling_fees) +
    costRow('Refunds & customer credits', s.refunds) +
    (s.safet ? costRow('SAFE-T reimbursements', s.safet) : '') +
    costRow('GST tax liable', s.taxes) +
    costRow('TCS & TDS', s.tcs_tds);

  $('#content').innerHTML = context() + alerts + adsAlert +
    `<div class="metric-grid">` +
    metric('Net Profit', s.net_profit === null ? 'Incomplete' : inr(s.net_profit), s.provisional ? 'Provisional · review costs' : 'After all costs & ads', 'all', true) +
    metric('Net Settlement', inr(s.settlement), `${number(s.recorded)} orders settled`, 'recorded') +
    metric('Product & Packaging', inr(s.product_cost + s.packaging), incomplete ? 'Some costs missing' : 'For retained units', 'cost') +
    metric('Awaiting settlement', number(s.awaiting), `${number(s.overdue)} overdue 14+ days`, 'awaiting') +
    `</div>` +
    `<div class="two-col">${panel('Settlement over time', 'Daily net settlement grouped by purchase date.', trend())}${panel('Your cash earnings, explained', 'Net settlement − ads − product cost − packaging', fin)}</div>` +
    `<div class="two-col">${panel('Orders overview', `${number(s.orders)} non-cancelled orders · ${number(s.cancelled)} cancelled excluded`, `<div class="stat-list">${states}</div><div class="cost-row" style="margin-top:20px"><span>Customer return rate</span><b>${s.customer_return_rate}%</b></div><div class="cost-row"><span>RTO rate</span><b>${s.rto_rate}%</b></div>`)}${panel('Top products by net profit', 'After product costs, packaging and attributed ad spend.', bars)}</div>` +
    `<div class="two-col">${panel('Settlement components breakdown', 'Components that add up to net settlement.', deductions)}${panel('GST and report preferences', 'Tax calculation treatment.', `<div class="form-row"><label class="form-label">GST Rate (%)<input type="number" id="gst-rate" value="${escapeHTML(data.settings.gst_rate || '18')}"></label><label class="form-label">Settlement cutoff days<input type="number" id="cutoff-days" value="${escapeHTML(data.settings.cutoff_days || '14')}"></label><label style="display:flex;align-items:center;gap:8px"><input type="checkbox" id="include-gst" ${data.settings.include_gst === 'true' ? 'checked' : ''}> Include GST in purchase cost</label><button id="save-gst" class="primary">Save preferences</button></div>`)}</div>`;
}

function filteredOrders() {
  return data.orders.filter(o => {
    if (currentPage === 'awaiting' && (o.payment_count || o.status === 'CANCELLED')) return false;
    if (orderFilter === 'recorded' && (!o.payment_count || o.status === 'CANCELLED')) return false;
    if (orderFilter === 'cost' && !o.costs_missing) return false;
    if (search && !(o.sku + ' ' + o.amazon_order_id + ' ' + (o.ship_city || '')).toLowerCase().includes(search.toLowerCase())) return false;
    if (statusFilter && o.status !== statusFilter) return false;
    if (paymentFilter && o.payment_state !== paymentFilter) return false;
    return true;
  });
}

function renderOrders() {
  const awaiting = currentPage === 'awaiting';
  const s = data.summary;
  const intro = awaiting ?
    `<div class="mini-grid">` +
    `<div><span>Awaiting settlement</span><strong>${number(s.awaiting)}</strong></div>` +
    `<div><span>Overdue (14+ days)</span><strong>${number(s.overdue)}</strong></div>` +
    `<div><span>Within window</span><strong>${number(s.awaiting - s.overdue)}</strong></div>` +
    `<div><span>Listed value</span><strong>${inr(s.listed_value)}</strong></div>` +
    `</div>` : '';

  $('#content').innerHTML = context() + intro +
    `<section class="panel">` +
    `<div class="panel-head">` +
    `<div><h2>${awaiting ? 'Orders awaiting settlement' : 'Orders financial detail'}</h2><p>Click any Amazon Order ID to inspect linked transactions, fees, and recovery.</p></div>` +
    `<a class="nowrap" href="/api/export?${query()}${awaiting ? '&awaiting=true' : ''}">↓ Export CSV</a>` +
    `</div>` +
    `<div class="toolbar">` +
    `<input type="search" id="order-search" placeholder="Search order ID, SKU, or city…" value="${escapeHTML(search)}">` +
    `<select id="order-status"><option value="">All statuses</option>${[...new Set(data.orders.map(o => o.status))].sort().map(v => `<option ${statusFilter === v ? 'selected' : ''}>${escapeHTML(v)}</option>`).join('')}</select>` +
    `<select id="payment-status"><option value="">All payment states</option>${[...new Set(data.orders.map(o => o.payment_state))].sort().map(v => `<option ${paymentFilter === v ? 'selected' : ''}>${escapeHTML(v)}</option>`).join('')}</select>` +
    `${orderFilter ? `<button class="secondary-btn" id="clear-order-filter">Clear ${escapeHTML(orderFilter)} filter</button>` : ''}` +
    `</div>` +
    `<div id="orders-table"></div>` +
    `</section>`;
  renderOrderTable();
}

function renderOrderTable() {
  const rows = filteredOrders();
  const per = 40;
  tablePage = Math.min(tablePage, Math.max(0, Math.ceil(rows.length / per) - 1));
  const visible = rows.slice(tablePage * per, (tablePage + 1) * per);

  $('#orders-table').innerHTML = `<div class="table-wrap"><table>` +
    `<thead><tr><th>Order ID / Item ID</th><th>SKU</th><th>Date</th><th>Qty</th><th>Status</th><th>Settlement state</th><th class="num">Net Settlement</th><th class="num">Product cost</th><th class="num">Packaging</th><th class="num">Contribution</th></tr></thead>` +
    `<tbody>${visible.map(o => `<tr>` +
      `<td><button class="id-button" data-order="${escapeHTML(o.amazon_order_id)}">${escapeHTML(o.amazon_order_id)}</button><div class="details">${escapeHTML(o.order_item_id)}</div></td>` +
      `<td class="sku">${escapeHTML(o.sku)}</td>` +
      `<td class="nowrap">${fmtDate(o.purchase_date)}<div class="details">Due ${fmtDate(o.due_date)}</div></td>` +
      `<td>${o.quantity}</td>` +
      `<td>${badge(o.status)}</td>` +
      `<td>${badge(o.payment_state)}<div class="details">${o.latest_trans_date ? fmtDate(o.latest_trans_date) : 'No settlement yet'}</div></td>` +
      `<td class="num">${inr(o.settlement)}</td>` +
      `<td class="num">${o.product_cost === null ? '<strong class="missing-cost">Missing cost</strong>' : inr(o.product_cost)}</td>` +
      `<td class="num">${o.packaging === null ? '<strong class="missing-cost">Missing cost</strong>' : inr(o.packaging)}</td>` +
      `<td class="num ${o.contribution < 0 ? 'negative' : 'positive'}">${inr(o.contribution)}${o.costs_missing ? '<div class="missing-cost">Provisional</div>' : ''}</td>` +
      `</tr>`).join('') || '<tr><td colspan="10" class="empty">No matching orders found.</td></tr>'}` +
    `</tbody></table></div>` +
    `<div class="pager">` +
    `<span>${number(rows.length)} orders</span>` +
    `<div><button data-order-page="-1" ${tablePage === 0 ? 'disabled' : ''}>←</button> ${tablePage + 1} / ${Math.max(1, Math.ceil(rows.length / per))} <button data-order-page="1" ${(tablePage + 1) * per >= rows.length ? 'disabled' : ''}>→</button></div>` +
    `</div>`;
}

function renderPerformance() {
  const rows = data.sku_performance;
  $('#content').innerHTML = context() +
    `<div class="notice info">Profit After Ads = Net Settlement − Product Cost − Packaging − Attributed Ad Spend. Missing costs make SKU results provisional.</div>` +
    `<section class="panel">` +
    `<div class="panel-head"><div><h2>SKU profitability & ad ROI</h2><p>Sorted by true profit after advertising spend.</p></div></div>` +
    `<div class="table-wrap"><table>` +
    `<thead><tr><th>SKU</th><th class="num">Orders</th><th class="num">Units</th><th class="num">Net Settlement</th><th class="num">Product Cost</th><th class="num">Packaging</th><th class="num">Profit before ads</th><th class="num">Ad Spend</th><th class="num">Profit after ads</th><th>Returns</th><th>RTO</th><th>Pending</th></tr></thead>` +
    `<tbody>${rows.map(r => `<tr>` +
      `<td class="sku"><button class="id-button" data-performance-sku="${escapeHTML(r.sku_key)}">${escapeHTML(r.sku)}</button></td>` +
      `<td class="num">${number(r.orders)}</td>` +
      `<td class="num">${number(r.units)}</td>` +
      `<td class="num">${inr(r.settlement)}</td>` +
      `<td class="num">${inr(r.product_cost)}</td>` +
      `<td class="num">${inr(r.packaging)}</td>` +
      `<td class="num ${r.contribution < 0 ? 'negative' : 'positive'}">${inr(r.contribution)}</td>` +
      `<td class="num negative">${inr(r.ad_spend)}</td>` +
      `<td class="num ${r.profit_after_ads < 0 ? 'negative' : 'positive'}"><strong>${inr(r.profit_after_ads)}</strong>${r.provisional ? '<div class="missing-cost">Provisional</div>' : ''}</td>` +
      `<td>${number(r.returns)}</td>` +
      `<td>${number(r.rto)}</td>` +
      `<td>${number(r.awaiting)}</td>` +
      `</tr>`).join('') || '<tr><td colspan="12" class="empty">No product data for selected period.</td></tr>'}` +
    `</tbody></table></div></section>`;
}

function renderReturns() {
  const allReturns = data.orders.filter(o => o.status === 'RTO' || o.status === 'RETURN' || (o.returns_info && o.returns_info.length > 0));
  const deliveredCount = allReturns.filter(o => o.returns_info && o.returns_info.some(r => r.delivery_date)).length;
  const inTransitCount = allReturns.length - deliveredCount;

  $('#content').innerHTML = context() +
    `<div class="mini-grid">` +
    `<div><span>RTO (Undelivered/Rejected)</span><strong>${number(data.summary.rto)}</strong></div>` +
    `<div><span>Customer Returns</span><strong>${number(data.summary.returns)}</strong></div>` +
    `<div class="interactive ${returnDeliveryFilter === 'delivered' ? 'active' : ''}" data-return-filter="delivered" role="button" tabindex="0">` +
    `<span>Received by Seller</span><strong class="positive">${number(deliveredCount)}</strong>` +
    `<div class="details">${returnDeliveryFilter === 'delivered' ? '✓ Filter active (click to reset)' : 'Click to filter received'}</div>` +
    `</div>` +
    `<div class="interactive ${returnDeliveryFilter === 'waiting' ? 'active' : ''}" data-return-filter="waiting" role="button" tabindex="0">` +
    `<span>In-Transit to Seller</span><strong class="warn">${number(inTransitCount)}</strong>` +
    `<div class="details">${returnDeliveryFilter === 'waiting' ? '✓ Filter active (click to reset)' : 'Click to filter waiting'}</div>` +
    `</div>` +
    `</div>` +
    `<div class="notice info">All returned and RTO goods default to <b>Resellable (In Stock)</b> with <b>₹0 product cost charged</b>. Packaging is retained. Check <b>In-Transit</b> vs <b>Delivered</b> dates to monitor returned inventory.</div>` +
    `<section class="panel">` +
    `<div class="panel-head"><div><h2>Returns & RTO management</h2><p>Click any Order ID to view details, or use the quick action buttons to mark damaged items.</p></div></div>` +
    `<div class="toolbar">` +
    `<input type="search" id="return-search" placeholder="Search order ID, SKU, tracking AWB…" value="${escapeHTML(returnSearch)}">` +
    `<select id="return-delivery-filter">` +
    `<option value="">All return logistics (${number(allReturns.length)})</option>` +
    `<option value="delivered" ${returnDeliveryFilter === 'delivered' ? 'selected' : ''}>Received / Delivered (${number(deliveredCount)})</option>` +
    `<option value="waiting" ${returnDeliveryFilter === 'waiting' ? 'selected' : ''}>Still waiting / In-transit (${number(inTransitCount)})</option>` +
    `</select>` +
    `${(returnDeliveryFilter || returnSearch) ? `<button class="secondary-btn" id="clear-return-filter">Clear filter</button>` : ''}` +
    `</div>` +
    `<div id="returns-table-wrap"></div>` +
    `</section>`;
  renderReturnsTable();
}

function renderReturnsTable() {
  const allReturns = data.orders.filter(o => o.status === 'RTO' || o.status === 'RETURN' || (o.returns_info && o.returns_info.length > 0));
  const ordersWithReturns = allReturns.filter(o => {
    const isDelivered = o.returns_info && o.returns_info.some(r => r.delivery_date);
    if (returnDeliveryFilter === 'delivered' && !isDelivered) return false;
    if (returnDeliveryFilter === 'waiting' && isDelivered) return false;
    if (returnSearch) {
      const q = returnSearch.toLowerCase();
      const matchOrder = (o.amazon_order_id || '').toLowerCase().includes(q);
      const matchSku = (o.sku || '').toLowerCase().includes(q);
      const matchRet = (o.returns_info || []).some(r =>
        (r.tracking_id || '').toLowerCase().includes(q) ||
        (r.return_reason || '').toLowerCase().includes(q) ||
        (r.detailed_disposition || '').toLowerCase().includes(q)
      );
      if (!matchOrder && !matchSku && !matchRet) return false;
    }
    return true;
  });

  const per = 30;
  returnsPage = Math.min(returnsPage, Math.max(0, Math.ceil(ordersWithReturns.length / per) - 1));
  const visible = ordersWithReturns.slice(returnsPage * per, (returnsPage + 1) * per);

  $('#returns-table-wrap').innerHTML = `<div class="table-wrap"><table>` +
    `<thead><tr><th>Order ID</th><th>SKU</th><th>Order Date</th><th>Status</th><th>Return Logistics & Delivery</th><th>Condition</th><th class="num">Net Cash</th><th class="num">Cost Charged</th><th>Quick action</th></tr></thead>` +
    `<tbody>${visible.map(o => {
      const ret = (o.returns_info && o.returns_info[0]) || {};
      const isSellable = o.recovery === 'sellable' || !o.recovery;
      const deliveryStatus = ret.delivery_date ?
        `<span class="badge green">Delivered: ${fmtDate(ret.delivery_date)}</span>` :
        (ret.return_date ? `<span class="badge warn">In-transit (Not received)</span>` : '');
      return `<tr>` +
        `<td><button class="id-button" data-order="${escapeHTML(o.amazon_order_id)}">${escapeHTML(o.amazon_order_id)}</button></td>` +
        `<td class="sku">${escapeHTML(o.sku)}</td>` +
        `<td class="nowrap">${fmtDate(o.purchase_date)}</td>` +
        `<td>${badge(o.status)}</td>` +
        `<td>${ret.return_type ? badge(ret.return_type) : ''} ${deliveryStatus}<div class="details">${escapeHTML(ret.return_reason || 'Return record')}${ret.tracking_id ? ` · AWB: ${escapeHTML(ret.tracking_id)}` : ''}</div></td>` +
        `<td>${badge(o.recovery || 'sellable')}</td>` +
        `<td class="num">${inr(o.settlement)}</td>` +
        `<td class="num">${inr(o.product_cost)}</td>` +
        `<td>` +
        (isSellable ?
          `<button class="secondary-btn" data-quick-recovery="${escapeHTML(o.amazon_order_id)}" data-quick-state="damaged">Mark Damaged</button>` :
          `<button class="secondary-btn" data-quick-recovery="${escapeHTML(o.amazon_order_id)}" data-quick-state="sellable">Mark Resellable</button>`
        ) +
        `</td>` +
        `</tr>`;
    }).join('') || '<tr><td colspan="9" class="empty">No returns match the selected filter.</td></tr>'}` +
    `</tbody></table></div>` +
    `<div class="pager">` +
    `<span>${number(ordersWithReturns.length)} returned/RTO items ${returnDeliveryFilter ? `(${returnDeliveryFilter === 'delivered' ? 'Received / Delivered' : 'Still waiting / In-transit'})` : ''}</span>` +
    `<div><button data-returns-page="-1" ${returnsPage === 0 ? 'disabled' : ''}>←</button> ${returnsPage + 1} / ${Math.max(1, Math.ceil(ordersWithReturns.length / per))} <button data-returns-page="1" ${(returnsPage + 1) * per >= ordersWithReturns.length ? 'disabled' : ''}>→</button></div>` +
    `</div>`;
}

async function renderProducts() {
  const chosen = $('#product-date')?.value || localToday();
  productRows = await api('/api/products?date=' + chosen);

  $('#content').innerHTML = `<section class="panel">` +
    `<div class="panel-head"><div><h2>Product cost book</h2><p>${number(productRows.length)} SKUs · Purchase costs include GST. Packaging is charged once per order.</p></div><button class="primary" id="add-product">+ Add SKU manually</button></div>` +
    `<div class="toolbar">` +
    `<input type="search" id="product-search" placeholder="Find SKU…" value="${escapeHTML(productSearch)}">` +
    `<select id="product-completeness"><option value="">All products</option><option value="cost" ${productCompleteness === 'cost' ? 'selected' : ''}>Missing purchase cost</option><option value="packaging" ${productCompleteness === 'packaging' ? 'selected' : ''}>Missing packaging</option></select>` +
    `<select id="product-status-filter"><option value="active" ${productStatus === 'active' ? 'selected' : ''}>Active SKUs</option><option value="disabled" ${productStatus === 'disabled' ? 'selected' : ''}>Disabled SKUs</option><option value="all" ${productStatus === 'all' ? 'selected' : ''}>All SKUs</option></select>` +
    `<label class="form-label">View prices on<input type="date" id="product-date" value="${chosen}"></label>` +
    `</div>` +
    `<div class="bulk-box"><div class="form-row"><label class="form-label">Packaging per order (₹)<input type="number" id="bulk-packaging" placeholder="5.00" min="0" step=".01"></label><button class="secondary-btn" id="preview-bulk">Apply bulk packaging</button><p>Applies to all active SKUs across all historical and future orders.</p></div></div>` +
    `<div id="product-table"></div>` +
    `</section>`;
  renderProductTable();
}

function renderProductTable() {
  const rows = productRows.filter(r =>
    (productStatus === 'all' || (productStatus === 'disabled' ? r.disabled : !r.disabled)) &&
    r.display_sku.toLowerCase().includes(productSearch.toLowerCase()) &&
    (!productCompleteness || r[productCompleteness] === null)
  );
  const per = 35;
  productPage = Math.min(productPage, Math.max(0, Math.ceil(rows.length / per) - 1));
  const visible = rows.slice(productPage * per, (productPage + 1) * per);

  $('#product-table').innerHTML = `<div class="table-wrap"><table>` +
    `<thead><tr><th>SKU</th><th class="num">Purchase Cost (₹)</th><th class="num">Packaging (₹)</th><th>Price applies from</th><th>Actions</th></tr></thead>` +
    `<tbody>${visible.map(r => `<tr>` +
      `<td class="sku">${escapeHTML(r.display_sku)}${r.disabled ? ' <span class="badge neutral">Disabled</span>' : ''}</td>` +
      `<td class="num"><input class="product-input" type="number" min="0" step=".01" data-cost value="${r.cost === null ? '' : (r.cost / 100).toFixed(2)}" placeholder="Not set">${r.cost === null ? '<div class="missing-cost">Missing cost</div>' : ''}</td>` +
      `<td class="num"><input class="product-input" type="number" min="0" step=".01" data-pack value="${r.packaging === null ? '' : (r.packaging / 100).toFixed(2)}" placeholder="Not set"></td>` +
      `<td><input type="date" data-apply-date value="${escapeHTML($('#product-date').value)}"><div class="details">Current version: ${fmtDate(r.effective_date)}</div>${r.initial_cost_applied ? '<div class="details">First saved price applied</div>' : ''}</td>` +
      `<td class="nowrap"><button class="primary" data-save-product="${escapeHTML(r.sku)}">Save</button> <button class="secondary-btn" data-product-history="${escapeHTML(r.sku)}">History</button> <button class="secondary-btn" data-toggle-product="${escapeHTML(r.sku)}" data-disabled="${r.disabled ? 'false' : 'true'}">${r.disabled ? 'Enable' : 'Disable'}</button></td>` +
      `</tr>`).join('') || '<tr><td colspan="5" class="empty">No products found. Add a SKU or import a workbook.</td></tr>'}` +
    `</tbody></table></div>` +
    `<div class="pager">` +
    `<span>${number(rows.length)} SKUs</span>` +
    `<div><button data-product-page="-1" ${productPage === 0 ? 'disabled' : ''}>←</button> ${productPage + 1} / ${Math.max(1, Math.ceil(rows.length / per))} <button data-product-page="1" ${(productPage + 1) * per >= rows.length ? 'disabled' : ''}>→</button></div>` +
    `</div>`;
}

function renderReconciliation() {
  const issuesGrouped = Object.entries(data.issues.reduce((a, r) => (a[r.issue] = (a[r.issue] || 0) + 1, a), {}));

  const wastedTable = `<div class="table-wrap"><table>` +
    `<thead><tr><th>Search Term</th><th>Campaign</th><th class="num">Clicks</th><th class="num">Cost</th><th class="num">Sales</th></tr></thead>` +
    `<tbody>${data.wasted_search_terms.map(w => `<tr><td><strong>${escapeHTML(w.search_term)}</strong></td><td>${escapeHTML(w.campaign_name)}</td><td class="num">${number(w.clicks)}</td><td class="num negative">${inr(w.cost)}</td><td class="num">₹0.00</td></tr>`).join('') || '<tr><td colspan="5">No wasted ad spend detected.</td></tr>'}` +
    `</tbody></table></div>`;

  const topKwTable = `<div class="table-wrap"><table>` +
    `<thead><tr><th>Search Term</th><th>Campaign</th><th class="num">Cost</th><th class="num">Sales</th><th class="num">ROAS</th></tr></thead>` +
    `<tbody>${data.top_search_terms.map(w => `<tr><td><strong>${escapeHTML(w.search_term)}</strong></td><td>${escapeHTML(w.campaign_name)}</td><td class="num">${inr(w.cost)}</td><td class="num positive">${inr(w.sales)}</td><td class="num"><strong>${Number(w.roas).toFixed(2)}x</strong></td></tr>`).join('') || '<tr><td colspan="5">No keyword data imported yet.</td></tr>'}` +
    `</tbody></table></div>`;

  $('#content').innerHTML = context() +
    panel('Reconciliation items to review', 'Differences and missing costs in the selected period.',
      issuesGrouped.map(([k, v]) => `<div class="cost-row"><span>${escapeHTML(k)}</span><b>${number(v)}</b></div>`).join('') || '<p>No reconciliation issues found in this period.</p>'
    ) +
    `<div class="two-col">${panel('Wasted Ad Spend (0 Sales)', 'Search terms with spend but 0 purchases. Add these as negative keywords.', wastedTable)}${panel('Top Winning Keywords', 'Search terms with highest return on ad spend (ROAS).', topKwTable)}</div>` +
    panel('Account fees & service charges', 'Non-order subscription and FBA fees.',
      (data.service_fees && data.service_fees.length ?
        data.service_fees.map(f => `<div class="cost-row"><span>${escapeHTML(f.description)} · ${fmtDate(f.post_date)}</span><b class="negative">${inr(f.total)}</b></div>`).join('') :
        '<p class="subtle">No non-ad operating service fees in this period.</p>'
      ) +
      (data.ad_invoices && data.ad_invoices.length ?
        `<div style="margin-top:20px;padding-top:14px;border-top:1px solid #e2e8f0"><div class="details" style="margin-bottom:8px"><strong>Advertising Invoices in Transaction Ledger (${inr(data.summary.ad_invoices || 0)})</strong><br>Billed directly to Seller Central account balance (accounted for under Advertising spend to avoid double counting):</div>` +
        data.ad_invoices.map(f => `<div class="cost-row"><span>${escapeHTML(f.description)} · ${fmtDate(f.post_date)}</span><span class="details">${inr(f.total)}</span></div>`).join('') +
        `</div>` : '')
    );
}

async function renderImports() {
  const imports = await api('/api/imports');
  const scans = await api('/api/scan-downloads');

  const scanSection = scans.length ?
    `<div class="scan-box">` +
    `<div><strong>⚡ Found ${number(scans.length)} Amazon report file(s) in your input folder (amazon/input/)!</strong><p class="details">Click to auto-import them directly without manual file choosing.</p></div>` +
    `<div><button class="primary" id="auto-import-all">Auto-Import Files from input/</button></div>` +
    `</div>` : '';

  $('#content').innerHTML = lastImportSummary + scanSection +
    panel('Upload Amazon files', 'Original files are stored with import history. Overlapping dates are safely upserted without duplication.',
      `<div class="upload-grid">` +
      [['orders', 'Orders Report', 'All Orders tab-delimited file (.txt / .tsv / .csv)', '.txt,.tsv,.csv'],
       ['transactions', 'Unified Transactions', 'Custom date range settlement CSV (.csv)', '.csv'],
       ['returns', 'Returns & RTO', 'Amazon Returns TSV / TXT (.tsv,.txt)', '.tsv,.txt,.csv'],
       ['ads', 'Advertising Reports', 'Advertised product, campaign or search terms (.csv)', '.csv'],
       ['products', 'Product Cost Book', 'Excel workbook with SKU and Cost Price (.xlsx)', '.xlsx']
      ].map(([id, label, desc, accept]) =>
        `<label class="upload-card">` +
        `<div class="upload-icon">${id === 'orders' ? '▤' : id === 'transactions' ? '⇄' : id === 'returns' ? '↺' : id === 'ads' ? '📢' : '▦'}</div>` +
        `<h3>${label}</h3><p>${desc}</p>` +
        `<input type="file" id="upload-${id}" accept="${accept}" ${id !== 'products' ? 'multiple' : ''} aria-label="${label}">` +
        `</label>`
      ).join('') +
      `</div>` +
      `<div class="form-row"><label class="form-label">Product cost effective date<input type="date" id="import-effective" value="0001-01-01"></label><p>Initial costs use 0001-01-01 to cover all historical orders.</p><button class="primary" id="preview-import">Preview selected files</button></div>` +
      `<div id="previews"></div>`
    ) +
    `<section class="panel"><div class="panel-head"><div><h2>Import history</h2><p>Saved import runs and idempotent record refresh history.</p></div></div>` +
    `<div class="table-wrap"><table><thead><tr><th>File name</th><th>Type</th><th>Imported at</th><th>Summary</th></tr></thead><tbody>` +
    imports.map(i => `<tr><td><strong>${escapeHTML(i.name)}</strong></td><td>${badge(i.kind)}</td><td>${escapeHTML(i.imported_at)} UTC</td><td>${escapeHTML(i.summary)}</td></tr>`).join('') ||
    '<tr><td colspan="4" class="empty">No files imported yet.</td></tr>' +
    `</tbody></table></div></section>`;

  renderPreviews();
}

function renderPreviews() {
  $('#previews').innerHTML = previews.map(p =>
    `<div class="upload-preview">` +
    `<b>${escapeHTML(p.name)}</b> ${p.already_imported ? badge('Re-upload · refreshing existing') : ''}<br>` +
    `Found <b>${number(p.total_records)}</b> records: ` +
    `<strong class="positive">${number(p.new_records)} new</strong>, ` +
    `<strong>${number(p.overlapping_records)} existing records will be safely refreshed (no duplicate counts).</strong>` +
    `${p.warnings.map(w => `<div class="details negative">${escapeHTML(w)}</div>`).join('')}` +
    `</div>`
  ).join('') +
  (previews.length ? `<button class="primary" id="commit-import">Save previewed files to database</button>` : '');
}

function fileBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(',')[1]);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

async function previewUploads() {
  const selected = ['orders', 'transactions', 'returns', 'ads', 'products'].flatMap(kind =>
    [...($('#upload-' + kind)?.files || [])].map(file => ({ kind: kind === 'ads' ? 'auto' : kind, file }))
  );
  if (!selected.length) throw new Error('Please choose at least one file to preview.');
  const btn = $('#preview-import');
  btn.disabled = true;
  btn.textContent = 'Reading files…';
  previews = [];
  try {
    for (const { kind, file } of selected) {
      previews.push(await api('/api/import/preview', {
        kind,
        name: file.name,
        payload: await fileBase64(file),
        effective_date: $('#import-effective').value
      }));
    }
    renderPreviews();
  } finally {
    btn.disabled = false;
    btn.textContent = 'Preview selected files';
  }
}

async function openOrder(id) {
  const detail = await api('/api/order?id=' + encodeURIComponent(id));
  const row = data.orders.find(r => r.amazon_order_id === id);
  $('#dialog-title').textContent = 'Amazon Order ' + id;

  $('#dialog-content').innerHTML = (row ?
    `<div class="mini-grid">` +
    `<div><span>SKU</span><strong>${escapeHTML(row.sku)}</strong></div>` +
    `<div><span>Net Settlement</span><strong>${inr(row.settlement)}</strong></div>` +
    `<div><span>Product Cost</span><strong>${inr(row.product_cost)}</strong></div>` +
    `<div><span>Contribution</span><strong class="${row.contribution < 0 ? 'negative' : 'positive'}">${inr(row.contribution)}</strong></div>` +
    `</div>` +
    `<div class="form-row">${badge(row.status)} ${badge(row.payment_state)} <span class="details">Location: ${escapeHTML(row.ship_city || '—')}, ${escapeHTML(row.ship_state || '—')}</span></div>` : '') +
    (detail.orders && detail.orders.length > 1 ?
      `<h3>Order items (${detail.orders.length})</h3>` +
      `<div class="table-wrap"><table>` +
      `<thead><tr><th>Item ID</th><th>SKU</th><th>Product</th><th class="num">Qty</th><th class="num">Item Price</th><th class="num">Subtotal</th></tr></thead>` +
      `<tbody>${detail.orders.map(it => `<tr>` +
        `<td>${escapeHTML(it.order_item_id)}</td>` +
        `<td class="sku">${escapeHTML(it.sku)}</td>` +
        `<td>${escapeHTML(it.product_name || '—')}</td>` +
        `<td class="num">${it.quantity}</td>` +
        `<td class="num">${inr(it.item_price)}</td>` +
        `<td class="num">${inr(it.item_price * it.quantity)}</td>` +
        `</tr>`).join('')}</tbody></table></div>` : '') +
    `<h3>Settlement transactions</h3>` +
    `<div class="table-wrap"><table>` +
    `<thead><tr><th>Date</th><th>Type</th><th>Description</th><th class="num">Sales</th><th class="num">Easy Ship</th><th class="num">Fees</th><th class="num">Total</th></tr></thead>` +
    `<tbody>${detail.transactions.map(t => `<tr>` +
      `<td class="nowrap">${fmtDate(t.post_date)}</td>` +
      `<td>${badge(t.type)}</td>` +
      `<td>${escapeHTML(t.description)}</td>` +
      `<td class="num">${inr(t.product_sales)}</td>` +
      `<td class="num negative">${inr(t.other_fees)}</td>` +
      `<td class="num negative">${inr(t.selling_fees)}</td>` +
      `<td class="num"><strong>${inr(t.total)}</strong></td>` +
      `</tr>`).join('') || '<tr><td colspan="7">No settlement records yet.</td></tr>'}` +
    `</tbody></table></div>` +
    (detail.returns && detail.returns.length > 0 ?
      `<h3>Return logistics & tracking</h3>` +
      `<div class="table-wrap"><table>` +
      `<thead><tr><th>Requested</th><th>Delivered to Seller</th><th>Type</th><th>Reason</th><th>Tracking (AWB)</th></tr></thead>` +
      `<tbody>${detail.returns.map(r => `<tr>` +
        `<td>${fmtDate(r.return_date)}</td>` +
        `<td>${r.delivery_date ? `<span class="badge green">Delivered: ${fmtDate(r.delivery_date)}</span>` : '<span class="badge warn">In-transit (Not received)</span>'}</td>` +
        `<td>${badge(r.return_type)}</td>` +
        `<td>${escapeHTML(r.return_reason || '—')}</td>` +
        `<td>${escapeHTML(r.tracking_id || '—')}</td>` +
        `</tr>`).join('')}</tbody></table></div>` : '') +
    (row && ['RTO', 'RETURN'].includes(row.status) ?
      `<h3>Product recovery state</h3>` +
      `<div class="form-row">` +
      `<label class="form-label">Recovery disposition<select id="recovery-state">` +
      `<option value="sellable" ${row.recovery === 'sellable' ? 'selected' : ''}>Recovered & Sellable (0 cost)</option>` +
      `<option value="awaiting" ${row.recovery === 'awaiting' ? 'selected' : ''}>Awaiting recovery</option>` +
      `<option value="damaged" ${row.recovery === 'damaged' ? 'selected' : ''}>Damaged (cost retained)</option>` +
      `<option value="lost" ${row.recovery === 'lost' ? 'selected' : ''}>Lost (cost retained)</option>` +
      `</select></label>` +
      `<label class="form-label">Effective Date<input type="date" id="recovery-date" value="${data.filters.as_of}"></label>` +
      `<label class="form-label">Note<input id="recovery-note" value="${escapeHTML(row.recovery_note)}" placeholder="Tracking or condition note"></label>` +
      `<button class="primary" data-save-recovery="${escapeHTML(id)}">Save disposition</button>` +
      `</div>` : '') +
    `<h3>Raw source audit rows</h3>` +
    [...detail.orders, ...detail.transactions, ...detail.returns].map(r =>
      `<details class="details"><summary>${escapeHTML(r.type || r.order_status || 'Record')} (${fmtDate(r.purchase_date || r.post_date || r.return_date)})</summary><pre>${escapeHTML(JSON.stringify(JSON.parse(r.raw), null, 2))}</pre></details>`
    ).join('');

  $('#detail-dialog').showModal();
}

document.addEventListener('click', e => run(async () => {
  const b = e.target.closest('button, a, [data-return-filter]');
  if (!b) return;
  if (b.dataset.page || b.dataset.nav) { e.preventDefault(); await navigate(b.dataset.page || b.dataset.nav); }
  if (b.id === 'header-import') await navigate('imports');
  if (b.id === 'apply-filters') { tablePage = 0; await refresh(); }
  if (b.id === 'reset-period') { $('#start').value = ''; tablePage = 0; await refresh(); }
  if (b.id === 'include-ads-range') {
    $('#mode').value = 'custom';
    $('#end').value = data.coverage.last_ad || localToday();
    $('#as-of').value = localToday();
    await refresh();
  }
  if (b.id === 'show-all-orders') {
    $('#mode').value = 'custom';
    $('#start').value = data.coverage.first_order || '';
    const latestDate = [data.coverage.last_order, data.coverage.last_payment, data.coverage.last_ad].filter(Boolean).sort().reverse()[0] || localToday();
    $('#end').value = latestDate;
    $('#as-of').value = localToday();
    await refresh();
  }
  if (b.id === 'refresh-report') await refresh();
  if (b.id === 'clear-sku') { selectedSkus = []; draftSkus.clear(); await refresh(); }
  if (b.dataset.order) await openOrder(b.dataset.order);
  if (b.id === 'close-dialog') $('#detail-dialog').close();
  if (b.dataset.orderPage) { tablePage += Number(b.dataset.orderPage); renderOrderTable(); }
  if (b.dataset.returnsPage) { returnsPage += Number(b.dataset.returnsPage); renderReturnsTable(); }
  if (b.dataset.productPage) { productPage += Number(b.dataset.productPage); renderProductTable(); }
  if (b.dataset.returnFilter) {
    const f = b.dataset.returnFilter;
    returnDeliveryFilter = (returnDeliveryFilter === f) ? '' : f;
    returnsPage = 0;
    renderReturns();
  }
  if (b.id === 'clear-return-filter') {
    returnDeliveryFilter = '';
    returnSearch = '';
    returnsPage = 0;
    renderReturns();
  }
  if (b.id === 'clear-order-filter') { orderFilter = ''; renderOrders(); }
  if (b.dataset.drill) {
    if (b.dataset.drill === 'awaiting') await navigate('awaiting');
    else if (b.dataset.drill === 'ads') await navigate('reconciliation');
    else { await navigate('orders'); orderFilter = b.dataset.drill; renderOrders(); }
  }
  if (b.dataset.performanceSku) {
    selectedSkus = [b.dataset.performanceSku];
    await navigate('dashboard');
  }
  if (b.id === 'preview-import') await previewUploads();
  if (b.id === 'commit-import') {
    b.disabled = true;
    try {
      const res = await api('/api/import/commit', { tokens: previews.map(p => p.token) });
      const added = res.imported.reduce((n, r) => n + (r.added_records || 0), 0);
      const updated = res.imported.reduce((n, r) => n + (r.updated_records || 0), 0);
      lastImportSummary = `<div class="notice info">Import complete: ${number(added)} records added, ${number(updated)} existing records refreshed. <button class="secondary-btn" data-nav="dashboard">Open Overview</button></div>`;
      previews = [];
      await refresh();
      toast('Imports successfully saved to database.');
    } finally {
      b.disabled = false;
    }
  }
  if (b.id === 'auto-import-all') {
    const scans = await api('/api/scan-downloads');
    let count = 0;
    for (const f of scans) {
      await api('/api/import/scan-load', { path: f.path });
      count++;
    }
    toast(`Imported ${count} file(s) from input/ folder!`);
    await refresh();
  }
  if (b.id === 'save-gst') {
    await api('/api/settings', {
      gst_rate: $('#gst-rate').value,
      include_gst: $('#include-gst').checked,
      cutoff_days: $('#cutoff-days').value
    });
    await refresh();
    toast('Settings saved.');
  }
  if (b.dataset.saveProduct) {
    const tr = b.closest('tr');
    await api('/api/products', {
      effective_date: tr.querySelector('[data-apply-date]').value,
      updates: [{
        sku: b.dataset.saveProduct,
        ...(tr.querySelector('[data-cost]').dataset.changed === 'true' ? { cost: tr.querySelector('[data-cost]').value } : {}),
        packaging: tr.querySelector('[data-pack]').value
      }]
    });
    await refresh();
    toast('Product cost saved.');
  }
  if (b.dataset.productHistory) {
    const rows = await api('/api/product-history?sku=' + encodeURIComponent(b.dataset.productHistory));
    $('#dialog-title').textContent = 'Cost history';
    $('#dialog-content').innerHTML = `<table><thead><tr><th>Effective Date</th><th>Cost</th><th>Packaging</th><th>Source</th></tr></thead><tbody>` +
      rows.map(r => `<tr><td>${fmtDate(r.effective_date)}</td><td>${inr(r.cost)}</td><td>${inr(r.packaging)}</td><td>${escapeHTML(r.source)}</td></tr>`).join('') +
      `</tbody></table>`;
    $('#detail-dialog').showModal();
  }
  if (b.dataset.toggleProduct) {
    await api('/api/products/status', { sku: b.dataset.toggleProduct, disabled: b.dataset.disabled === 'true' });
    await renderProducts();
    toast('Product status updated.');
  }
  if (b.id === 'add-product') {
    $('#dialog-title').textContent = 'Add SKU manually';
    $('#dialog-content').innerHTML = `<div class="form-row">` +
      `<label class="form-label">SKU name<input id="new-sku" maxlength="500"></label>` +
      `<label class="form-label">Purchase Cost incl. GST (₹)<input id="new-cost" type="number" min="0" step=".01"></label>` +
      `<label class="form-label">Packaging per order (₹)<input id="new-packaging" type="number" min="0" step=".01"></label>` +
      `<label class="form-label">Applies from<input id="new-cost-date" type="date" value="${localToday()}"></label>` +
      `</div><button class="primary" id="create-product">Save new SKU</button>`;
    $('#detail-dialog').showModal();
  }
  if (b.id === 'create-product') {
    await api('/api/products/create', {
      sku: $('#new-sku').value,
      cost: $('#new-cost').value,
      packaging: $('#new-packaging').value,
      effective_date: $('#new-cost-date').value
    });
    $('#detail-dialog').close();
    await refresh();
    toast('New SKU added.');
  }
  if (b.dataset.saveRecovery) {
    await api('/api/recovery', {
      order_id: b.dataset.saveRecovery,
      state: $('#recovery-state').value,
      effective_date: $('#recovery-date').value,
      note: $('#recovery-note').value
    });
    $('#detail-dialog').close();
    await refresh();
    toast('Recovery disposition saved.');
  }
  if (b.dataset.quickRecovery) {
    await api('/api/recovery', {
      order_id: b.dataset.quickRecovery,
      state: b.dataset.quickState,
      effective_date: localToday(),
      note: 'Quick status change'
    });
    await refresh();
    toast(`Condition updated to ${b.dataset.quickState === 'sellable' ? 'Resellable' : 'Damaged'}.`);
  }
  if (b.id === 'preview-bulk') {
    const v = $('#bulk-packaging').value;
    if (v === '' || Number(v) < 0) throw new Error('Enter a valid packaging amount');
    await api('/api/products', {
      effective_date: $('#product-date').value,
      updates: productRows.filter(r => !r.disabled).map(r => ({ sku: r.sku, packaging: v }))
    });
    await refresh();
    toast('Bulk packaging updated for all active SKUs.');
  }
}));

document.addEventListener('input', e => {
  if (e.target.matches('[data-cost]')) e.target.dataset.changed = 'true';
  if (e.target.id === 'sku-search') renderSkuOptions();
  if (e.target.id === 'order-search') { search = e.target.value; tablePage = 0; renderOrderTable(); }
  if (e.target.id === 'product-search') { productSearch = e.target.value; productPage = 0; renderProductTable(); }
  if (e.target.id === 'return-search') { returnSearch = e.target.value; returnsPage = 0; renderReturnsTable(); }
});

document.addEventListener('change', e => run(async () => {
  if (e.target.id === 'mode') { $('#end').disabled = e.target.value === 'mature'; $('#start').value = ''; await refresh(); }
  if (e.target.id === 'as-of') { $('#start').value = ''; await refresh(); }
  if (e.target.id === 'end' && e.target.value) {
    const from = new Date(e.target.value + 'T12:00:00');
    from.setDate(from.getDate() - 29);
    $('#start').value = `${from.getFullYear()}-${String(from.getMonth() + 1).padStart(2, '0')}-${String(from.getDate()).padStart(2, '0')}`;
  }
  if (e.target.id === 'order-status') { statusFilter = e.target.value; tablePage = 0; renderOrderTable(); }
  if (e.target.id === 'payment-status') { paymentFilter = e.target.value; tablePage = 0; renderOrderTable(); }
  if (e.target.id === 'return-delivery-filter') { returnDeliveryFilter = e.target.value; returnsPage = 0; renderReturns(); }
  if (e.target.id === 'product-date') await renderProducts();
  if (e.target.id === 'product-status-filter') { productStatus = e.target.value; productPage = 0; renderProductTable(); }
  if (e.target.id === 'product-completeness') { productCompleteness = e.target.value; productPage = 0; renderProductTable(); }
  if (e.target.matches('[data-sku-choice]')) {
    const key = e.target.dataset.skuChoice;
    e.target.checked ? draftSkus.add(key) : draftSkus.delete(key);
  }
}));

function renderSkuPicker() {
  $('#sku-summary').textContent = selectedSkus.length ? `${selectedSkus.length} SKU${selectedSkus.length === 1 ? '' : 's'} selected` : 'All SKUs';
  renderSkuOptions();
}

function renderSkuOptions() {
  const term = ($('#sku-search')?.value || '').toLowerCase();
  const options = [...data.sku_options];
  $('#sku-options').innerHTML = options.filter(o => o.label.toLowerCase().includes(term)).map(o =>
    `<label class="sku-choice"><input type="checkbox" data-sku-choice="${escapeHTML(o.value)}" ${draftSkus.has(o.value) ? 'checked' : ''}><span>${escapeHTML(o.label)}</span></label>`
  ).join('') || '<p class="subtle">No matching SKUs.</p>';
}

document.addEventListener('click', e => run(async () => {
  if (e.target.id === 'apply-skus') { selectedSkus = [...draftSkus]; $('#sku-picker').open = false; await refresh(); }
  if (e.target.id === 'reset-skus') { selectedSkus = []; draftSkus.clear(); $('#sku-picker').open = false; await refresh(); }
}));

$('#as-of').value = localToday();
$('#mode').value = 'mature';
if (pageNames[location.hash.slice(1)]) currentPage = location.hash.slice(1);
run(refresh);
