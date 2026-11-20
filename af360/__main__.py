"""Command line.

    python -m af360 new   auctions/<name>   make an empty intake folder
    python -m af360 scan  auctions/<name>   add a lots.csv row for each photo folder
    python -m af360 check auctions/<name>   check everything, write nothing
    python -m af360 build auctions/<name> [--no-zip]
                                            check, then build the AF360 upload package
    python -m af360 fetch-ebay auctions/<name> [--exclude-category "Remote Controls"] [--test]
                                            pull active listings from the eBay server on this phone
    python -m af360 from-ebay auctions/<name> [--items file.json] [--seller-code X]
                                            [--start-bid-percent 25]
                                            add eBay listings as lots (downloads photos)
"""
import argparse
import sys
from pathlib import Path

from .config import load_config
from .export import build
from .intake import new_auction, scan_photo_folders

COMMANDS = ["new", "scan", "check", "build", "fetch-ebay", "from-ebay"]


def run_fetch(args):
    from .fetch import DEFAULT_URL, fetch, read_token
    from .mcp_client import McpClient, McpError

    token = read_token(args.token_file)
    if not token:
        print("Note: no token found, trying without one. (Set --token-file if the server needs it.)")
    client = McpClient(args.url or DEFAULT_URL, token=token)
    try:
        if args.test:
            client.connect()
            data = client.call_tool("ebay_get_active_listings", {"entriesPerPage": 1, "page": 1})
            total = ((data or {}).get("ActiveList") or {}).get("PaginationResult", {}).get("TotalNumberOfEntries")
            print(f"Connected to the eBay server. Active listings: {total}")
            return 0
        result = fetch(args.auction_dir, client, exclude_categories=args.exclude_category or [],
                       pause=args.pause)
    except McpError as exc:
        print(f"Error: {exc}")
        return 1
    print(f"\nSaved {result['items']} listing(s) to {result['file']}")
    if result["excluded"]:
        print(f"Left out {len(result['excluded'])} listing(s) in excluded categories.")
    if result["failed"]:
        print(f"{len(result['failed'])} listing(s) failed. Run the same command again to retry them:")
        for iid, err in result["failed"][:10]:
            print(f"  {iid}: {err}")
        return 1
    print(f"Next: python -m af360 from-ebay {args.auction_dir} --seller-code <your code>")
    return 0


def run_from_ebay(args):
    from .ebay import import_items, load_items, report_text
    items_path = args.items or str(Path(args.auction_dir) / "ebay_items.json")
    if not Path(items_path).exists():
        print(f"Error: {items_path} not found. Run fetch-ebay first, or pass --items <file.json>.")
        return 1
    items = load_items(items_path)

    def progress(done, total, lot, iid, photos):
        print(f"  lot {lot} <- eBay {iid} ({photos} photo(s))   [{done} new so far, {total} in file]")

    report = import_items(args.auction_dir, items, load_config(args.config), seller_code=args.seller_code,
                          start_bid_percent=args.start_bid_percent, progress=progress)
    print(report_text(report))
    print(f"\nNext: python -m af360 check {args.auction_dir}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m af360", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("auction_dir", help="folder for one auction, e.g. auctions/2026-11-22-estate")
    parser.add_argument("--config", help="path to config.json (default: the one in this repo)")
    parser.add_argument("--items", help="from-ebay: JSON file of eBay listings (default: <auction>/ebay_items.json)")
    parser.add_argument("--seller-code", default="", help="from-ebay: AF360 seller code for every lot")
    parser.add_argument("--start-bid-percent", type=float,
                        help="from-ebay: starting bid as a %% of the eBay price (blank if not given)")
    parser.add_argument("--no-zip", action="store_true", help="build: skip images.zip (saves space)")
    parser.add_argument("--url", help="fetch-ebay: eBay MCP server URL (default http://127.0.0.1:3333/mcp)")
    parser.add_argument("--token-file", help="fetch-ebay: file holding the server's token")
    parser.add_argument("--exclude-category", action="append",
                        help="fetch-ebay: eBay category to leave out (repeatable)")
    parser.add_argument("--pause", type=float, default=0.3, help="fetch-ebay: seconds between eBay calls")
    parser.add_argument("--test", action="store_true", help="fetch-ebay: only check the connection")
    args = parser.parse_args(argv)
    cfg = load_config(args.config)

    try:
        if args.command == "fetch-ebay":
            return run_fetch(args)
        if args.command == "from-ebay":
            return run_from_ebay(args)

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

        report, summary = build(args.auction_dir, cfg, dry_run=(args.command == "check"),
                                make_zip=not args.no_zip)
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
