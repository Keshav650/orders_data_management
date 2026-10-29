const fs=require('fs'),vm=require('vm'),assert=require('assert');
const elements=new Map();const document={querySelector(s){if(!elements.has(s))elements.set(s,{value:'',hidden:false,innerHTML:'',textContent:'',classList:{toggle(){}}});return elements.get(s);},querySelectorAll(){return []}};
const context=vm.createContext({document,URLSearchParams,Intl,console});
vm.runInContext(fs.readFileSync('flipkart/static/app.js','utf8').replace(/initialize\(\);\s*$/,''),context);
vm.runInContext(`data={as_of:'2026-10-11',orders:[
{item_id:'a1',order_id:'A',ordered_on:'2026-10-01',dispatched_on:'2026-10-02',return_category:'RTO',returned:true,status:'RETURNED',payment_count:1,net:20000000,excluded:false},
{item_id:'a2',order_id:'A',ordered_on:'2026-10-01',dispatched_on:'2026-10-02',return_category:'RTO',returned:true,status:'RETURNED',payment_count:1,net:10000000,excluded:false},
{item_id:'b1',order_id:'B',ordered_on:'2026-10-01',dispatched_on:'',return_category:'',returned:false,status:'CANCELLED',payment_count:0,net:0,excluded:true},
{item_id:'c1',order_id:'C',ordered_on:'2026-10-02',dispatched_on:'2026-10-12',return_category:'',returned:false,status:'APPROVED',payment_count:0,net:0,excluded:false}
],settlements:[{item_id:'a1',refund:-50000000},{item_id:'a2',refund:0}]};dailyPage();`,context);
const html=elements.get('#content').innerHTML;
assert(html.includes('data-daily-day="2026-10-01" data-daily-metric="all">2</button>'));
assert(html.includes('data-daily-day="2026-10-01" data-daily-metric="rto">1</button>'));
assert(html.includes('data-daily-day="2026-10-01" data-daily-metric="paid">₹30.00</button>'));
assert(html.includes('data-daily-day="2026-10-01" data-daily-metric="returned">-₹50.00</button>'));
assert.strictEqual(vm.runInContext("dailyMatch(data.orders[2],'pending')",context),false);
assert.strictEqual(vm.runInContext("dailyMatch(data.orders[3],'pending')",context),true);
assert.strictEqual(vm.runInContext("dailyMatch(data.orders[3],'picked')",context),false);
assert.strictEqual(vm.runInContext("dailyMatch(data.orders[0],'rvp')",context),false);
assert.strictEqual(vm.runInContext("dailyMatch(data.orders[2],'awaiting')",context),false);
assert.strictEqual(vm.runInContext("dailyMatch(data.orders[3],'awaiting')",context),true);
assert.strictEqual(vm.runInContext("dailyMatch(data.orders[0],'awaiting')",context),false);
console.log('Daily report checks passed: unique orders, signed settlements/refunds, pickup cutoff and pending exclusions.');
