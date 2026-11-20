import csv
import io
import json

import pytest
from PIL import Image

from af360.__main__ import main
from af360.config import load_config
from af360.ebay import (clean_description, import_items, load_items, normalize_item,
                        photo_candidates)
from af360.export import build

# Trimmed copy of a real GetItem response shape (seller details removed).
RAW = {
    "ItemID": 820257856860,
    "Title": "USAF M-65 Field Jacket DCU 3 Color Desert Camo Medium Regular Air Combat Command",
    "Description": "<p>US Air Force M-65 field coat in three color desert camouflage (DCU).</p>"
                   "<p>Pre-owned. Please review all photos for condition.</p>"
                   "<p>Measured flat: chest 22 inches. Ships same day! Free shipping on orders.</p>"
                   "<p>Check out my other items &amp; save.</p>",
    "ConditionDisplayName": "Used",
    "PrimaryCategory": {"CategoryName": "Collectibles:Militaria:1992-2001:Original Period Items"},
    "StartPrice": {"#text": 65, "@_currencyID": "USD"},
    "Quantity": 1,
    "SKU": "MIL-001",
    "ListingDetails": {"ViewItemURL": "https://www.ebay.com/itm/820257856860"},
    "PictureDetails": {"PictureURL": [
        "https://i.ebayimg.com/00/s/MTYwMFgxNjAw/z/0hYAAeSwOW9qy0Da/$_12.JPG?set_id=880000500F",
        "https://i.ebayimg.com/00/s/MTIwMFgxNjAw/z/2OgAAeSwcjxqw0Xn/$_1.JPG?set_id=8800005007",
    ]},
    "ItemSpecifics": {"NameValueList": [
        {"Name": "Branch", "Value": ["Air Force"]},
        {"Name": "Country of Origin", "Value": "United States"},
    ]},
}


def jpeg(size):
    buf = io.BytesIO()
    Image.new("RGB", size, (90, 90, 90)).save(buf, "JPEG")
    return buf.getvalue()


def fake_downloader(calls):
    def dl(url):
        calls.append(url)
        if "s-l1600" in url:
            return jpeg((1600, 1200))
        raise OSError("not found")
    return dl


def test_normalize_raw_getitem():
    item = normalize_item(RAW)
    assert item["item_id"] == "820257856860"
    assert item["price"] == "65"
    assert len(item["pictures"]) == 2
    assert item["specifics"] == {"Branch": "Air Force", "Country of Origin": "United States"}


def test_clean_description_strips_ebay_wording_only():
    text, removed = clean_description(RAW["Description"])
    assert "desert camouflage" in text
    assert "Please review all photos for condition." in text
    assert "chest 22 inches" in text
    assert "Ships" not in text and "shipping" not in text and "other items" not in text
    assert len(removed) == 3


def test_photo_candidates_largest_first_and_host_locked():
    c = photo_candidates(RAW["PictureDetails"]["PictureURL"][1])
    assert c[0] == "https://i.ebayimg.com/images/g/2OgAAeSwcjxqw0Xn/s-l1600.jpg"
    assert any("$_57.JPG" in x for x in c)
    assert photo_candidates("https://evil.example.com/a.jpg") == []
    assert photo_candidates("http://i.ebayimg.com/x/z/abcdefghijk/$_1.JPG") == []  # not https


def test_import_then_build(tmp_path):
    d = tmp_path / "sale"
    calls = []
    report = import_items(d, [normalize_item(RAW)], load_config(), downloader=fake_downloader(calls),
                          seller_code="GRS", start_bid_percent=25, pause=0)
    assert report["added"] == [("1", "820257856860", 2)]
    with open(d / "lots.csv", newline="", encoding="utf-8") as fh:
        row = next(csv.DictReader(fh))
    assert row["title"].startswith("USAF M-65")
    assert row["starting_bid"] == "16"            # 25% of $65, rounded
    assert row["category"] == "Original Period Items"
    assert "Condition: Used." in row["description"]
    assert "Branch: Air Force" in row["description"]
    assert "ebay:820257856860" in row["notes"]
    with open(d / "ebay_to_end.csv", newline="", encoding="utf-8") as fh:
        assert next(csv.DictReader(fh))["ebay_item_id"] == "820257856860"

    built, summary = build(d, load_config())
    assert built.ok, built.text()
    assert summary["photos"] == 2


def test_second_run_skips_and_keeps_numbering(tmp_path):
    d = tmp_path / "sale"
    cfg = load_config()
    import_items(d, [normalize_item(RAW)], cfg, downloader=fake_downloader([]), pause=0)
    other = dict(RAW, ItemID=111111111111, Title="Second item")
    report = import_items(d, [normalize_item(RAW), normalize_item(other)], cfg,
                          downloader=fake_downloader([]), pause=0)
    assert report["skipped"] == ["820257856860"]
    assert report["added"][0][0] == "2"
    with open(d / "ebay_to_end.csv", newline="", encoding="utf-8") as fh:
        assert len(list(csv.DictReader(fh))) == 2


def test_failed_photos_warn_not_crash(tmp_path):
    def broken(url):
        raise OSError("offline")
    report = import_items(tmp_path / "s", [normalize_item(RAW)], load_config(), downloader=broken, pause=0)
    assert any("NO photos downloaded" in w for w in report["warnings"])


def test_quantity_over_one_is_flagged(tmp_path):
    raw = dict(RAW, Quantity=3)
    report = import_items(tmp_path / "s", [normalize_item(raw)], load_config(),
                          downloader=fake_downloader([]), pause=0)
    assert any("quantity 3" in w for w in report["warnings"])


def test_cli_requires_items(tmp_path):
    assert main(["from-ebay", str(tmp_path / "s")]) == 1


def test_load_items_accepts_list_or_single(tmp_path):
    p = tmp_path / "items.json"
    p.write_text(json.dumps(RAW))
    assert len(load_items(p)) == 1
    p.write_text(json.dumps([RAW, RAW]))
    assert len(load_items(p)) == 2


def test_max_photos_keeps_first_ones(tmp_path):
    raw = dict(RAW)
    raw["PictureDetails"] = {"PictureURL": [
        f"https://i.ebayimg.com/00/s/x/z/PICTURE{i:02d}abcdef/$_1.JPG" for i in range(8)]}
    calls = []
    report = import_items(tmp_path / "s", [normalize_item(raw)], load_config(),
                          downloader=fake_downloader(calls), pause=0, max_photos=3)
    assert report["added"][0][2] == 3
    assert "PICTURE00" in calls[0] and not any("PICTURE03" in c for c in calls)
