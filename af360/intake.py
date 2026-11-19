"""Reads and creates the intake sheet (lots.csv) for one auction.

Intake folder layout:

    auctions/<auction-name>/
        lots.csv            one row per lot (you or Claude fill this in)
        photos/
            1/              photos for lot 1 (any file names)
            2/
            ...
"""
import csv
from pathlib import Path

from .config import FIELDS

# Columns in the intake sheet: every export field plus where the photos live.
INTAKE_COLUMNS = FIELDS + ["photo_folder", "notes"]

PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".bmp", ".tif", ".tiff"}


def read_lots(auction_dir):
    """Return a list of dicts, one per non-empty row of lots.csv."""
    sheet = Path(auction_dir) / "lots.csv"
    if not sheet.exists():
        raise FileNotFoundError(f"No lots.csv found in {auction_dir}")
    # utf-8-sig quietly removes the hidden marker Excel adds to the start of files.
    with open(sheet, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        rows = []
        for line_no, raw in enumerate(reader, start=2):  # line 1 is the header
            row = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k}
            if not any(row.values()):
                continue  # skip blank lines
            row["_line"] = line_no
            rows.append(row)
    return rows


def write_lots(auction_dir, rows):
    sheet = Path(auction_dir) / "lots.csv"
    with open(sheet, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=INTAKE_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in INTAKE_COLUMNS})


def list_photos(folder):
    """Photos in a folder, sorted by file name (so IMG_001 comes before IMG_002)."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    return sorted(
        (p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in PHOTO_EXTENSIONS
         and not p.name.startswith(".")),
        key=lambda p: p.name.lower(),
    )


def new_auction(auction_dir):
    """Create an empty intake folder. Refuses to overwrite an existing lots.csv."""
    auction_dir = Path(auction_dir)
    (auction_dir / "photos").mkdir(parents=True, exist_ok=True)
    sheet = auction_dir / "lots.csv"
    if sheet.exists():
        raise FileExistsError(f"{sheet} already exists - not overwriting it.")
    write_lots(auction_dir, [])
    return auction_dir


def scan_photo_folders(auction_dir, defaults=None):
    """Add a row to lots.csv for every photos/<folder> that has no row yet.

    Existing rows are kept exactly as they are. Returns the folder names added.
    """
    auction_dir = Path(auction_dir)
    photos_root = auction_dir / "photos"
    sheet = auction_dir / "lots.csv"
    rows = read_lots(auction_dir) if sheet.exists() else []
    used = {r.get("photo_folder") or r.get("lot") for r in rows}

    def sort_key(p):
        return (0, int(p.name), "") if p.name.isdigit() else (1, 0, p.name.lower())

    added = []
    folders = sorted((p for p in photos_root.iterdir() if p.is_dir()), key=sort_key) \
        if photos_root.is_dir() else []
    for folder in folders:
        if folder.name in used or not list_photos(folder):
            continue
        row = dict(defaults or {})
        row["photo_folder"] = folder.name
        if folder.name.isdigit():
            row["lot"] = folder.name
        rows.append(row)
        added.append(folder.name)
    for r in rows:
        r.pop("_line", None)
    write_lots(auction_dir, rows)
    return added
