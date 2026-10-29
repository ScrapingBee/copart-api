// Copart search results from Node 18+ (global fetch), one JSON line per lot.
//
//   SCRAPINGBEE_API_KEY=... node copart_lots.js "ford f150" > f150.jsonl
//
// Renders https://www.copart.com/lotSearchResults with ScrapingBee's stealth
// proxy (75 credits), switches the table to 100 rows and reads it with
// extract_rules. Same page and selectors as copart_client.py.

const API = "https://app.scrapingbee.com/api/v1/";
const key = process.env.SCRAPINGBEE_API_KEY;
const query = process.argv[2];
if (!key || !query) {
  console.error('usage: SCRAPINGBEE_API_KEY=... node copart_lots.js "toyota camry"');
  process.exit(1);
}

// Same in-page steps as SEARCH_JS in copart_client.py, fixed at 100 rows, page 1.
const scenario = `(async()=>{
const sleep=t=>new Promise(r=>setTimeout(r,t));
const until=async(f,ms)=>{const t0=Date.now();while(Date.now()-t0<ms){if(f())return true;await sleep(200);}return false;};
const cur=()=>(document.querySelector('.p-paginator-current')||{}).textContent||'';
const PER=100, PAGE=0;
const rpp=document.querySelector('.p-paginator-rpp-options');
if(PER!==20&&rpp){
  rpp.click();await sleep(500);
  const o=[...document.querySelectorAll('li[role=option]')].find(li=>li.textContent.trim()===String(PER));
  if(o){o.click();}
  await until(()=>cur().includes(' to '+PER+' of'),10000);
}
for(let i=1;i<=PAGE;i++){
  const next=document.querySelector('button.p-paginator-next');
  if(!next||next.disabled)break;
  next.click();
  await until(()=>cur().startsWith('Showing '+(i*PER+1)+' '),10000);
}
const rows=()=>document.querySelectorAll('tr[data-lotnumber]');
const filled=()=>document.querySelectorAll('tr[data-lotnumber] .search_result_amount_block').length;
let last=-1,stable=0;
for(let i=0;i<110;i++){
  window.scrollBy(0,600);await sleep(250);
  const n=rows().length, bottom=window.innerHeight+window.scrollY>=document.body.scrollHeight-5;
  if(n>=PER&&filled()>=n)break;
  stable=(n===last)?stable+1:0;last=n;
  if(bottom&&stable>=6)break;
}
})()`;

const rules = {
  showing: ".p-paginator-current",
  rows: {
    selector: "tr[data-lotnumber]",
    type: "list",
    output: {
      lot: { selector: "tr", output: "@data-lotnumber" },
      title: ".search_result_lot_detail",
      vehicle: ".search_result_veh_info_block",
      title_label: ".search_result_condition_block span[title]",
      yard: ".search_result_yard_location_label",
      bid: ".search_result_amount_block .currencyAmount",
      currency: ".search_result_amount_block .currencyCode",
    },
  },
};

const clean = (s) => (s || "").replace(/\s+/g, " ").trim();

async function call(params, tries = 3) {
  const url = API + "?" + new URLSearchParams(params);
  for (let i = 1; i <= tries; i++) {
    const res = await fetch(url, { headers: { Authorization: `Bearer ${key}` } });
    const cost = Number(res.headers.get("spb-cost") || 0);
    if (res.ok) return { data: await res.json(), cost };
    if (res.status !== 500 || i === tries) throw new Error(`ScrapingBee ${res.status}: ${(await res.text()).slice(0, 200)}`);
    await new Promise((r) => setTimeout(r, 2000 * i)); // 500s are not billed, so retrying is free
  }
}

async function main() {
const { data, cost } = await call({
  url: "https://www.copart.com/lotSearchResults?free=true&query=" + encodeURIComponent(query),
  render_js: "true",
  stealth_proxy: "true",
  country_code: "us",
  js_scenario: JSON.stringify({ instructions: [{ wait_for: "tr[data-lotnumber]" }, { evaluate: scenario }], strict: false }),
  extract_rules: JSON.stringify(rules),
});

for (const r of data.rows || []) {
  const odo = clean(r.vehicle).match(/Odometer\s+([\d,]+)/);
  const bid = clean(r.bid).replace(/[$,]/g, "");
  console.log(
    JSON.stringify({
      lot: Number(r.lot),
      title: clean(r.title),
      odometer: odo ? Number(odo[1].replace(/,/g, "")) : null,
      title_group: clean(r.title_label).split("(")[0].trim(),
      yard: clean(r.yard),
      current_bid: bid ? Number(bid) : null,
      currency: clean(r.currency) || null,
    }),
  );
}
console.error(`${clean(data.showing)}, ${(data.rows || []).length} rows, ${cost} credits`);
}

main().catch((e) => {
  console.error(e.message);
  process.exit(1);
});
