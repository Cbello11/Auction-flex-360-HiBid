"""Command line.

    python -m af360 new   auctions/<name>   make an empty intake folder
    python -m af360 scan  auctions/<name>   add a lots.csv row for each photo folder
    python -m af360 check auctions/<name>   check everything, write nothing
    python -m af360 build auctions/<name>   check, then build the AF360 upload package
"""
import argparse
import sys

from .config import load_config
from .export import build
from .intake import new_auction, scan_photo_folders


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m af360", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["new", "scan", "check", "build"])
    parser.add_argument("auction_dir", help="folder for one auction, e.g. auctions/2026-11-22-estate")
    parser.add_argument("--config", help="path to config.json (default: the one in this repo)")
    args = parser.parse_args(argv)
    cfg = load_config(args.config)

    try:
        if args.command == "new":
            path = new_auction(args.auction_dir)
            print(f"Created {path}/lots.csv and {path}/photos/")
            print("Put each lot's photos in its own folder: photos/1/, photos/2/, ...")
            print(f"Then run: python -m af360 scan {args.auction_dir}")
            return 0

        if args.command == "scan":
            added = scan_photo_folders(args.auction_dir, cfg["defaults"])
            print(f"Added {len(added)} row(s) to lots.csv: {', '.join(added) or 'none'}")
            print("Fill in title, description, seller_code (and prices) for each, then run check.")
            return 0

        report, summary = build(args.auction_dir, cfg, dry_run=(args.command == "check"))
        print(report.text())
        print()
        if not report.ok:
            print(f"Stopped: {len(report.errors)} error(s). Nothing was written.")
            return 1
        if args.command == "check":
            print(f"{summary['lots']} lot(s) ready. Run 'build' to make the upload package.")
        else:
            print(f"Built {summary['lots']} lot(s), {summary['photos']} photo(s) -> {summary['output']}")
            print("Upload steps are in report.txt in that folder.")
        return 0
    except (FileNotFoundError, FileExistsError) as exc:
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
