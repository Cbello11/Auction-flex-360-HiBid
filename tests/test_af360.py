import csv
import zipfile

import pytest
from PIL import Image

from af360.__main__ import main
from af360.config import load_config
from af360.export import build
from af360.intake import scan_photo_folders, write_lots
from af360.validate import parse_money


def make_photo(path, size=(800, 600), mode="RGB", exif_rotate=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new(mode, size, (200, 30, 30, 128) if mode == "RGBA" else (200, 30, 30))
    if exif_rotate:
        exif = Image.Exif()
        exif[0x0112] = 6  # "rotate 90 degrees" - what phones write
        im.save(path, exif=exif)
    else:
        im.save(path)


def good_row(lot, **over):
    row = {"lot": lot, "title": f"Item {lot}", "description": "Good condition.",
           "quantity": "1", "seller_code": "GRS", "starting_bid": "5"}
    row.update(over)
    return row


@pytest.fixture
def auction(tmp_path):
    d = tmp_path / "sale"
    (d / "photos").mkdir(parents=True)
    return d


@pytest.fixture
def cfg():
    return load_config()


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.reader(fh))


def test_happy_path_builds_csv_and_named_photos(auction, cfg):
    make_photo(auction / "photos/1/b.jpg")
    make_photo(auction / "photos/1/a.jpg")
    make_photo(auction / "photos/2/x.png")
    write_lots(auction, [good_row("1", starting_bid="$1,250.00"), good_row("2")])

    report, summary = build(auction, cfg)
    assert report.ok, report.text()
    out = auction / "output"
    rows = read_csv(out / "af360_lots.csv")
    assert rows[0][:3] == ["Lot #", "Lead", "Description"]
    assert rows[1][0] == "1" and rows[1][8] == "1250"
    assert sorted(p.name for p in (out / "images").iterdir()) == ["1-1.jpg", "1-2.jpg", "2-1.jpg"]
    assert sorted(zipfile.ZipFile(out / "images.zip").namelist()) == ["1-1.jpg", "1-2.jpg", "2-1.jpg"]
    assert summary["photos"] == 3


def test_photo_order_follows_file_names(auction, cfg):
    make_photo(auction / "photos/1/IMG_002.jpg", size=(100, 50))
    make_photo(auction / "photos/1/IMG_001.jpg", size=(300, 50))
    write_lots(auction, [good_row("1")])
    build(auction, cfg)
    with Image.open(auction / "output/images/1-1.jpg") as im:
        assert im.size == (300, 50)


def test_big_photos_shrink_and_phone_rotation_applied(auction, cfg):
    make_photo(auction / "photos/1/big.jpg", size=(4000, 3000))
    make_photo(auction / "photos/2/rot.jpg", size=(400, 300), exif_rotate=True)
    make_photo(auction / "photos/3/clear.png", mode="RGBA")
    write_lots(auction, [good_row("1"), good_row("2"), good_row("3")])
    report, _ = build(auction, cfg)
    assert report.ok, report.text()
    with Image.open(auction / "output/images/1-1.jpg") as im:
        assert im.size[0] <= 2048 and im.size[1] <= 1536
    with Image.open(auction / "output/images/2-1.jpg") as im:
        assert im.size == (300, 400)  # rotated upright
    with Image.open(auction / "output/images/3-1.jpg") as im:
        assert im.mode == "RGB"


def test_errors_stop_build_and_write_nothing(auction, cfg):
    make_photo(auction / "photos/1/a.jpg")
    write_lots(auction, [
        good_row("1", title=""),
        good_row("1", quantity="0"),            # duplicate + bad quantity
        good_row("3", seller_code=""),          # no seller, no photos
        good_row("4", low_estimate="50", high_estimate="20", reserve="abc"),
    ])
    report, _ = build(auction, cfg)
    text = report.text()
    assert not report.ok
    for expected in ["title is empty", "duplicate lot number", "quantity must be",
                     "seller_code is empty", "no photos found", "higher than high_estimate",
                     "reserve is not a valid amount"]:
        assert expected in text, expected
    assert not (auction / "output").exists()


def test_blank_lot_numbers_are_assigned(auction, cfg):
    make_photo(auction / "photos/5/a.jpg")
    make_photo(auction / "photos/chair/a.jpg")
    write_lots(auction, [good_row("5"), good_row("", photo_folder="chair")])
    report, _ = build(auction, cfg)
    assert report.ok, report.text()
    assert "assigned lot 6" in report.text()
    assert (auction / "output/images/6-1.jpg").exists()


def test_smart_quotes_cleaned(auction, cfg):
    make_photo(auction / "photos/1/a.jpg")
    write_lots(auction, [good_row("1", title="Mom’s 12” “vintage” lamp — nice")])
    build(auction, cfg)
    assert read_csv(auction / "output/af360_lots.csv")[1][1] == 'Mom\'s 12" "vintage" lamp - nice'


def test_bad_image_reported_not_crash(auction, cfg):
    (auction / "photos/1").mkdir(parents=True)
    (auction / "photos/1/broken.jpg").write_bytes(b"not an image")
    write_lots(auction, [good_row("1")])
    report, _ = build(auction, cfg)
    assert not report.ok and "could not read image" in report.text()
    assert not (auction / "output/af360_lots.csv").exists()


def test_rebuild_replaces_old_photos(auction, cfg):
    make_photo(auction / "photos/1/a.jpg")
    make_photo(auction / "photos/1/b.jpg")
    write_lots(auction, [good_row("1")])
    build(auction, cfg)
    (auction / "photos/1/b.jpg").unlink()
    build(auction, cfg)
    assert [p.name for p in (auction / "output/images").iterdir()] == ["1-1.jpg"]


def test_scan_adds_rows_and_keeps_existing(auction, cfg):
    make_photo(auction / "photos/1/a.jpg")
    make_photo(auction / "photos/2/a.jpg")
    make_photo(auction / "photos/10/a.jpg")
    (auction / "photos/empty").mkdir()
    write_lots(auction, [good_row("1", title="Keep me")])
    added = scan_photo_folders(auction, cfg["defaults"])
    assert added == ["2", "10"]
    with open(auction / "lots.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["title"] == "Keep me"
    assert rows[1]["quantity"] == "1"


def test_column_names_come_from_config(auction, cfg):
    cfg["columns"]["title"] = "Title"
    make_photo(auction / "photos/1/a.jpg")
    write_lots(auction, [good_row("1")])
    build(auction, cfg)
    assert read_csv(auction / "output/af360_lots.csv")[0][1] == "Title"


def test_cli_new_check_build(tmp_path, capsys):
    d = tmp_path / "cli"
    assert main(["new", str(d)]) == 0
    assert main(["new", str(d)]) == 1  # never overwrites lots.csv
    make_photo(d / "photos/1/a.jpg")
    assert main(["scan", str(d)]) == 0
    assert main(["check", str(d)]) == 1  # title + seller still blank
    rows = [good_row("1")]
    write_lots(d, rows)
    assert main(["check", str(d)]) == 0
    assert not (d / "output").exists()
    assert main(["build", str(d)]) == 0
    assert (d / "output/report.txt").exists()


@pytest.mark.parametrize("raw,expected", [
    ("", ""), ("5", "5"), ("$1,250.00", "1250"), ("12.50", "12.5"), ("abc", None), ("-3", None),
])
def test_parse_money(raw, expected):
    assert parse_money(raw) == expected
