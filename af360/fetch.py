"""Fetch active eBay listings (full details + photo URLs) from the eBay MCP server.

Writes <auction>/ebay_items.json for `python -m af360 from-ebay`. Every listing
is cached in <auction>/ebay_cache/<item id>.json as soon as it arrives, so if
the phone sleeps or Termux is killed, running the same command again picks up
where it stopped instead of starting over.
"""
import json
import os
import time
from pathlib import Path

from .ebay import normalize_item
from .mcp_client import McpClient

DEFAULT_URL = "http://127.0.0.1:3333/mcp"
# Where the phone's eBay server keeps its token (inside the Ubuntu proot), seen from Termux.
DEFAULT_TOKEN_FILES = [
    "/data/data/com.termux/files/usr/var/lib/proot-distro/installed-rootfs/ubuntu/root/.config/ebay-connector/token",
]


def read_token(token_file=None):
    if os.environ.get("MCP_AUTH_TOKEN"):
        return os.environ["MCP_AUTH_TOKEN"].strip()
    for candidate in ([token_file] if token_file else DEFAULT_TOKEN_FILES):
        if candidate and Path(candidate).is_file():
            return Path(candidate).read_text().strip()
    return None


def _as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def list_active_ids(client, page_size=200, log=print):
    """Every active listing's item id, paging through ebay_get_active_listings."""
    ids, page, pages = [], 1, 1
    while page <= pages:
        data = client.call_tool("ebay_get_active_listings", {"entriesPerPage": page_size, "page": page})
        active = (data or {}).get("ActiveList") or {}
        for item in _as_list((active.get("ItemArray") or {}).get("Item")):
            ids.append(str(item.get("ItemID")))
        pages = int((active.get("PaginationResult") or {}).get("TotalNumberOfPages") or 1)
        log(f"  listing page {page}/{pages}: {len(ids)} so far")
        page += 1
    return list(dict.fromkeys(ids))  # drop duplicates, keep order


def slim(raw):
    """Keep only listing fields; drop the Seller block (account email) and other noise."""
    keep = ("ItemID", "Title", "Description", "ConditionDisplayName", "PrimaryCategory", "SKU",
            "StartPrice", "SellingStatus", "Quantity", "ListingDetails", "PictureDetails",
            "ItemSpecifics", "ListingType")
    out = {k: raw[k] for k in keep if k in raw}
    if "ListingDetails" in out:
        out["ListingDetails"] = {"ViewItemURL": out["ListingDetails"].get("ViewItemURL", "")}
    return out


def fetch(auction_dir, client, exclude_categories=(), only_ids=None, skip_ids=(), pause=0.3, log=print):
    auction_dir = Path(auction_dir)
    cache = auction_dir / "ebay_cache"
    cache.mkdir(parents=True, exist_ok=True)
    exclude = {c.strip().lower() for c in exclude_categories if c.strip()}
    skip = {str(s) for s in skip_ids}

    client.connect()
    ids = [str(i) for i in only_ids] if only_ids else list_active_ids(client, log=log)
    ids = [i for i in ids if i not in skip]
    log(f"{len(ids)} listing(s) to look at")

    items, excluded, failed = [], [], []
    for n, iid in enumerate(ids, start=1):
        cached = cache / f"{iid}.json"
        if cached.exists():
            raw = json.loads(cached.read_text(encoding="utf-8"))
        else:
            try:
                raw = slim(client.call_tool("ebay_get_listing", {"itemId": iid}))
            except Exception as exc:  # keep going; report at the end
                failed.append((iid, str(exc)[:200]))
                log(f"  [{n}/{len(ids)}] {iid}: FAILED ({str(exc)[:120]})")
                continue
            cached.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            if pause:
                time.sleep(pause)
        category = str(((raw.get("PrimaryCategory") or {}).get("CategoryName")) or "")
        leaf = category.split(":")[-1].strip().lower()
        if leaf in exclude or category.lower() in exclude:
            excluded.append(iid)
            continue
        items.append(normalize_item(raw))
        if n % 25 == 0 or n == len(ids):
            log(f"  [{n}/{len(ids)}] fetched")

    out = auction_dir / "ebay_items.json"
    out.write_text(json.dumps(items, ensure_ascii=False, indent=0), encoding="utf-8")
    return {"items": len(items), "excluded": excluded, "failed": failed, "file": out}
