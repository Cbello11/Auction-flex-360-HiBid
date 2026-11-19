"""Turn eBay listings into lots: download every photo, clean the description,
and add rows to lots.csv. Also writes ebay_to_end.csv - the eBay listings you
MUST end before the auction goes live, so nothing sells twice.

Input is a JSON file holding a list of eBay listings, either in the raw shape the
eBay Trading API GetItem call returns (what the eBay MCP's ebay_get_listing gives)
or already simplified (see normalize_item).
"""
import csv
import html
import io
import json
import re
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from PIL import Image

from .intake import INTAKE_COLUMNS, read_lots, write_lots

ALLOWED_PHOTO_HOST = "i.ebayimg.com"
END_LIST = "ebay_to_end.csv"


# ---------------------------------------------------------------- reading items

def _text(value):
    """eBay JSON wraps numbers as {"#text": 49.99, "@_currencyID": "USD"}."""
    if isinstance(value, dict):
        value = value.get("#text", "")
    return "" if value is None else str(value)


def _as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def normalize_item(raw):
    """Accept a raw GetItem object or a simplified dict; return a simplified dict."""
    if "item_id" in raw:  # already simplified
        item = dict(raw)
        item.setdefault("pictures", [])
        item.setdefault("specifics", {})
        return item

    specifics = {}
    for pair in _as_list((raw.get("ItemSpecifics") or {}).get("NameValueList")):
        name = str(pair.get("Name", "")).strip()
        values = [str(v) for v in _as_list(pair.get("Value")) if str(v).strip()]
        if name and values:
            specifics[name] = ", ".join(values)

    return {
        "item_id": _text(raw.get("ItemID")),
        "title": _text(raw.get("Title")),
        "description": raw.get("Description") or "",
        "condition": _text(raw.get("ConditionDisplayName")),
        "category": _text((raw.get("PrimaryCategory") or {}).get("CategoryName")),
        "sku": _text(raw.get("SKU")),
        "price": _text(raw.get("StartPrice")) or _text((raw.get("SellingStatus") or {}).get("CurrentPrice")),
        "quantity": _text(raw.get("Quantity")) or "1",
        "url": _text((raw.get("ListingDetails") or {}).get("ViewItemURL")),
        "pictures": [str(u) for u in _as_list((raw.get("PictureDetails") or {}).get("PictureURL"))],
        "specifics": specifics,
    }


def load_items(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict):  # a single listing, or {"items": [...]}
        data = data.get("items", [data])
    return [normalize_item(d) for d in data]


# ------------------------------------------------------- cleaning descriptions

class _TextExtractor(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "table"}

    def __init__(self):
        super().__init__()
        self.parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


# Sentences about eBay checkout, shipping or returns are wrong on HiBid.
EBAY_ONLY = re.compile(
    r"\b(free shipping|ships?\b|shipping|shipped|same[- ]day|handling time|"
    r"returns?\b|return policy|refund|best offer|make (an|me an) offer|buy it now|"
    r"other (items|listings)|my store|feedback|ebay|combined (shipping|postage)|"
    r"international (buyers|shipping)|import (duties|charges)|watch ?list|"
    r"bundle (and|&) save|message me|send (me )?a message)",
    re.IGNORECASE,
)


def clean_description(raw_html):
    """HTML -> plain text, with eBay-only sentences removed.

    Returns (text, removed) where removed lists the sentences that were dropped,
    so a person can check nothing important went.
    """
    parser = _TextExtractor()
    parser.feed(html.unescape(raw_html or ""))
    text = "".join(parser.parts)

    kept_lines, removed = [], []
    for line in text.split("\n"):
        line = re.sub(r"[ \t ]+", " ", line).strip()
        if not line:
            continue
        sentences = re.split(r"(?<=[.!?])\s+", line)
        keep = []
        for s in sentences:
            (removed if EBAY_ONLY.search(s) else keep).append(s)
        if keep:
            kept_lines.append(" ".join(keep))
    return "\n".join(kept_lines), removed


def build_description(item):
    text, removed = clean_description(item["description"])
    parts = [text] if text else []
    if item.get("condition") and "condition:" not in text.lower():
        parts.append(f"Condition: {item['condition']}.")
    if item.get("specifics"):
        parts.append("\n".join(f"{k}: {v}" for k, v in item["specifics"].items()))
    return "\n\n".join(p for p in parts if p), removed


# ------------------------------------------------------------- photo download

