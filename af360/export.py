"""Builds the upload package:

    auctions/<name>/output/
        af360_lots.csv     -> AF360: Lots > Import Lots
        images/1-1.jpg ... -> AF360: Lot Images (after the lots are imported)
        images.zip         same photos zipped, handy for moving between computers
        report.txt         what was built and any warnings
"""
import csv
import shutil
import zipfile
from pathlib import Path

from .config import FIELDS
from .images import PhotoError, process_photo
from .intake import read_lots
from .validate import prepare_lots, safe_lot_name


def build(auction_dir, cfg, dry_run=False, make_zip=True):
    """Validate and (unless dry_run) write the package. Returns (report, summary)."""
    auction_dir = Path(auction_dir)
    rows = read_lots(auction_dir)
    lots, report = prepare_lots(rows, auction_dir, cfg)
    summary = {"lots": len(lots), "photos": 0, "output": None}

    if not lots:
        report.error("lots.csv", "has no lots in it")
    if dry_run or not report.ok:
        return report, summary

    out = auction_dir / "output"
    images_dir = out / "images"
    tmp_dir = out / ".building"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    (tmp_dir / "images").mkdir(parents=True)

    # Photos first: a bad photo should stop the build before we write a CSV.
    for lot in lots:
        for n, src in enumerate(lot["_photos"], start=1):
            name = f"{safe_lot_name(lot['lot'])}-{n}.jpg"
            try:
                process_photo(src, tmp_dir / "images" / name, cfg["images"])
                summary["photos"] += 1
            except PhotoError as exc:
                report.error(f"Lot {lot['lot']}", str(exc))

    if not report.ok:
        shutil.rmtree(tmp_dir)
        return report, summary

    columns = cfg["columns"]
    with open(tmp_dir / "af360_lots.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, quoting=csv.QUOTE_MINIMAL)
        writer.writerow([columns[f] for f in FIELDS])
        for lot in lots:
            writer.writerow([lot.get(f, "") for f in FIELDS])

    if make_zip:
        with zipfile.ZipFile(tmp_dir / "images.zip", "w", zipfile.ZIP_DEFLATED) as zf:
            for img in sorted((tmp_dir / "images").iterdir()):
                zf.write(img, img.name)

    with open(tmp_dir / "report.txt", "w", encoding="utf-8") as fh:
        fh.write(f"Lots: {summary['lots']}\nPhotos: {summary['photos']}\n\n")
        fh.write(report.text() + "\n\n")
        fh.write("Next steps in Auction Flex 360:\n")
        fh.write("  1. Lots > Import Lots > Choose File > af360_lots.csv\n")
        fh.write("     Check the field mapping (File Header -> DB Column), then Import.\n")
        fh.write("  2. Lot Images > select everything in the images folder > Upload.\n")
        fh.write("  3. Review a few lots, then Upload Auction to HiBid.\n")

    # Swap the finished build into place only once everything succeeded.
    if out.exists():
        for item in ("images", "af360_lots.csv", "images.zip", "report.txt"):
            target = out / item
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
    for item in tmp_dir.iterdir():
        shutil.move(str(item), out / item.name)
    tmp_dir.rmdir()
    summary["output"] = out
    return report, summary
