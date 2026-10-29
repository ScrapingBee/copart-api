"""Copart API client built on ScrapingBee.

Reads two public, logged out copart.com pages and nothing else:

  /lotSearchResults?free=true&query=...   the search results table, up to 100 lots per call
  /lot/<number>                           one lot: its JSON-LD plus the label/value detail rows

Both pages are Angular apps behind Imperva, so every call renders a real
browser with stealth_proxy=true (75 credits). Classic and premium rendering
came back HTTP 200 with an empty shell in testing, so the client checks for
data instead of trusting the status code.

Usage:
    export SCRAPINGBEE_API_KEY=...
    python copart_client.py search "toyota camry" --pages 2 --csv camry.csv
    python copart_client.py lot 69970966 --json lot.json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Iterator
from urllib.parse import quote, urljoin

import requests

API = "https://app.scrapingbee.com/api/v1/"
COPART = "https://www.copart.com"
STEALTH = {"render_js": "true", "stealth_proxy": "true", "country_code": "us"}

# Runs inside the rendered search page, the way a visitor would use it:
# pick PER rows in the table's own "rows per page" menu, click "Next Page"
# PAGE times, then scroll in 600 px steps. The table mounts a row, and fills
# its cells, only after the row has been scrolled into view: scrolling in
# 1500 px jumps left half the cells empty in testing.
SEARCH_JS = """(async()=>{
const sleep=t=>new Promise(r=>setTimeout(r,t));
const until=async(f,ms)=>{const t0=Date.now();while(Date.now()-t0<ms){if(f())return true;await sleep(200);}return false;};
const cur=()=>(document.querySelector('.p-paginator-current')||{}).textContent||'';
const PER=__PER__, PAGE=__PAGE__;
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
})()"""

SEARCH_RULES = {
    "showing": ".p-paginator-current",
    "rows": {
        "selector": "tr[data-lotnumber]",
        "type": "list",
        "output": {
            "lot": {"selector": "tr", "output": "@data-lotnumber"},
            "title": ".search_result_lot_detail",
            "href": {"selector": ".search_result_lot_detail_meta_data_block a", "output": "@href"},
            "grade": ".ag-score-text",
            "vehicle": ".search_result_veh_info_block",
            "title_label": ".search_result_condition_block span[title]",
            "title_type": {"selector": ".search_result_condition_block span[title]", "output": "@title"},
            "condition": ".search_result_condition_block",
            "yard": ".search_result_yard_location_label",
            "status": "span.search_result_lot_sale_status",
            "bid": ".search_result_amount_block .currencyAmount",
            "currency": ".search_result_amount_block .currencyCode",
            "row": {"selector": "tr", "output": "text"},
        },
    },
}

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


class CopartError(RuntimeError):
    pass


class EmptyPage(CopartError):
    """HTTP 200 from Copart, but the Angular app never filled the page. Billed."""


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def _money(text: str | None) -> float | None:
    m = re.search(r"[\d,]+(?:\.\d+)?", text or "")
    return float(m.group(0).replace(",", "")) if m else None


def _grade(text: str | None) -> float | None:
    t = _clean(text)
    return float(t) if re.fullmatch(r"\d+(?:\.\d+)?", t) else None


@dataclass
class SearchLot:
    lot_number: int
    title: str
    year: int | None
    make: str
    model: str
    url: str
    odometer: int | None
    odometer_status: str
    est_retail_value: float | None
    title_group: str
    title_code: str
    title_state: str
    title_type: str
    damage: str
    keys: str
    yard: str
    lane: str
    item: str
    sale_status: str
    current_bid: float | None
    currency: str
    grade: float | None

    @classmethod
    def from_row(cls, r: dict[str, Any]) -> "SearchLot":
        title = _clean(r.get("title"))
        parts = title.split()
        vehicle = _clean(r.get("vehicle"))
        odo = re.search(r"Odometer\s+([\d,]+)(?:\s*\(([^)]+)\))?", vehicle)
        retail = re.search(r"Estimated retail value\s+(\$[\d,.]+)", vehicle)
        # "Clean Title ( CT - AL )"  ->  group, title code, state
        label = _clean(r.get("title_label"))
        tl = re.match(r"(.+?)\s*\(\s*(\w+)\s*-\s*(\w+)\s*\)", label)
        # "Clean Title ( CT - AL ) Minor Dent/Scratches Damage Keys available"
        cond = _clean(r.get("condition"))
        rest = cond[len(label):].strip() if label and cond.startswith(label) else cond
        dmg = re.match(r"(.*?)\s*Damage\b\s*(.*)", rest)
        row = _clean(r.get("row"))
        lane = re.search(r"\b([A-Z])\s*/\s*(\d+)\b", row)
        # Lots not yet assigned to a sale show "Upcoming lot" in the Bids cell, no bid at all.
        bids_cell = row.rsplit(" Bids ", 1)[-1] if " Bids " in row else ""
        return cls(
            lot_number=int(r["lot"]),
            title=title,
            year=int(parts[0]) if parts and parts[0].isdigit() else None,
            make=parts[1] if len(parts) > 1 else "",
            model=" ".join(parts[2:]),
            url=urljoin(COPART, r.get("href") or f"/lot/{r['lot']}"),
            odometer=int(odo.group(1).replace(",", "")) if odo else None,
            odometer_status=(odo.group(2) or "").strip() if odo else "",
            est_retail_value=_money(retail.group(1)) if retail else None,
            title_group=tl.group(1) if tl else label,
            title_code=tl.group(2) if tl else "",
            title_state=tl.group(3) if tl else "",
            title_type=_clean(r.get("title_type")),
            damage=dmg.group(1) if dmg else "",
            keys=dmg.group(2) if dmg else rest,
            yard=_clean(r.get("yard")),
            lane=lane.group(1) if lane else "",
            item=lane.group(2) if lane else "",
            sale_status=_clean(r.get("status")) or ("Upcoming lot" if "Upcoming lot" in bids_cell else ""),
            current_bid=_money(r.get("bid")),
            currency=_clean(r.get("currency")),
            grade=_grade(r.get("grade")),
        )


@dataclass
class LotDetail:
    lot_number: int
    title: str
    year: int | None = None
    make: str = ""
    model: str = ""
    body: str = ""
    vin_masked: str = ""
    odometer: int | None = None
    color: str = ""
    fuel: str = ""
    transmission: str = ""
    current_bid: float | None = None
    currency: str = ""
    auction_status: str = ""
    auction_start: str = ""
    auction_end: str = ""
    image: str = ""
    sale_name: str = ""
    location: str = ""
    grade: float | None = None
    details: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_page(cls, lot: int, page: dict[str, Any]) -> "LotDetail":
        graph: list[dict] = []
        for raw in page.get("ld") or []:
            body = re.sub(r"^<script[^>]*>|</script>$", "", raw.strip())
            try:
                doc = json.loads(body)
            except ValueError:
                continue
            graph += doc.get("@graph", [doc])
        by_type = {g.get("@type"): g for g in graph}
        veh = by_type.get("Vehicle")
        if not veh:
            raise EmptyPage(f"lot {lot}: no Vehicle JSON-LD on the page")
        offer = veh.get("offers") or {}
        prop = (by_type.get("Product") or {}).get("additionalProperty") or {}
        event = by_type.get("Event") or {}
        details = {
            _clean(d.get("label")).rstrip(":"): _clean(d.get("value"))
            for d in page.get("details") or []
            if d.get("label")
        }
        return cls(
            lot_number=lot,
            title=_clean(veh.get("name")),
            year=veh.get("vehicleModelDate"),
            make=(veh.get("brand") or {}).get("name", ""),
            model=veh.get("model", ""),
            body=veh.get("bodyType", ""),
            vin_masked=veh.get("vehicleIdentificationNumber", ""),
            odometer=(veh.get("mileageFromOdometer") or {}).get("value"),
            color=veh.get("color", ""),
            fuel=veh.get("fuelType", ""),
            transmission=veh.get("vehicleTransmission", ""),
            current_bid=offer.get("price"),
            currency=offer.get("priceCurrency", ""),
            auction_status=prop.get("value", "") if prop.get("name") == "Auction Status" else "",
            auction_start=event.get("startDate", ""),
            auction_end=event.get("endDate", ""),
            image=veh.get("image", ""),
            sale_name=_clean(page.get("sale_name")),
            location=_clean(page.get("location")),
            grade=_grade(page.get("grade")),
            details=details,
        )


class CopartClient:
    def __init__(self, api_key: str | None = None, retries: int = 3, timeout: int = 150):
        self.api_key = api_key or os.environ.get("SCRAPINGBEE_API_KEY")
        if not self.api_key:
            raise CopartError("set SCRAPINGBEE_API_KEY")
        self.retries = retries
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {self.api_key}"
        self.credits = 0

    def _get(self, params: dict[str, str]) -> dict[str, Any]:
        """One ScrapingBee call. HTTP 500 is retried (ScrapingBee does not bill it)."""
        for attempt in range(1, self.retries + 1):
            r = self.session.get(API, params=params, timeout=self.timeout)
            if r.status_code == 200:
                self.credits += int(r.headers.get("spb-cost", 0))
                return r.json()
            if r.status_code == 500 and attempt < self.retries:
                print(f"  ScrapingBee 500, retry {attempt}", file=sys.stderr)
                time.sleep(2 * attempt)
                continue
            raise CopartError(f"ScrapingBee {r.status_code}: {r.text[:300]}")
        raise CopartError("no response")

    def search_page(self, query: str, page: int = 0, per_page: int = 100) -> tuple[str, list[SearchLot]]:
        if per_page not in (20, 50, 100):
            raise ValueError("per_page must be 20, 50 or 100, the table's own options")
        js = SEARCH_JS.replace("__PER__", str(per_page)).replace("__PAGE__", str(page))
        params = {
            "url": f"{COPART}/lotSearchResults?free=true&query={quote(query)}",
            **STEALTH,
            "js_scenario": json.dumps(
                {"instructions": [{"wait_for": "tr[data-lotnumber]"}, {"evaluate": js}], "strict": False}
            ),
            "extract_rules": json.dumps(SEARCH_RULES),
        }
        for attempt in range(1, self.retries + 1):
            data = self._get(params)
            rows = data.get("rows") or []
            if rows:
                return _clean(data.get("showing")), [SearchLot.from_row(r) for r in rows]
            print(f"  empty results table, billed (attempt {attempt})", file=sys.stderr)
        raise EmptyPage(f"search {query!r} page {page + 1}: no rows after {self.retries} tries")

    def search(self, query: str, pages: int = 1, per_page: int = 100) -> Iterator[SearchLot]:
        """Yield lots page by page. Each page is its own 75 credit call."""
        seen: set[int] = set()
        for p in range(pages):
            showing, lots = self.search_page(query, p, per_page)
            print(f"  page {p + 1}: {showing}, {len(lots)} rows", file=sys.stderr)
            for lot in lots:
                if lot.lot_number not in seen:
                    seen.add(lot.lot_number)
                    yield lot
            if len(lots) < per_page:
                break

    def lot(self, number: int) -> LotDetail | None:
        """Read one lot page. Returns None when Copart shows no lot under that number."""
        params = {
            "url": f"{COPART}/lot/{number}",
            **STEALTH,
            "wait_for": "h1.ldp-header-title",
            "extract_rules": json.dumps(LOT_RULES),
        }
        for attempt in range(1, self.retries + 1):
            try:
                page = self._get(params)
            except CopartError as e:
                if str(e).startswith("ScrapingBee 410"):
                    return None  # Copart answers 410 Gone for a lot number it does not have
                raise
            try:
                return LotDetail.from_page(number, page)
            except EmptyPage:
                print(f"  lot {number}: page rendered without lot data, billed (attempt {attempt})", file=sys.stderr)
        raise EmptyPage(f"lot {number}: no lot data after {self.retries} tries")


def write_csv(path: str, rows: list[Any]) -> None:
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=[f.name for f in fields(rows[0])])
        w.writeheader()
        for row in rows:
            d = asdict(row)
            if isinstance(d.get("details"), dict):
                d["details"] = json.dumps(d["details"], ensure_ascii=False)
            w.writerow(d)


def main() -> None:
    ap = argparse.ArgumentParser(description="Copart search and lot reader via ScrapingBee")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search", help="read the search results table")
    s.add_argument("query", help='free text, as typed in the Copart search box, e.g. "toyota camry"')
    s.add_argument("--pages", type=int, default=1)
    s.add_argument("--per-page", type=int, default=100, choices=(20, 50, 100))
    s.add_argument("--csv")
    s.add_argument("--json")
    lp = sub.add_parser("lot", help="read one or more lot pages")
    lp.add_argument("numbers", nargs="+", type=int)
    lp.add_argument("--csv")
    lp.add_argument("--json")
    a = ap.parse_args()

    client = CopartClient()
    rows: list[Any] = []
    if a.cmd == "search":
        rows = list(client.search(a.query, a.pages, a.per_page))
        for r in rows[:5]:
            bid = f"{r.current_bid:>9,.0f}" if r.current_bid is not None else "        -"
            print(f"{r.lot_number}  {r.title[:28]:28} {r.title_group[:14]:14} {r.damage[:20]:20} {bid} {r.currency}  {r.yard}")
        print(f"{len(rows)} lots, {client.credits} credits")
    else:
        for n in a.numbers:
            d = client.lot(n)
            if d is None:
                print(json.dumps({"lot": n, "found": False}))
                continue
            rows.append(d)
            print(json.dumps(asdict(d), ensure_ascii=False))
        print(f"{len(rows)} of {len(a.numbers)} lots found, {client.credits} credits", file=sys.stderr)
    if a.csv:
        write_csv(a.csv, rows)
        print(f"wrote {a.csv}", file=sys.stderr)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump([asdict(r) for r in rows], fh, ensure_ascii=False, indent=2)
        print(f"wrote {a.json}", file=sys.stderr)


if __name__ == "__main__":
    main()
