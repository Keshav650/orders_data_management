'use strict';
const $ = s => document.querySelector(s);
const escapeHTML = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const inr = v => v === null || v === undefined ? 'Not set' : new Intl.NumberFormat('en-IN', {style:'currency', currency:'INR', maximumFractionDigits:2}).format((v || 0) / 100);
const number = v => new Intl.NumberFormat('en-IN').format(v || 0);
const localToday = () => {const d=new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;};
const fmtDate = v => !v ? '—' : v === '0001-01-01' ? 'All order dates' : new Date(v+'T00:00:00').toLocaleDateString('en-IN',{day:'2-digit',month:'short',year:'numeric'});
let data, currentPage='dashboard', productRows=[], previews=[], tablePage=0, productPage=0, orderFilter='', search='', statusFilter='', paymentFilter='', productSearch='', productCompleteness='', productStatus='active', lastImportSummary='', busy=false;
let selectedSkus=[], draftSkus=new Set();
const pageNames={performance:['Product Performance','Compare settlements, costs, and profit before ads for every SKU.','Product Performance'],dashboard:['Your business, at a glance.','Understand your earnings, costs, and the payments still to come.','Overview'],orders:['Every order. Every payment.','Follow an order from purchase to settlement, with the full financial detail.','Orders'],awaiting:['Payments to keep an eye on.','Orders without a payment record, separated by the 21-day window.','Awaiting payment'],products:['Your products & costs.','Manage GST-inclusive purchase prices and packaging for each SKU.','Products & costs'],reconciliation:['Make every record count.','Review differences, missing costs, and transactions that need attention.','Reconciliation'],imports:['Bring your records together.','Preview your files, check the results, and save them to your workspace.','Import files']};
async function api(path, body){
 const response=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 const result=await response.json(); if(!response.ok) throw new Error(result.error || 'Request failed'); return result;
}
function error(e){$('#error').textContent=e.message || e; $('#error').hidden=false;}
function clearError(){$('#error').hidden=true;}
function toast(message){$('#toast').textContent=message;$('#toast').hidden=false;setTimeout(()=>$('#toast').hidden=true,4500);}
async function run(action){clearError();try{await action();}catch(e){error(e);}}
function query(){const q=new URLSearchParams({start:$('#start').value,end:$('#end').value,as_of:$('#as-of').value,mode:$('#mode').value});selectedSkus.forEach(s=>q.append('skus',s));return q.toString();}
async function refresh(){
 data=await api('/api/report?'+query());
 selectedSkus=data.filters.skus||[];draftSkus=new Set(selectedSkus);renderSkuPicker();
 $('#start').value=data.filters.start;$('#end').value=data.filters.end;$('#as-of').value=data.filters.as_of;
 $('#end').disabled=$('#mode').value==='mature';$('#nav-pending').textContent=number(data.summary.awaiting);
 await renderPage();
}
async function navigate(page){currentPage=page; tablePage=0;orderFilter='';search='';statusFilter='';paymentFilter='';history.replaceState(null,'','#'+page);if(['dashboard','performance','orders','awaiting','reconciliation'].includes(page)){await refresh();}else{await renderPage();}}
async function renderPage(){
 const titles=pageNames[currentPage] || pageNames.dashboard;
 $('#page-title').textContent=titles[0];$('#page-description').textContent=titles[1];$('#breadcrumb').textContent=titles[2];
 document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('active',b.dataset.page===currentPage));
 $('#report-filters').hidden=['products','imports'].includes(currentPage);
 if(currentPage==='dashboard') renderDashboard();
 if(currentPage==='performance') renderPerformance();
 if(currentPage==='orders'||currentPage==='awaiting') renderOrders();
 if(currentPage==='products') await renderProducts();
 if(currentPage==='reconciliation') renderReconciliation();
 if(currentPage==='imports') await renderImports();
}
function panel(title,subtitle,body,extra=''){return `<section class="panel"><div class="panel-head"><div><h2>${title}</h2>${subtitle?`<p>${subtitle}</p>`:''}</div>${extra}</div><div class="panel-body">${body}</div></section>`;}
function context(){return `<div class="notice info">Imported orders: ${fmtDate(data.coverage.first_order)} – ${fmtDate(data.coverage.last_order)}. ${data.filters.mode==='mature'?'Default view excludes orders newer than '+fmtDate(data.filters.cutoff)+' (21-day settlement window).':''} Last import: ${escapeHTML(data.coverage.last_import||'None')} UTC. <button id="show-all-orders">Show all imported orders</button> <button id="refresh-report">Refresh report</button></div>`+(selectedSkus.length?`<div class="notice info">Selected SKUs: <strong>${selectedSkus.map(k=>escapeHTML(data.sku_options.find(o=>o.value===k)?.label||k)).join(', ')}</strong> · <button id="clear-sku">Show all SKUs</button></div>`:'')+`<div class="context-line"><span>${fmtDate(data.filters.start)} – ${fmtDate(data.filters.end)} orders · Payments through ${fmtDate(data.filters.as_of)}</span><span>Payment records imported: ${fmtDate(data.coverage.first_payment)} – ${fmtDate(data.coverage.last_payment)}</span></div>`;}
function badge(value){const s=String(value);const cls=/RTO|EXCHANGE/.test(s)?'purple':/RETURN|Negative/.test(s)?'red':/Overdue|Within|awaiting/.test(s)?'warn':/CANCEL|Excluded/.test(s)?'neutral':'';return `<span class="badge ${cls}">${escapeHTML(s.replaceAll('_',' '))}</span>`;}
function metric(label,value,note,filter,hero=false){return `<button class="metric ${hero?'hero':''}" data-drill="${filter}"><span class="label">${label}<span>↗</span></span><strong class="value">${value}</strong><span class="note">${note}</span></button>`;}
function costRow(label,value,filter='',total=false){return `<div class="cost-row ${total?'total':''}"><button ${filter?`data-drill="${filter}"`:''}>${label}</button><span class="${value<0?'negative':''}">${value===null?'Incomplete':inr(value)}</span></div>`;}
function trend(){
 const rows=data.timeline;if(!rows.length)return '<div class="empty">Import orders to see daily trends.</div>';
 const high=Math.max(...rows.map(r=>r.settlement),1),low=Math.min(0,...rows.map(r=>r.settlement));
 const x=i=>75+i*655/Math.max(rows.length-1,1),y=v=>165-(v-low)/(high-low)*140;
 const pts=rows.map((r,i)=>[x(i),y(r.settlement)]),line=pts.map(p=>p.join(',')).join(' ');
 const ticks=[0,1,2,3].map(i=>{const value=low+(high-low)*(3-i)/3,cy=y(value);return `<line x1="75" x2="745" y1="${cy}" y2="${cy}" stroke="#e8efeb" stroke-dasharray="3 4"/><text x="68" y="${cy+4}" text-anchor="end">${escapeHTML(inr(Math.round(value)))}</text>`;}).join('');
 const labels=rows.filter((r,i)=>i%Math.max(1,Math.ceil(rows.length/7))===0).map(r=>`<text x="${x(rows.indexOf(r))}" y="198" text-anchor="middle">${r.date.slice(8)}/${r.date.slice(5,7)}</text>`).join('');
 const points=rows.map((r,i)=>{const label=`${fmtDate(r.date)}: ${inr(r.settlement)} settlement, ${number(r.orders)} orders`;return `<g class="chart-point" tabindex="0" role="button" aria-label="${escapeHTML(label)}" data-chart-index="${i}"><title>${escapeHTML(label)}</title><rect x="${x(i)-Math.min(14,327/Math.max(rows.length-1,1))}" y="15" width="${Math.min(28,655/Math.max(rows.length-1,1))}" height="160" fill="transparent"/><circle cx="${x(i)}" cy="${y(r.settlement)}" r="3" fill="#168c77"/></g>`;}).join('');
 return `<svg class="chart" viewBox="0 0 780 215" role="group" aria-label="Settlement by order date. Hover or focus a day for values.">${ticks}<polyline points="${line}" fill="none" stroke="#168c77" stroke-width="2.5"/>${labels}${points}</svg><div id="chart-detail" class="chart-detail" role="status">Hover, tap, or focus a day to see its settlement and order count.</div><div class="legend"><span><i></i>Settlement received, grouped by order date</span></div>`;
}
function showChartValue(event){
 const point=event.target.closest('[data-chart-index]');if(!point)return;
 const r=data.timeline[Number(point.dataset.chartIndex)];
 if(r && $('#chart-detail'))$('#chart-detail').textContent=`${fmtDate(r.date)} · Settlement: ${inr(r.settlement)} · Orders: ${number(r.orders)} · Listed value: ${inr(r.listed_value)}`;
}
for(const event of ['pointerover','focusin','click'])document.addEventListener(event,showChartValue);
function renderDashboard(){
 const s=data.summary;
 const profitLabel=s.profit_before_ads?'SKU profit before ads':'Net profit';
 const adsNotice=s.profit_before_ads?`<div class="notice info">${s.net_profit>0?'Profitable before ads':s.net_profit<0?'Loss before ads':'Break-even before ads'}${s.provisional?' · provisional':''}. Period advertising of ${inr(s.unallocated_ads)} has no SKU mapping, so it is not deducted from the selected SKUs. Settlement, costs, counts, and charts below are filtered to the selected SKUs.</div>`:'';
 if(!data.orders.length){$('#content').innerHTML=context()+`<div class="panel empty"><h2>${data.coverage.last_order?'No orders in this period.':'Start with your order and payment files.'}</h2><p>${data.coverage.last_order?'Change the order dates to view your records.':'Import the orders CSV, payment ZIPs, and your product cost workbook.'}</p><button class="primary" data-nav="imports">Import files</button></div>`;return;}
 const incomplete=s.missing_cost||s.missing_packaging;
 const alerts=incomplete?`<div class="notice cost-warning" role="alert">Provisional profit: ${number(s.missing_cost)} orders need product costs and ${number(s.missing_packaging)} need packaging costs. Profit is calculated using known costs only; missing amounts are not deducted. <a href="#products" data-nav="products">Set your costs →</a></div>`:'';
 const fin=costRow('Final settlement',s.settlement,'recorded')+(s.profit_before_ads?'':costRow('Advertising · order spend period',-s.ads,'ads'))+costRow('Product cost'+(s.missing_cost?' · known costs only':''),-s.product_cost,'cost')+costRow('Packaging'+(s.missing_packaging?' · known costs only':''),-s.packaging,'packaging')+costRow(profitLabel,s.net_profit,'all',true);
 const states=Object.entries(s.statuses).map(([key,value])=>`<div><strong>${number(value)}</strong><small>${escapeHTML(key==='RETURN'?'Customer returns':key==='RTO'?'RTO':key[0]+key.slice(1).toLowerCase())}</small></div>`).join('');
 const top=data.sku_performance.filter(r=>!r.incomplete).slice(0,5);const topmax=Math.max(...top.map(r=>Math.abs(r.contribution)),1);
 const bars=top.length?top.map(r=>`<div class="bar-item"><div class="bar-label"><span title="${escapeHTML(r.sku)}">${escapeHTML(r.sku)}</span><b>${inr(r.contribution)}</b></div><div class="bar-track"><div class="bar-fill" style="width:${Math.abs(r.contribution)/topmax*100}%"></div></div></div>`).join(''):'<p class="empty">Set product and packaging costs to compare SKU contributions.</p>';
 $('#content').innerHTML=context()+alerts+adsNotice+`<div class="metric-grid">${metric(profitLabel,s.net_profit===null?'Incomplete':inr(s.net_profit),s.provisional?'Provisional · review outstanding items':'Based on imported records','all',true)}${metric('Settlement received',inr(s.settlement),`${number(s.recorded)} orders with payment records`,'recorded')}${metric('Product & packaging',inr(s.product_cost+s.packaging),incomplete?'Known costs · some inputs missing':'For non-cancelled orders','cost')}${metric('Awaiting payment',number(s.awaiting),`${number(s.overdue)} aged 21+ days · amount unknown`,'awaiting')}</div>`+
 `<div class="two-col">${panel('Settlement over time','See when the orders behind your earnings were placed.',trend())}${panel('Your earnings, explained',s.profit_before_ads?'Settlement − product − packaging':'Settlement − advertising − product − packaging',fin,`<span class="badge ${s.provisional?'warn':''}">${s.provisional?'Provisional':'Imported records'}</span>`)}</div>`+
 `<div class="two-col">${panel('Orders overview',`${number(s.orders)} non-cancelled sub-orders · ${number(s.cancelled)} cancelled excluded`,`<div class="stat-list">${states}</div><div class="cost-row" style="margin-top:20px"><span>Customer return rate</span><b>${s.customer_return_rate}%</b></div><div class="cost-row"><span>RTO rate</span><b>${s.rto_rate}%</b></div><p class="details">Each rate uses unique non-cancelled sub-orders in this period.</p>`)}${panel('Top products by contribution','Before advertising. Only SKUs with complete costs are ranked.',bars)}</div>`+
 `<div class="two-col">${panel('Settlement deductions','Already reflected in settlement; not subtracted again.',costRow('Outbound shipping',s.shipping,'shipping')+costRow('Customer return shipping',s.reverse_shipping,'reverse_shipping')+costRow('TDS · as recorded',s.tds,'tds')+costRow('TCS · as recorded',s.tcs,'tcs'))}${panel('Product GST treatment','Imported purchase prices already include GST.',`<div class="gst-controls"><label>GST rate <input aria-label="GST rate" id="gst-rate" type="number" min="0" max="100" step=".01" value="${escapeHTML(data.settings.gst_rate)}"> %</label><label><input type="checkbox" id="include-gst" ${data.settings.include_gst==='true'?'checked':''}> Include GST in product cost</label><button id="save-gst">Apply</button></div><p class="details">Checked uses imported cost. Unchecked divides it by 1 + GST rate. This report preference applies to all dates without overwriting imported prices.</p><div class="cost-row"><span>Recoveries awaiting confirmation</span><a href="#reconciliation" data-nav="reconciliation">${number(s.recovery_pending)} orders →</a></div>`)}</div>`+
 `<p class="details">Payment dates limit financial transactions. Order statuses reflect the latest imported evidence and may include later outcomes. Payment file coverage does not prove that every payment has been imported.</p>`;
}
function filteredOrders(){return data.orders.filter(o=>{
 if(currentPage==='awaiting' && (o.payment_count||o.status==='CANCELLED'))return false;
 if(orderFilter==='recorded' && (!o.payment_count||o.status==='CANCELLED'))return false;
 if(['shipping','reverse_shipping','tds','tcs'].includes(orderFilter) && (!o[orderFilter]||o.status==='CANCELLED'))return false;
 if(search && !(o.sku+' '+o.sub_order_id).toLowerCase().includes(search.toLowerCase()))return false;
 if(statusFilter && o.status!==statusFilter)return false;
 if(paymentFilter && o.payment_state!==paymentFilter)return false;
 return true;
});}
function renderOrders(){
 const awaiting=currentPage==='awaiting',s=data.summary;
 const intro=awaiting?`<div class="mini-grid"><div><span>Awaiting payment record</span><strong>${number(s.awaiting)}</strong></div><div><span>Aged 21+ days</span><strong>${number(s.overdue)}</strong></div><div><span>Within 21 days</span><strong>${number(s.within_window)}</strong></div><div><span>Pending amount</span><strong>Unknown</strong></div></div><div class="notice info">These files do not provide an expected settlement for unpaid orders. Listed order value is shown for reference and is not a pending payout estimate. A recorded ₹0 settlement is not classified as missing.</div>`:'';
 $('#content').innerHTML=context()+intro+`<section class="panel"><div class="panel-head"><div><h2>${awaiting?'Orders awaiting payment record':'Order details'}</h2><p>Click a sub-order ID to inspect payment history and update recovery.</p></div><a class="nowrap" href="/api/export?${query()}${awaiting?'&awaiting=true':''}">↓ Export period CSV</a></div><div class="toolbar"><input type="search" id="order-search" aria-label="Search orders" placeholder="Search SKU or sub-order ID" value="${escapeHTML(search)}"><select id="order-status" aria-label="Order status"><option value="">All order statuses</option>${[...new Set(data.orders.map(o=>o.status))].sort().map(v=>`<option ${statusFilter===v?'selected':''}>${escapeHTML(v)}</option>`).join('')}</select><select id="payment-status" aria-label="Payment status"><option value="">All payment statuses</option>${[...new Set(data.orders.map(o=>o.payment_state))].sort().map(v=>`<option ${paymentFilter===v?'selected':''}>${escapeHTML(v)}</option>`).join('')}</select>${orderFilter?`<button id="clear-order-filter">Clear ${escapeHTML(orderFilter)} filter</button>`:''}</div><div id="orders-table"></div></section>`;
 renderOrderTable();
}
function renderOrderTable(){
 const rows=filteredOrders(),per=40;tablePage=Math.min(tablePage,Math.max(0,Math.ceil(rows.length/per)-1));const visible=rows.slice(tablePage*per,(tablePage+1)*per);
 $('#orders-table').innerHTML=`<div class="table-wrap"><table><thead><tr><th>SKU / Sub-order</th><th>Order date</th><th>Qty</th><th>Status</th><th>Payment status</th><th class="num">Listed value</th><th class="num">Settlement</th><th class="num">Product cost</th><th class="num">Packaging</th><th class="num">Contribution*</th></tr></thead><tbody>${visible.map(o=>`<tr><td class="sku">${escapeHTML(o.sku)}<br><button class="id-button" data-order="${escapeHTML(o.sub_order_id)}">${escapeHTML(o.sub_order_id)}</button></td><td class="nowrap">${fmtDate(o.order_date)}<div class="details">Due ${fmtDate(o.due_date)}</div></td><td>${o.quantity}</td><td>${badge(o.status)}</td><td>${badge(o.payment_state)}<div class="details">${o.payment_date?fmtDate(o.payment_date):'No payment record'}</div></td><td class="num">${inr(o.listed_value)}</td><td class="num">${inr(o.settlement)}</td><td class="num">${o.product_cost===null?'<strong class="missing-cost">Missing cost</strong>':inr(o.product_cost)}</td><td class="num">${o.packaging===null?'<strong class="missing-cost">Missing cost</strong>':inr(o.packaging)}</td><td class="num ${o.contribution<0?'negative':'positive'}">${inr(o.contribution)}${o.costs_missing?'<div class="missing-cost">Provisional</div>':''}</td></tr>`).join('')||'<tr><td colspan="10" class="empty">No orders match these filters.</td></tr>'}</tbody></table></div><div class="pager"><span>${number(rows.length)} orders · *Before advertising. Missing costs are excluded; affected contributions are provisional.</span><div><button data-order-page="-1" ${tablePage===0?'disabled':''}>←</button> ${tablePage+1} / ${Math.max(1,Math.ceil(rows.length/per))} <button data-order-page="1" ${(tablePage+1)*per>=rows.length?'disabled':''}>→</button></div></div>`;
}
async function renderProducts(){
 const chosen=$('#product-date')?.value || localToday();
 productRows=await api('/api/products?date='+chosen);
 $('#content').innerHTML=`<section class="panel"><div class="panel-head"><div><h2>Product cost book</h2><button class="primary" id="add-product">+ Add SKU manually</button><p>${number(productRows.length)} SKUs · Purchase prices include GST. Packaging is charged once per sub-order.</p></div></div><div class="toolbar"><input type="search" id="product-search" aria-label="Search products" placeholder="Find a SKU…" value="${escapeHTML(productSearch)}"><select id="product-completeness" aria-label="Cost completeness"><option value="">All products</option><option value="cost" ${productCompleteness==='cost'?'selected':''}>Missing product cost</option><option value="packaging" ${productCompleteness==='packaging'?'selected':''}>Missing packaging</option></select><select id="product-status-filter" aria-label="Product status filter"><option value="active" ${productStatus==='active'?'selected':''}>Active SKUs</option><option value="disabled" ${productStatus==='disabled'?'selected':''}>Disabled SKUs</option><option value="all" ${productStatus==='all'?'selected':''}>All SKUs</option></select><label class="form-label">View product prices on<input type="date" id="product-date" value="${chosen}"></label></div><div class="bulk-box"><div class="form-row"><label class="form-label">Packaging per sub-order (₹)<input type="number" id="bulk-packaging" placeholder="e.g. 5.00" min="0" step=".01"></label><button id="preview-bulk">Preview bulk update</button><p>Applies to all ${number(productRows.filter(r=>!r.disabled).length)} active SKUs, including those outside the search. Disabled SKUs are not changed.<br>Packaging updates apply to all previous and future orders for these SKUs.</p></div></div><div id="product-table"></div></section><div class="notice info">Disabled SKUs remain in historical reports. Product price versions apply by order date. Packaging always uses the latest saved amount for every order date. Set the Apply from date beside a SKU before saving a new price. Orders on or after that date use the new cost until the next price change; earlier orders keep the previous cost. The first saved price also fills earlier orders that had no price, including older orders imported later. History shows all saved prices. Blank costs remain unknown; enter 0 explicitly when no cost applies.</div>`;
 renderProductTable();
}
function renderProductTable(){
 const rows=productRows.filter(r=>(productStatus==='all'||(productStatus==='disabled'?r.disabled:!r.disabled))&&r.display_sku.toLowerCase().includes(productSearch.toLowerCase())&&(!productCompleteness||r[productCompleteness]===null));const per=35;productPage=Math.min(productPage,Math.max(0,Math.ceil(rows.length/per)-1));
 $('#product-table').innerHTML=`<div class="table-wrap"><table><thead><tr><th>SKU</th><th class="num">Cost incl. GST (₹)</th><th class="num">Packaging (₹)</th><th>Product price applies from</th><th></th></tr></thead><tbody>${rows.slice(productPage*per,(productPage+1)*per).map(r=>`<tr><td class="sku">${escapeHTML(r.display_sku)}${r.disabled?'<div class="badge neutral">Disabled</div>':''}</td><td class="num"><input class="product-input" type="number" min="0" step=".01" aria-label="Cost for ${escapeHTML(r.display_sku)}" data-cost value="${r.cost===null?'':(r.cost/100).toFixed(2)}" placeholder="Not set">${r.cost===null?'<div class="missing-cost">Missing product cost</div>':''}</td><td class="num"><input class="product-input" type="number" min="0" step=".01" aria-label="Packaging for ${escapeHTML(r.display_sku)}" data-pack value="${r.packaging===null?'':(r.packaging/100).toFixed(2)}" placeholder="Not set"></td><td><input type="date" data-apply-date aria-label="Apply cost from for ${escapeHTML(r.display_sku)}" value="${escapeHTML($('#product-date').value)}"><div class="details">Current version: ${fmtDate(r.effective_date)}</div>${r.initial_cost_applied?'<div class="details">First saved price applied</div>':''}${r.cost_source_sku!==r.sku?'<div class="details">Matched export separator difference</div>':''}</td><td class="nowrap"><button data-save-product="${escapeHTML(r.sku)}">Save</button> <button data-product-history="${escapeHTML(r.sku)}">History</button> <button data-toggle-product="${escapeHTML(r.sku)}" data-disabled="${r.disabled?'false':'true'}">${r.disabled?'Enable':'Disable'}</button></td></tr>`).join('')||'<tr><td colspan="5" class="empty">No products. Add a SKU manually or import a product cost workbook.</td></tr>'}</tbody></table></div><div class="pager"><span>${number(rows.length)} SKUs</span><div><button data-product-page="-1" ${productPage===0?'disabled':''}>←</button> ${productPage+1} / ${Math.max(1,Math.ceil(rows.length/per))} <button data-product-page="1" ${(productPage+1)*per>=rows.length?'disabled':''}>→</button></div></div>`;
}
function renderReconciliation(){
 const count=Object.entries(data.issues.reduce((a,r)=>(a[r.issue]=(a[r.issue]||0)+1,a),{}));
 $('#content').innerHTML=context()+`<div class="notice info">Status differences are preserved for review. Return evidence in payments takes precedence over “Delivered” in the orders file. Cancelled orders remain excluded. Unmatched payments below cover all imported orders, not just the selected order period.</div>`+
 panel('Items to review','Review the source record before changing a cost or recovery outcome.',count.map(([k,v])=>`<div class="cost-row"><span>${escapeHTML(k)}</span><b>${number(v)}</b></div>`).join('')||'<p>No order issues in the selected period.</p>')+
 `<section class="panel"><div class="panel-head"><div><h2>Order reconciliation</h2><p>${number(data.issues.length)} findings · showing the first 200; use Orders to search all records.</p></div></div><div class="table-wrap"><table><thead><tr><th>Sub-order ID</th><th>SKU</th><th>Finding</th></tr></thead><tbody>${data.issues.slice(0,200).map(i=>`<tr><td><button class="id-button" data-order="${escapeHTML(i.sub_order_id)}">${escapeHTML(i.sub_order_id)}</button></td><td class="sku">${escapeHTML(i.sku)}</td><td>${escapeHTML(i.issue)}</td></tr>`).join('')||'<tr><td colspan="3" class="empty">No findings.</td></tr>'}</tbody></table></div></section>`+
 panel('Advertising expenses','Attributed to the spend period. Multi-day periods are allocated evenly by day.',`<div class="table-wrap"><table><thead><tr><th>Spend period</th><th>Deducted</th><th>Campaign</th><th class="num">Period cost</th></tr></thead><tbody>${data.ads.map(a=>`<tr><td>${fmtDate(a.spend_start)}${a.spend_end!==a.spend_start?' – '+fmtDate(a.spend_end):''}</td><td>${fmtDate(a.deduction_date)}</td><td>${escapeHTML(a.campaign)}</td><td class="num">${inr(a.allocated_cost)}</td></tr>`).join('')||'<tr><td colspan="4">No ads records for the selected spend period.</td></tr>'}</tbody></table></div>`)+
 panel('Payments without an imported order',`${number(data.unmatched_payments.length)} transactions · excluded from order profit. Showing first 100.`,`<div class="table-wrap"><table><thead><tr><th>Sub-order ID</th><th>Order date</th><th>Payment date</th><th class="num">Settlement</th></tr></thead><tbody>${data.unmatched_payments.slice(0,100).map(r=>`<tr><td><button class="id-button" data-order="${escapeHTML(r.sub_order_id)}">${escapeHTML(r.sub_order_id)}</button></td><td>${fmtDate(r.order_date)}</td><td>${fmtDate(r.payment_date)}</td><td class="num">${inr(r.settlement)}</td></tr>`).join('')||'<tr><td colspan="4">All payments match imported orders.</td></tr>'}</tbody></table></div>`)+
 panel('Account-level payments','Referral and other account adjustments are stored separately and excluded from the agreed order-profit formula.',data.other_payments.map(r=>`<div class="cost-row"><span>${escapeHTML(r.kind)} · ${fmtDate(r.payment_date)}</span><b>${inr(r.amount)}</b></div>`).join('')||'<p class="subtle">No account-level payments in the selected payment period.</p>');
}
async function renderImports(){
 const imports=await api('/api/imports');
 $('#content').innerHTML=lastImportSummary+panel('Upload files','Select one or more files. Your originals will be stored with the import history.',`<div class="upload-grid">${[['orders','Order data','Select CSV files for multiple months together. Previous imports are retained.','.csv'],['payments','Payment files','Meesho payment ZIPs or XLSX files. Multiple months supported.','.zip,.xlsx'],['products','Product costs','One sheet with SKU and GST-inclusive Cost Price.','.xlsx']].map(([id,label,desc,accept])=>`<label class="upload-card"><div class="upload-icon">${id==='orders'?'▤':id==='payments'?'⇄':'▦'}</div><h3>${label}</h3><p>${desc}</p><input type="file" id="upload-${id}" accept="${accept}" ${id!=='products'?'multiple':''} aria-label="${label}"></label>`).join('')}</div><div class="form-row"><label class="form-label">Imported product costs effective from<input type="date" id="import-effective" value="${imports.some(i=>i.kind==='products')?localToday():'0001-01-01'}"></label><p>For an initial import, 01/01/0001 covers all historical orders.<br>For later cost changes, choose the date the new prices apply.</p><button class="primary" id="preview-import">Preview files</button></div><div id="previews"></div>`)+
 `<section class="panel"><div class="panel-head"><div><h2>Import history</h2><p>Re-uploaded records update the existing data. Distinct payment transactions are retained and counted once.</p></div></div><div class="table-wrap"><table><thead><tr><th>File</th><th>Type</th><th>Imported</th><th>Records</th></tr></thead><tbody>${imports.map(i=>`<tr><td>${escapeHTML(i.name)}</td><td>${escapeHTML(i.kind)}</td><td class="nowrap">${escapeHTML(i.imported_at)} UTC</td><td>${Object.entries(JSON.parse(i.summary).counts).map(([k,v])=>number(v)+' '+({order_events:'order rows',payments:'payments',product_versions:'product costs',ads:'ads',other_payments:'adjustments'}[k]||k)).join(' · ')}</td></tr>`).join('')||'<tr><td colspan="4" class="empty">No files imported yet.</td></tr>'}</tbody></table></div></section>`;
 renderPreviews();
}
function renderPreviews(){
 $('#previews').innerHTML=previews.map(p=>`<div class="upload-preview"><b>${escapeHTML(p.name)}</b> ${p.already_imported?badge('Re-upload · refresh existing'):''}<br>${Object.entries(p.counts).map(([k,v])=>`${number(v)} ${escapeHTML(k.replaceAll('_',' '))}`).join(' · ')}<br>${number(p.new_records)+' new records; '+number(p.overlapping_records)+' existing records will be refreshed.'}${p.warnings.map(w=>`<div>${escapeHTML(w)}</div>`).join('')}</div>`).join('')+(previews.length?'<button class="primary" id="commit-import">Save previewed files to database</button>':'');
}
function fileBase64(file){return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file);});}
async function previewUploads(){
 const selected=['orders','payments','products'].flatMap(kind=>[...$('#upload-'+kind).files].map(file=>({kind,file})));
 if(!selected.length)throw new Error('Choose at least one file.');
 const btn=$('#preview-import');btn.disabled=true;btn.textContent='Reading files…';previews=[];
 try{for(const {kind,file} of selected){if(file.size>50*1024*1024)throw new Error('Each file must be 50 MB or smaller.');previews.push(await api('/api/import/preview',{kind,name:file.name,payload:await fileBase64(file),effective_date:$('#import-effective').value}));}renderPreviews();}
 finally{btn.disabled=false;btn.textContent='Preview files';}
}
async function openOrder(id){
 const detail=await api('/api/order?id='+encodeURIComponent(id));const row=data.orders.find(r=>r.sub_order_id===id);
 $('#dialog-title').textContent='Sub-order '+id;
 $('#dialog-content').innerHTML=(row?`<p>${escapeHTML(row.sku)}</p>${row.initial_cost_applied?'<p class="details">Purchase cost uses the first saved price for this previously unpriced order.</p>':''}${row.cost_source_sku!==row.sku.trim().toLowerCase()?`<p class="details">Cost source SKU: ${escapeHTML(row.cost_source_sku)}</p>`:''}<div class="form-row">${badge(row.status)} ${badge(row.payment_state)}</div><div class="mini-grid"><div><span>Settlement</span><strong>${inr(row.settlement)}</strong></div><div><span>Product cost</span><strong>${inr(row.product_cost)}</strong></div><div><span>Packaging</span><strong>${inr(row.packaging)}</strong></div><div><span>Contribution before ads</span><strong>${row.contribution===null?'Incomplete':inr(row.contribution)}</strong></div></div>${row.issues.length?`<div class="notice">${row.issues.map(escapeHTML).join('<br>')}</div>`:''}`:'')+
 `<h3>Payment transactions · all imported dates</h3><p class="subtle">Transactions after the report's payments-through date are shown here but excluded from its totals.</p><div class="table-wrap"><table><thead><tr><th>Date / Transaction</th><th>Status</th><th class="num">Settlement</th><th class="num">Shipping</th><th class="num">Reverse</th><th class="num">TDS</th></tr></thead><tbody>${detail.payments.map(p=>`<tr><td>${fmtDate(p.payment_date)}<div class="details">${escapeHTML(p.transaction_id)}</div>${p.counted===false?'<div class="details">Duplicate source record · excluded from totals</div>':''}</td><td>${badge(p.status||'Not recorded')}</td><td class="num">${inr(p.settlement)}</td><td class="num">${inr(p.shipping)}</td><td class="num">${inr(p.reverse_shipping)}</td><td class="num">${inr(p.tds)}</td></tr>`).join('')||'<tr><td colspan="6">No payment transactions imported.</td></tr>'}</tbody></table></div>`+
 (row&&['RTO','RETURN','EXCHANGE','LOST'].includes(row.status)?`<h3>Product recovery</h3><p class="subtle">RTO orders default to reusable stock with zero product cost. Customer returns need sellable recovery confirmed. Packaging is retained. Explicit damaged, lost, or awaiting outcomes retain purchase cost.</p><div class="form-row"><label class="form-label">Recovery outcome<select id="recovery-state">${[['awaiting','Awaiting recovery'],['sellable','Recovered · sellable'],['damaged','Damaged'],['lost','Lost']].map(([v,label])=>`<option value="${v}" ${row.recovery===v?'selected':''}>${label}</option>`).join('')}</select></label><label class="form-label">Recovery date<input type="date" id="recovery-date" value="${data.filters.as_of}"></label><label class="form-label">Note<input id="recovery-note" value="${escapeHTML(row.recovery_note)}" placeholder="Condition or reference"></label><button class="primary" data-save-recovery="${escapeHTML(id)}">Save outcome</button></div>`:'')+
 `<h3>Source records</h3>${[...detail.order_events,...detail.payments].map(r=>`<details class="details"><summary>${escapeHTML(r.source_file)} · ${escapeHTML(r.status||'Payment')}</summary><pre>${escapeHTML(JSON.stringify(JSON.parse(r.raw),null,2))}</pre></details>`).join('')}`+
 (detail.recovery_history.length?`<h3>Recovery history</h3>${detail.recovery_history.map(r=>`<p class="details">${fmtDate(r.effective_date)} · ${escapeHTML(r.state)} · ${escapeHTML(r.note)}</p>`).join('')}`:'');
 $('#detail-dialog').showModal();
}
document.addEventListener('click',event=>run(async()=>{
 const b=event.target.closest('button,a');if(!b)return;
 if(b.dataset.page||b.dataset.nav){event.preventDefault();await navigate(b.dataset.page||b.dataset.nav);}
 if(b.id==='header-import')await navigate('imports');
 if(b.id==='apply-filters'){tablePage=0;await refresh();}
 if(b.id==='reset-period'){$('#start').value='';tablePage=0;await refresh();}
 if(b.dataset.drill){
  if(b.dataset.drill==='ads'){await navigate('reconciliation');return;}
  await navigate(b.dataset.drill==='awaiting'?'awaiting':'orders');orderFilter=b.dataset.drill;renderOrders();
 }
 if(b.id==='save-gst'){await api('/api/settings',{gst_rate:$('#gst-rate').value,include_gst:$('#include-gst').checked});await refresh();toast('GST treatment updated.');}
 if(b.dataset.order)await openOrder(b.dataset.order);
 if(b.id==='close-dialog')$('#detail-dialog').close();
 if(b.dataset.orderPage){tablePage+=Number(b.dataset.orderPage);renderOrderTable();}
 if(b.dataset.productPage){productPage+=Number(b.dataset.productPage);renderProductTable();}
 if(b.id==='clear-order-filter'){orderFilter='';renderOrders();}
 if(b.dataset.saveProduct){const tr=b.closest('tr');await api('/api/products',{effective_date:tr.querySelector('[data-apply-date]').value,updates:[{sku:b.dataset.saveProduct,...(tr.querySelector('[data-cost]').dataset.changed==='true'?{cost:tr.querySelector('[data-cost]').value}:{}),packaging:tr.querySelector('[data-pack]').value}]});await refresh();toast('Saved. Packaging applies to all order dates; product prices keep their apply-from dates.');}
 if(b.dataset.productHistory){const rows=await api('/api/product-history?sku='+encodeURIComponent(b.dataset.productHistory));$('#dialog-title').textContent='Cost history';$('#dialog-content').innerHTML=`<p class="details">Dates apply to product prices. Packaging shows saved snapshots; all orders use the latest packaging setting.</p><table><thead><tr><th>Effective from</th><th>Cost</th><th>Packaging</th><th>Source</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${fmtDate(r.effective_date)}</td><td>${inr(r.cost)}</td><td>${inr(r.packaging)}</td><td>${escapeHTML(r.source)}</td></tr>`).join('')||'<tr><td colspan="4">No costs saved.</td></tr>'}</tbody></table>`;$('#detail-dialog').showModal();}
 if(b.id==='preview-bulk'){
  const v=$('#bulk-packaging').value;if(v===''||!Number.isFinite(Number(v))||Number(v)<0)throw new Error('Enter a non-negative packaging cost.');
  const d=$('#product-date').value;$('#dialog-title').textContent='Review bulk packaging update';$('#dialog-content').innerHTML=`<p>Set packaging to <b>${inr(Math.round(Number(v)*100))}</b> per sub-order for <b>${number(productRows.filter(r=>!r.disabled).length)} active SKUs</b>, for all previous and future orders.</p><p class="details">This recalculates packaging for every order date. Product purchase prices and their apply-from dates are preserved.</p><div class="form-row"><button class="primary" id="confirm-bulk" data-amount="${escapeHTML(v)}" data-date="${escapeHTML(d)}">Apply to all SKUs</button></div>`;$('#detail-dialog').showModal();
 }
 if(b.id==='confirm-bulk'){await api('/api/products',{effective_date:b.dataset.date,updates:productRows.filter(r=>!r.disabled).map(r=>({sku:r.sku,packaging:b.dataset.amount}))});$('#detail-dialog').close();await refresh();toast('Packaging updated for all SKUs and all order dates.');}
 if(b.id==='preview-import')await previewUploads();
 if(b.id==='commit-import'){b.disabled=true;try{const applied=await api('/api/import/commit',{tokens:previews.map(p=>p.token)});const added=applied.imported.reduce((n,r)=>n+r.added_records,0),updated=applied.imported.reduce((n,r)=>n+r.updated_records,0);lastImportSummary=`<div class="notice info" role="status">Import complete: ${number(added)} records added, ${number(updated)} existing records refreshed. <button data-nav="dashboard">Open Overview</button> <button id="show-all-orders">Show all imported orders</button></div>`;previews=[];$('#as-of').value=localToday();if($('#mode').value==='mature')$('#start').value='';await refresh();toast('Imports applied. Overview follows the selected order dates and 21-day setting.');}finally{b.disabled=false;}}
 if(b.dataset.saveRecovery){await api('/api/recovery',{sub_order_id:b.dataset.saveRecovery,state:$('#recovery-state').value,effective_date:$('#recovery-date').value,note:$('#recovery-note').value});$('#detail-dialog').close();await refresh();toast('Recovery outcome saved.');}
}));
document.addEventListener('input',event=>{
 if(event.target.matches('[data-cost]'))event.target.dataset.changed='true';
 if(event.target.id==='sku-search')renderSkuOptions();
 if(event.target.id==='order-search'){search=event.target.value;tablePage=0;renderOrderTable();}
 if(event.target.id==='product-search'){productSearch=event.target.value;productPage=0;renderProductTable();}
});
document.addEventListener('change',event=>run(async()=>{
 if(event.target.id==='mode'){$('#end').disabled=event.target.value==='mature';$('#start').value='';await refresh();}
 if(event.target.id==='as-of'){$('#start').value='';if($('#mode').value==='custom' && $('#end').value>event.target.value)$('#end').value=event.target.value;await refresh();}
 if(event.target.id==='end' && event.target.value){const from=new Date(event.target.value+'T12:00:00');from.setDate(from.getDate()-29);$('#start').value=from.getFullYear()+'-'+String(from.getMonth()+1).padStart(2,'0')+'-'+String(from.getDate()).padStart(2,'0');}
 if(event.target.id==='order-status'){statusFilter=event.target.value;tablePage=0;renderOrderTable();}
 if(event.target.id==='payment-status'){paymentFilter=event.target.value;tablePage=0;renderOrderTable();}
 if(event.target.id==='product-date')await renderProducts();
 if(event.target.id==='product-status-filter'){productStatus=event.target.value;productPage=0;renderProductTable();}
 if(event.target.id==='product-completeness'){productCompleteness=event.target.value;productPage=0;renderProductTable();}
}));
$('#as-of').value=localToday();$('#mode').value='mature';
if(pageNames[location.hash.slice(1)])currentPage=location.hash.slice(1);
run(refresh);


document.addEventListener('click',e=>{if(e.target.closest('#clear-sku')){selectedSkus=[];draftSkus.clear();run(refresh);}});

function renderSkuPicker(){
 $('#sku-summary').textContent=selectedSkus.length?`${selectedSkus.length} SKU${selectedSkus.length===1?'':'s'} selected`:'All SKUs';
 renderSkuOptions();
}
function renderSkuOptions(){
 const term=$('#sku-search').value.toLowerCase();
 const options=[...data.sku_options,...selectedSkus.filter(k=>!data.sku_options.some(o=>o.value===k)).map(k=>({value:k,label:k+' (no orders in period)'}))];
 $('#sku-options').innerHTML=options.filter(o=>o.label.toLowerCase().includes(term)).map(o=>`<label class="sku-choice"><input type="checkbox" data-sku-choice="${escapeHTML(o.value)}" ${draftSkus.has(o.value)?'checked':''}><span>${escapeHTML(o.label)}</span></label>`).join('')||'<p>No matching SKUs.</p>';
}
function renderPerformance(){
 const rows=data.sku_performance;
 const totals=data.summary;
 $('#content').innerHTML=context()+`<div class="notice info">Profit before ads = settlement − product cost − packaging. Advertising is not assigned to SKUs. Missing costs, pending payments, and unconfirmed recoveries make results provisional.</div><div class="mini-grid"><div><span>SKUs in period</span><strong>${number(rows.length)}</strong></div><div><span>Positive before ads</span><strong>${number(rows.filter(r=>r.contribution>0).length)}</strong></div><div><span>Negative before ads</span><strong>${number(rows.filter(r=>r.contribution<0).length)}</strong></div><div><span>Total profit before ads</span><strong>${inr(totals.settlement-totals.product_cost-totals.packaging)}</strong></div></div><section class="panel"><div class="panel-head"><div><h2>SKU profitability</h2><p>Sorted by profit before ads. Use the searchable SKU filter above to compare selected products.</p></div></div><div class="table-wrap"><table><thead><tr><th>SKU</th><th>Orders</th><th>Units</th><th>Settlement</th><th>Product cost</th><th>Packaging</th><th>Profit before ads</th><th>Result</th><th>Returns</th><th>RTO</th><th>Awaiting payment</th></tr></thead><tbody>${rows.map(r=>`<tr><td class="sku"><button data-performance-sku="${escapeHTML(r.sku_key)}">${escapeHTML(r.sku)}</button></td><td>${number(r.orders)}</td><td>${number(r.quantity)}</td><td>${inr(r.settlement)}</td><td>${inr(r.product_cost)}</td><td>${inr(r.packaging)}</td><td class="${r.contribution<0?'negative':'positive'}">${inr(r.contribution)}</td><td>${r.contribution>0?'Profit':r.contribution<0?'Loss':'Break-even'}${r.provisional?'<div class="missing-cost">Provisional</div>':''}</td><td>${number(r.returns)}</td><td>${number(r.rto)}</td><td>${number(r.awaiting)}</td></tr>`).join('')||'<tr><td colspan="11" class="empty">No orders match these dates and SKUs.</td></tr>'}</tbody></table></div></section>`;
}
document.addEventListener('change',e=>{
 if(e.target.matches('[data-sku-choice]')){const key=e.target.dataset.skuChoice;e.target.checked?draftSkus.add(key):draftSkus.delete(key);}
 if(e.target.matches('[data-apply-date]'))e.target.closest('tr').querySelector('[data-cost]').dataset.changed='true';
});
document.addEventListener('click',e=>run(async()=>{
 const b=e.target.closest('button');if(!b)return;
 if(b.id==='apply-skus'){selectedSkus=[...draftSkus];$('#sku-picker').open=false;await refresh();}
 if(b.id==='reset-skus'){selectedSkus=[];draftSkus.clear();$('#sku-search').value='';$('#sku-picker').open=false;await refresh();}
 if(b.dataset.performanceSku){selectedSkus=[b.dataset.performanceSku];currentPage='dashboard';history.replaceState(null,'','#dashboard');await refresh();}
 if(b.id==='add-product'){
  $('#dialog-title').textContent='Add SKU manually';
  $('#dialog-content').innerHTML=`<p>Use the same SKU name as your order files. Purchase price includes GST. The first price also fills older unpriced orders; later price changes follow their apply-from dates.</p><div class="form-row"><label class="form-label">SKU name<input id="new-sku" maxlength="1000"></label><label class="form-label">Cost incl. GST (₹)<input id="new-cost" type="number" min="0" step=".01"></label><label class="form-label">Packaging per sub-order (₹)<input id="new-packaging" type="number" min="0" step=".01"></label><label class="form-label">Product price applies from<input id="new-cost-date" type="date" value="${localToday()}"></label></div><p id="new-product-error" class="missing-cost" role="alert"></p><button class="primary" id="create-product">Save new SKU</button>`;
  $('#detail-dialog').showModal();
 }
 if(b.id==='create-product'){
  try{await api('/api/products/create',{sku:$('#new-sku').value,cost:$('#new-cost').value,packaging:$('#new-packaging').value,effective_date:$('#new-cost-date').value});}catch(err){$('#new-product-error').textContent=err.message;return;}
  $('#detail-dialog').close();await refresh();toast('SKU saved. It will match orders imported with the same SKU name.');
 }
}));

document.addEventListener('click',e=>run(async()=>{
 const b=e.target.closest('button');if(!b)return;
 if(b.dataset.toggleProduct){await api('/api/products/status',{sku:b.dataset.toggleProduct,disabled:b.dataset.disabled==='true'});await renderProducts();toast('Product status saved. Historical reports are unchanged.');}
 if(b.id==='refresh-report')await refresh();
 if(b.id==='show-all-orders'){
  $('#mode').value='custom';$('#start').value=data.coverage.first_order||'';$('#end').value=data.coverage.last_order||localToday();$('#as-of').value=localToday();
  currentPage='dashboard';history.replaceState(null,'','#dashboard');await refresh();
 }
}));
