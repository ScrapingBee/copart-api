# Copart API

<p align="center">
  <a href="https://www.scrapingbee.com/">
    <img src="REPLACE_WITH_SCREENSHOT_URL" alt="copart-api" />
  </a>
</p>

[![checks](https://github.com/ScrapingBee/copart-api/workflows/checks/badge.svg)](https://github.com/ScrapingBee/copart-api/actions)
[![license](https://img.shields.io/github/license/ScrapingBee/copart-api.svg)](LICENSE)

Python and Node code for pulling salvage auction data out of copart.com the way a logged out visitor sees it: the search results table, one lot page at a time, and a watcher that logs bid changes to CSV. It is a working [Copart API](https://www.scrapingbee.com/scrapers/copart-api/) client on top of [ScrapingBee](https://www.scrapingbee.com/), and it reads only two public pages, both allowed by Copart's robots.txt:

```
Allow: /lotSearchResults$        search results (query string after it)
       /lot/<number>/<slug>      lot pages, not listed under any Disallow
Disallow: /public/data/          not used by anything in this repo
```

There is no private endpoint, no replayed request and no cookie handling here. A real browser opens the page, does what a visitor would do (picks 100 rows per page, clicks Next, scrolls), and the HTML it ends up with is parsed with CSS selectors. All numbers below were measured against the US site on 2026-10-29.

```bash
pip install requests
export SCRAPINGBEE_API_KEY=your_key
python examples/copart_client.py search "ford f150" --csv f150.csv
```

```
  page 1: Showing 1 to 100 of 1000 entries, 100 rows
65012146  2023 FORD F150 SUPERCREW     Clean Title    Normal Wear                  0 USD  NC - CONCORD
67825876  2019 FORD F150 SUPER CAB     Clean Title    Normal Wear                  0 CAD  QC - MONTREAL
69190496  2016 FORD F150 SUPERCREW     Clean Title    Minor Dent/Scratches     5,300 USD  TX - CORPUS CHRISTI
69504416  2009 FORD F150 SUPER CAB     Clean Title    Minor Dent/Scratches     1,750 USD  PA - HARRISBURG
70507996  2019 FORD F150 SUPERCREW     Clean Title    Minor Dent/Scratches    10,500 USD  GA - ATLANTA SOUTH
100 lots, 75 credits
```

## Files

| File | What it does | Credits per call |
|---|---|---|
| [`examples/copart_client.py`](examples/copart_client.py) | `CopartClient`, two dataclasses, `search` and `lot` CLI with CSV/JSON export | 75 |
| [`examples/copart_watch.py`](examples/copart_watch.py) | re-reads one search page per round, appends changed fields to a CSV | 75 per round |
| [`examples/copart_lots.js`](examples/copart_lots.js) | Node 18 search, one JSON line per lot | 75 |

## Which proxy tier gets past the shell

Copart's pages are an Angular app behind Imperva. Every tier below returned HTTP 200, so the status code says nothing. What matters is whether the lot data is in the HTML that comes back:

| Request for `/lot/69970966` | Credits | Size | Vehicle JSON-LD | Detail rows |
|---|---|---|---|---|
| `render_js=false` | 1 | 273 KB | no (an `_Incapsula_Resource` script) | 0 |
| `render_js=true` | 5 | 404 KB | no | 0 |
| `render_js=true&premium_proxy=true` | 25 | 404 KB | no | 0 |
| `render_js=true&stealth_proxy=true` | 75 | 852 KB | yes | 12 |

The search page behaved the same way, so the client always sends `stealth_proxy=true`. Because a failed render is still a 200, both `search_page()` and `lot()` check for rows or JSON-LD and retry a billed empty page, up to three times. ScrapingBee's own HTTP 500s are not billed and are retried separately in `_get()`.

## What the search page holds

Each result is a table row keyed by the lot number. Trimmed from the rendered DOM (Angular's `_ngcontent` attributes and empty comments removed):

```html
<tr class="p-element p-selectable-row ng-star-inserted" data-lotnumber="69970966">
  <td><span class="p-column-title">Lot info</span>
    <a href="/lot/69970966/clean-title-2016-toyota-camry-xle-al-mobile-south">
      <span class="search_result_lot_detail"> 2016 TOYOTA CAMRY XLE </span></a>
    <span class="search_result_lot_number p-bold blue-heading"><a href="..."> 69970966 </a></span>
    <span class="p-ml-2 p-fs-14 ag-score-text">3.6</span></td>
  <td><span class="p-column-title">Vehicle info</span>
    <div class="search_result_veh_info_block text-black">
      <label class="search_result_meta_data_label">Odometer</label><div> 196152 <p>(ACTUAL)</p></div></div></td>
  <td><span class="p-column-title">Condition</span>
    <div class="search_result_condition_block">
      <span class="text-black p-bold" title="CERTIFICATE OF TITLE"> Clean Title ( CT - AL ) </span>
      <div class="p-mt-2"><span class="text-black p-bold">Minor Dent/Scratches Damage</span></div>
      <div class="p-mt-2"><span class="text-black p-bold">Keys available</span></div></div></td>
  <td><span class="p-column-title">Sale info</span>
    <span class="search_result_yard_location_label blue-heading"> AL - MOBILE SOUTH </span>
    <div class="p-mt-2 p-mb-2"><span> A / 289 </span></div>
    <span class="search_result_lot_sale_status text-green live_status"> Live now </span></td>
  <td><span class="p-column-title">Bids</span>
    <label class="search_result_meta_data_label">Current bid:</label>
    <span class="search_result_amount_block text-black p-bold">
      <span class="currencyAmount">$2,100.00</span><span class="currencyCode">USD</span></span></td>
</tr>
```

Those class names map one to one onto the `extract_rules` the client sends, so ScrapingBee returns JSON and no HTML crosses the wire:

```python
SEARCH_RULES = {
    "showing": ".p-paginator-current",
    "rows": {
        "selector": "tr[data-lotnumber]",
        "type": "list",
        "output": {
            "lot": {"selector": "tr", "output": "@data-lotnumber"},
            "title": ".search_result_lot_detail",
            "vehicle": ".search_result_veh_info_block",
            "title_label": ".search_result_condition_block span[title]",
            "title_type": {"selector": ".search_result_condition_block span[title]", "output": "@title"},
            "condition": ".search_result_condition_block",
            "yard": ".search_result_yard_location_label",
            "status": "span.search_result_lot_sale_status",
            "bid": ".search_result_amount_block .currencyAmount",
            "currency": ".search_result_amount_block .currencyCode",
            "row": {"selector": "tr", "output": "text"},
            # ...href and grade as well, see the file
        },
    },
}
```

### Getting 100 rows instead of 4

A plain render of `/lotSearchResults?free=true&query=toyota%20camry` came back with the paginator reading "Showing 1 to 20 of 1000 entries" and only 4 `<tr data-lotnumber>` in the HTML. Two things are going on. The table defaults to 20 rows, and it mounts a row (the rows carry an `intersection-observer-component` attribute) only once that row has scrolled into view.

The client handles both inside a `js_scenario` `evaluate` step. It uses the table's own "rows per page" menu (20, 50 or 100), clicks `button.p-paginator-next` for later pages, then scrolls:

```js
const rpp=document.querySelector('.p-paginator-rpp-options');
if(PER!==20&&rpp){
  rpp.click();await sleep(500);
  const o=[...document.querySelectorAll('li[role=option]')].find(li=>li.textContent.trim()===String(PER));
  if(o){o.click();}
  await until(()=>cur().includes(' to '+PER+' of'),10000);
}
...
for(let i=0;i<110;i++){
  window.scrollBy(0,600);await sleep(250);
  const n=rows().length, bottom=window.innerHeight+window.scrollY>=document.body.scrollHeight-5;
  if(n>=PER&&filled()>=n)break;
  stable=(n===last)?stable+1:0;last=n;
  if(bottom&&stable>=6)break;
}
```

The scroll step size matters. Our first version jumped 1,500 px every 150 ms and got 75 rows back, about half of them with no odometer, title or bid. At 600 px every 250 ms, both pages of a two page "honda civic" run came back with 100 rows each, and the two pages shared no lot numbers. Each page is a separate 75 credit call; single calls took 25 to 30 seconds.

In the scenario, `wait_for: "tr[data-lotnumber]"` runs before the `evaluate`, and `"strict": false` keeps one missing control from failing the whole request.

### Parsing a row

`SearchLot.from_row()` splits the cell text into typed fields. The condition cell is the busiest one: `"Clean Title ( CT - AL ) Minor Dent/Scratches Damage Keys available"` becomes four values.

```python
label = _clean(r.get("title_label"))                       # "Clean Title ( CT - AL )"
tl = re.match(r"(.+?)\s*\(\s*(\w+)\s*-\s*(\w+)\s*\)", label)  # group, title code, state
cond = _clean(r.get("condition"))
rest = cond[len(label):].strip() if label and cond.startswith(label) else cond
dmg = re.match(r"(.*?)\s*Damage\b\s*(.*)", rest)           # "Minor Dent/Scratches", "Keys available"
```

One row from `f150.json`:

```json
{"lot_number": 69190496, "title": "2016 FORD F150 SUPERCREW", "year": 2016, "make": "FORD",
 "model": "F150 SUPERCREW", "odometer": 131616, "odometer_status": "ACTUAL",
 "est_retail_value": null, "title_group": "Clean Title", "title_code": "CT", "title_state": "TX",
 "title_type": "CERTIFICATE OF TITLE", "damage": "Minor Dent/Scratches", "keys": "Keys available",
 "yard": "TX - CORPUS CHRISTI", "lane": "A", "item": "246", "sale_status": "Live now",
 "current_bid": 5300.0, "currency": "USD", "grade": 4.1,
 "url": "https://www.copart.com/lot/69190496/clean-title-2016-ford-f150-supercrew-tx-corpus-christi"}
```

Across those 100 F150 lots: 73 clean title, 26 salvage, 1 non-repairable; odometer `ACTUAL` 75, `NOT ACTUAL` 17, `EXEMPT` 8; 92 with keys; an estimated retail value on 67; a condition grade on 46; a lane and item on 31 (the lots already in a live sale). Five were in Canadian yards and priced in CAD, even on the US site, so keep `currency` next to every bid.

Some values look odd but are what Copart prints. The most common damage on that page was `Ao` (31 lots). Some makes are cut short (`2007 HOND CIVIC`). And lots not yet assigned to a sale show "Upcoming lot" in the Bids cell, with no bid and often no title group or odometer status. The parser keeps all of these as they are and leaves missing values empty.

## Reading one lot page

`/lot/<number>` carries more than the search row: a JSON-LD graph in the head and a label/value list in the page body. The JSON-LD from lot 69970966, rendered with the stealth proxy:

```json
{"@type": "Vehicle",
 "name": "2016 TOYOTA CAMRY XLE  ",
 "brand": {"@type": "Brand", "name": "TOYOTA"}, "model": "CAMRY", "vehicleModelDate": 2016,
 "vehicleTransmission": "AUTOMATIC", "bodyType": "SEDAN", "fuelType": "GAS",
 "mileageFromOdometer": {"@type": "QuantitativeValue", "value": 196152, "unitCode": "MI"},
 "color": "BURGUNDY", "vehicleIdentificationNumber": "4T1BF1FK4GU******",
 "offers": {"@type": "Offer", "priceCurrency": "USD", "price": 2100,
            "priceValidUntil": "2026-10-29T18:00:00-05:00", ...}}
{"@type": "Product", "sku": 69970966, ...,
 "additionalProperty": {"@type": "PropertyValue", "name": "Auction Status", "value": "LIVE_BIDDING"}}
{"@type": "Event", "name": "Copart Online Auction - 2016 TOYOTA CAMRY XLE  ",
 "startDate": "2026-10-29T16:00:00-05:00", "endDate": "2026-10-29T18:00:00-05:00", ...}
```

The graph also holds an `Organization` and a `BreadcrumbList`. The detail rows sit in plain markup:

```html
<div class="lot-details-information ng-star-inserted">
  <label class="lot-details-information-label">Title code:</label>
  <div class="lot-details-information-value p-gap-4 p-flex-wrap">
    <span>AL -</span><span> Certificate Of Title - Clean Title </span></div></div>
<div class="lot-details-information ng-star-inserted">
  <label class="lot-details-information-label">Engine type:</label>
  <div class="p-d-flex p-ai-center lot-details-information-value"><span>2.5L 4</span></div></div>
```

`LOT_RULES` grabs both. One snag: asking `extract_rules` for a `<script>` element with `"output": "text"` returned empty strings in our test, so the client asks for `"output": "html"` and strips the tag before `json.loads`:

```python
LOT_RULES = {
    "h1": "h1.ldp-header-title",
    "ld": {"selector": "script[type='application/ld+json']", "type": "list", "output": "html"},
    "details": {
        "selector": ".lot-details-information",
        "type": "list",
        "output": {"label": ".lot-details-information-label", "value": ".lot-details-information-value"},
    },
    "sale_name": "#saleNameLink",
    "location": "#locationInfoButton",
    "grade": ".ag-score-text",
}
```

```bash
python examples/copart_client.py lot 71227066 99999999 --json lots.json
```

```
{"lot_number": 71227066, "title": "2026 HONDA CIVIC LX", "year": 2026, "make": "HONDA", "model": "CIVIC",
 "body": "SEDAN", "vin_masked": "2HGFE2F23TH******", "odometer": 25323, "color": "WHITE", "fuel": "GAS",
 "transmission": "AUTOMATIC", "current_bid": 15200, "currency": "USD", "auction_status": "LIVE_BIDDING",
 "auction_start": "2026-10-29T16:00:00-05:00", "auction_end": "2026-10-29T18:00:00-05:00",
 "sale_name": "WHOLESALE AUCTION", "location": "TN - NASHVILLE", "grade": 2.7,
 "details": {"Seller": "Santander", "Title code": "TN - Certificate Of Title - Clean Title",
             "Odometer": "25,323 mi Actual", "Color": "White", "Has key": "Yes", "Engine type": "2.0L 4",
             "Transmission": "Automatic", "Vehicle type": "Automobile", "Drivetrain": "FRONT WHEEL DRIVE",
             "Fuel": "Gas", "Sale date": "Thu. Oct 29, 2026 12:00 PM EDT",
             "Notes": "There are no notes for this lot"}, ...}
{"lot": 99999999, "found": false}
1 of 2 lots found, 75 credits
```

The made up lot number came back as HTTP 410 from Copart, which ScrapingBee passed through and did not bill. `lot()` turns a 410 into `None`. Primary damage is not in the lot page's label rows, so if you need it, take it from the search row.

## Watching bids: the Copart CSV sales data you can build yourself

Copart's own sales data download is tied to a member account, so this repo makes its CSV from the public search table instead. [`examples/copart_watch.py`](examples/copart_watch.py) re-reads one search page per round, compares each lot with the last round and appends a row per changed field to `copart_changes.csv`:

```bash
python examples/copart_watch.py "toyota camry" --rounds 2 --every 4
```

Run on 2026-10-29 while the 12:00 EDT sales were live:

```
2026-10-29T17:18:40Z: 100 lots, 100 changes, 75 credits
  73237726 | 2008 TOYOTA CAMRY SOLARA SLE V6 | _first_seen |  | Auction in 4D 23H 41min
  69970966 | 2016 TOYOTA CAMRY XLE | _first_seen |  | Live now
  ...
2026-10-29T17:22:54Z: 100 lots, 8 changes, 75 credits
  70873266 | 2021 TOYOTA CAMRY SE | current_bid | 7600.0 | 8300.0
  63473386 | 2020 TOYOTA CAMRY SE | current_bid | 3200.0 | 6500.0
  64192396 | 2019 TOYOTA CAMRY LE | current_bid | 550.0 | 625.0
  70767066 | 2024 TOYOTA CAMRY SE | current_bid | 425.0 | 550.0
  71401016 | 2026 TOYOTA CAMRY XSE | current_bid | 9900.0 | 10000.0
  72143336 | 2023 TOYOTA CAMRY SE | current_bid | 900.0 | 1450.0
  65180846 | 2011 TOYOTA CAMRY XLE V6 | _first_seen |  | 0.0
  68280996 | 2007 TOYOTA CAMRY LE | _left_results | Live now |
```

The first version logged 71 changes in round two, and 63 of them were the countdown ("Auction in 4D 23H 46min" turning into "...42min"). `phase()` now folds every "Auction in ..." value into one state, so only a move between Scheduled, Live now and Upcoming lot counts as a status change:

```python
def phase(status: str | None) -> str:
    return "Scheduled" if (status or "").startswith("Auction in") else (status or "")
```

A lot that drops out of the results (sold in the live sale, or moved) gets one `_left_results` row. State lives in `copart_state.json`, so `--rounds 1` from cron works the same as a loop. Use `--only 69970966 73237726` to keep the log to the lots you care about; the round still costs 75 credits, because it is one page either way.

## Node.js

```bash
SCRAPINGBEE_API_KEY=... node examples/copart_lots.js "ford f150" > f150.jsonl
```

```
Showing 1 to 100 of 1000 entries, 100 rows, 75 credits
{"lot":65012146,"title":"2023 FORD F150 SUPERCREW","odometer":61661,"title_group":"Clean Title","yard":"NC - CONCORD","current_bid":0,"currency":"USD"}
{"lot":67825876,"title":"2019 FORD F150 SUPER CAB","odometer":258300,"title_group":"Clean Title","yard":"QC - MONTREAL","current_bid":0,"currency":"CAD"}
```

It runs the same in-page steps as the Python client. An earlier Node version had its own shorter scroll script and returned 13 rows for this query, with the paginator text empty, while Python returned 100. Copying the Python steps fixed it.

## Copart API documentation, in short

Copart does not publish an API for public lot data that this repo could build on, so the selectors above are the reference. If Copart renames a class, the fix goes in `SEARCH_RULES` or `LOT_RULES`. The [ScrapingBee docs](https://www.scrapingbee.com/documentation/) cover every parameter used here, the [data extraction page](https://www.scrapingbee.com/documentation/data-extraction/) covers `extract_rules`, and the [JavaScript scenario docs](https://www.scrapingbee.com/documentation/js-scenario/) cover `evaluate` and `wait_for`.

| Job | Calls | Credits |
|---|---|---|
| 100 lots from search | 1 | 75 |
| 300 lots from search | 3 | 225 |
| One lot page | 1 | 75 |
| A lot number that does not exist (HTTP 410) | 1 | 0 |
| Watch up to 100 lots, one round | 1 | 75 |
| Watch them every 30 minutes for 8 hours | 16 | 1,200 |

For anything above a handful of lots, the search table is the cheap way in: 100 lots cost the same as one lot page. New accounts get 1,000 free credits, and plans are on the [pricing page](https://www.scrapingbee.com/pricing/).

## Logged out only

Everything in this repo is what copart.com shows a visitor who has not signed in. The VIN arrives with its last six characters masked, in the table and in the JSON-LD alike, and bidding, watchlists and member downloads are not touched. ScrapingBee's terms forbid scraping behind a login, so nothing here signs in or carries a member session.

Only copart.com was tested. Other Copart country sites may share the markup, but nothing here was checked against them.

Related endpoints: [Carfax API](https://www.scrapingbee.com/scrapers/carfax-api/), [Autotrader API](https://www.scrapingbee.com/scrapers/autotrader-api/), [CarGurus API](https://www.scrapingbee.com/scrapers/cargurus-api/), [Cars.com API](https://www.scrapingbee.com/scrapers/cars.com-api/), [AutoScout24 API](https://www.scrapingbee.com/scrapers/autoscout24-api/), [vehicle data API](https://www.scrapingbee.com/scrapers/vehicle-data-api/), [eBay scraper](https://www.scrapingbee.com/scrapers/ebay-scraper/).

## FAQ

**Is there official Copart API documentation for lot data?**
Not a public one this repo relies on. The tables and selectors in this README are measured from the rendered pages, and the two rule dictionaries in `copart_client.py` are the place to update when Copart changes its markup.

**Why does every request cost 75 credits?**
The lot data only appears after the Angular app runs, and on the 1, 5 and 25 credit tiers it did not run past Imperva in our tests, even though each returned HTTP 200. Stealth rendering was the only tier that produced lots.

**How do I export Copart sales data to CSV without an account?**
Run `copart_client.py search "<make model>" --pages 3 --csv out.csv` for a snapshot, or `copart_watch.py` for a running log of bid and status changes. Both use only the public search table.

**Can I get the full VIN from Copart?**
Not logged out. Both the search table and the lot page JSON-LD mask the last six characters.

**How many lots can one search return?**
The paginator read "of 1000 entries" for every query we tried, so treat 1,000 as the most one query exposes. Narrow the query (make, model and year) to reach lots past that.

## License

MIT. See [LICENSE](LICENSE).