def photo_candidates(url):
    """Largest-first URLs to try for one eBay photo. Only eBay's photo host is allowed."""
    if not re.match(rf"^https://{re.escape(ALLOWED_PHOTO_HOST)}/", url):
        return []
    found = re.search(r"/(?:z|g)/([A-Za-z0-9~_-]{10,})/", url)
    candidates = []
    if found:
        candidates.append(f"https://{ALLOWED_PHOTO_HOST}/images/g/{found.group(1)}/s-l1600.jpg")
    if "$_" in url:
        candidates.append(re.sub(r"\$_\d+\.JPG", "$_57.JPG", url, flags=re.IGNORECASE))
    candidates.append(url)
    seen, out = set(), []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def http_download(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "af360-lot-builder/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_photo(url, downloader=http_download):
    """Return (bytes, (w, h), used_url) for the biggest version that downloads."""
    last_error = None
    for candidate in photo_candidates(url):
        try:
            data = downloader(candidate)
            with Image.open(io.BytesIO(data)) as im:
                im.verify()
            with Image.open(io.BytesIO(data)) as im:
                size = im.size
            return data, size, candidate
        except Exception as exc:  # try the next size
            last_error = exc
    if not photo_candidates(url):
        raise ValueError(f"not an eBay photo URL, skipped: {url}")
    raise ValueError(f"could not download {url} ({last_error})")


# -------------------------------------------------------------------- import

def import_items(auction_dir, items, cfg, downloader=http_download,
                 seller_code="", start_bid_percent=None, pause=0.2):
    """Add eBay items to an auction folder. Returns a report dict.

    Items already imported (matched by eBay item number) are skipped, so this is
    safe to run twice.
    """
    auction_dir = Path(auction_dir)
    (auction_dir / "photos").mkdir(parents=True, exist_ok=True)
    rows = read_lots(auction_dir) if (auction_dir / "lots.csv").exists() else []
    for r in rows:
        r.pop("_line", None)

    already = {m.group(1) for r in rows for m in [re.search(r"ebay:(\d+)", r.get("notes", ""))] if m}
    used_lots = {r.get("lot") for r in rows if r.get("lot")}
    next_lot = max([int(x) for x in used_lots if x and x.isdigit()] or [0]) + 1

    report = {"added": [], "skipped": [], "warnings": [], "removed_text": []}
    end_rows = []

    for item in items:
        iid = item["item_id"]
        if not iid:
            report["warnings"].append("an item with no eBay item number was skipped")
            continue
        if iid in already:
            report["skipped"].append(iid)
            continue
        while str(next_lot) in used_lots:
            next_lot += 1
        lot = str(next_lot)
        used_lots.add(lot)

        folder = auction_dir / "photos" / lot
        folder.mkdir(parents=True, exist_ok=True)
        saved = 0
        for n, url in enumerate(item["pictures"], start=1):
            try:
                data, (w, h), _ = fetch_photo(url, downloader)
            except ValueError as exc:
                report["warnings"].append(f"Lot {lot} (eBay {iid}): {exc}")
                continue
            (folder / f"{n:02d}.jpg").write_bytes(data)
            saved += 1
            if max(w, h) < 1000:
                report["warnings"].append(f"Lot {lot} (eBay {iid}): photo {n} is only {w}x{h}")
            if pause:
                time.sleep(pause)
        if not saved:
            report["warnings"].append(f"Lot {lot} (eBay {iid}): NO photos downloaded")

        description, removed = build_description(item)
        for s in removed:
            report["removed_text"].append(f"Lot {lot}: {s}")

        qty = item.get("quantity") or "1"
        if qty.isdigit() and int(qty) > 1:
            report["warnings"].append(
                f"Lot {lot} (eBay {iid}): eBay has quantity {qty}. Set to 1 lot - decide if "
                "this should be one lot of all of them or split into separate lots")

        starting_bid = ""
        if start_bid_percent and item.get("price"):
            try:
                starting_bid = str(max(1, round(float(item["price"]) * start_bid_percent / 100)))
            except ValueError:
                pass

        category = item.get("category", "").split(":")[-1].strip()
        rows.append({
            "lot": lot,
            "title": item["title"],
            "description": description,
            "quantity": "1",
            "seller_code": seller_code,
            "category": category,
            "starting_bid": starting_bid,
            "photo_folder": lot,
            "notes": f"ebay:{iid} sku:{item.get('sku', '')} ebay_price:{item.get('price', '')}",
        })
        end_rows.append({"lot": lot, "ebay_item_id": iid, "sku": item.get("sku", ""),
                         "title": item["title"], "url": item.get("url", "")})
        report["added"].append((lot, iid, saved))

    write_lots(auction_dir, rows)

    end_path = auction_dir / END_LIST
    existing = []
    if end_path.exists():
        with open(end_path, newline="", encoding="utf-8") as fh:
            existing = list(csv.DictReader(fh))
    with open(end_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["lot", "ebay_item_id", "sku", "title", "url"])
        writer.writeheader()
        writer.writerows(existing + end_rows)
    return report


def report_text(report):
    lines = [f"Added {len(report['added'])} lot(s):"]
    for lot, iid, n in report["added"]:
        lines.append(f"  lot {lot}  <- eBay {iid}  ({n} photo(s))")
    if report["skipped"]:
        lines.append(f"Skipped {len(report['skipped'])} already imported: {', '.join(report['skipped'])}")
    for w in report["warnings"]:
        lines.append(f"WARNING {w}")
    if report["removed_text"]:
        lines.append("Removed eBay-only wording (check nothing important went):")
        lines.extend(f"  - {s}" for s in report["removed_text"])
    lines.append(f"\nBEFORE THE AUCTION GOES LIVE: end every listing in {END_LIST} on eBay.")
    return "\n".join(lines)
