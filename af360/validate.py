"""Checks every lot before anything is exported.

ERRORS stop the build (the import would fail or be wrong).
WARNINGS are printed but do not stop it (worth a look, not fatal).
"""
import re
from pathlib import Path

from .intake import list_photos

MONEY_FIELDS = ["low_estimate", "high_estimate", "starting_bid", "reserve"]

# Curly quotes, long dashes etc. that Word/phones insert and older importers choke on.
_ASCII_SWAPS = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "…": "...", " ": " ",
    "″": '"', "′": "'",
}


def clean_text(text, plain_ascii=True):
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if plain_ascii:
        for bad, good in _ASCII_SWAPS.items():
            text = text.replace(bad, good)
    return text


def parse_money(value):
    """'$1,250.00' -> '1250.00'. Returns None if it isn't a number."""
    cleaned = value.replace("$", "").replace(",", "").strip()
    if cleaned == "":
        return ""
    try:
        number = float(cleaned)
    except ValueError:
        return None
    if number < 0:
        return None
    return f"{number:.2f}".rstrip("0").rstrip(".") if number != int(number) else str(int(number))


class Report:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def error(self, where, msg):
        self.errors.append(f"{where}: {msg}")

    def warn(self, where, msg):
        self.warnings.append(f"{where}: {msg}")

    @property
    def ok(self):
        return not self.errors

    def text(self):
        out = []
        for e in self.errors:
            out.append(f"ERROR   {e}")
        for w in self.warnings:
            out.append(f"WARNING {w}")
        if not out:
            out.append("All lots passed.")
        return "\n".join(out)


def prepare_lots(rows, auction_dir, cfg):
    """Clean, number and check every row. Returns (lots, report).

    Each returned lot has the cleaned field values plus '_photos' (list of Paths).
    """
    report = Report()
    defaults = cfg["defaults"]
    rules = cfg["rules"]
    plain = rules.get("plain_ascii_text", True)
    photos_root = Path(auction_dir) / "photos"

    # 1) Work out lot numbers. Blank lot numbers get the next free whole number.
    taken = set()
    for r in rows:
        if r.get("lot"):
            taken.add(r["lot"])
    next_num = max([int(x) for x in taken if x.isdigit()] or [0]) + 1

    lots = []
    seen = {}
    for r in rows:
        where = f"lots.csv line {r['_line']}"
        lot = {}
        for field, default in defaults.items():
            if not r.get(field) and default != "":
                r[field] = str(default)

        number = r.get("lot", "")
        if not number:
            while str(next_num) in taken:
                next_num += 1
            number = str(next_num)
            taken.add(number)
            report.warn(where, f"no lot number - assigned lot {number}")
        lot["lot"] = number
        where = f"Lot {number} (line {r['_line']})"

        if number in seen:
            report.error(where, f"duplicate lot number (also on line {seen[number]})")
        seen[number] = r["_line"]
        if not number.isdigit():
            report.warn(where, "lot number is not a plain whole number - confirm AF360 "
                               "matches photos named like '%s-1.jpg'" % number)

        # 2) Text fields.
        lot["title"] = clean_text(r.get("title"), plain).replace("\n", " ")
        lot["description"] = clean_text(r.get("description"), plain)
        lot["category"] = clean_text(r.get("category"), plain)
        lot["seller_code"] = clean_text(r.get("seller_code"), plain)

        if not lot["title"]:
            report.error(where, "title is empty")
        max_len = int(rules.get("max_title_length") or 0)
        if max_len and len(lot["title"]) > max_len:
            report.error(where, f"title is {len(lot['title'])} characters (limit {max_len})")
        if not lot["description"]:
            report.warn(where, "description is empty")
        if not lot["seller_code"]:
            report.error(where, "seller_code is empty (AF360: Customers > Customer List > Customer Code)")

        # 3) Quantity.
        qty = r.get("quantity", "")
        if not qty.isdigit() or int(qty) < 1:
            report.error(where, f"quantity must be a whole number 1 or more (got '{qty}')")
        lot["quantity"] = qty

        # 4) Money fields.
        for field in MONEY_FIELDS:
            raw = r.get(field, "")
            value = parse_money(raw)
            if value is None:
                report.error(where, f"{field} is not a valid amount (got '{raw}')")
                value = ""
            lot[field] = value
        lo, hi = lot["low_estimate"], lot["high_estimate"]
        if lo and hi and float(lo) > float(hi):
            report.error(where, f"low_estimate {lo} is higher than high_estimate {hi}")
        sb, rv = lot["starting_bid"], lot["reserve"]
        if sb and rv and float(rv) < float(sb):
            report.warn(where, f"reserve {rv} is below starting_bid {sb}")

        # 5) Photos.
        folder_name = r.get("photo_folder") or number
        photos = list_photos(photos_root / folder_name)
        lot["_photos"] = photos
        lot["_photo_folder"] = folder_name
        if not photos:
            msg = f"no photos found in photos/{folder_name}/"
            (report.error if rules.get("require_photos", True) else report.warn)(where, msg)

        lots.append(lot)

    # Photo folders that no lot points at are usually a typo.
    used = {l["_photo_folder"] for l in lots}
    if photos_root.is_dir():
        for folder in sorted(p for p in photos_root.iterdir() if p.is_dir()):
            if folder.name not in used and list_photos(folder):
                report.warn(f"photos/{folder.name}/", "has photos but no row in lots.csv uses it")

    lots.sort(key=lambda l: (0, int(l["lot"]), "") if l["lot"].isdigit() else (1, 0, l["lot"]))
    return lots, report


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")


def safe_lot_name(lot_number):
    return _SAFE_NAME.sub("_", lot_number)
