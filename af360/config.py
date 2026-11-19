"""Loads config.json and fills in anything missing with safe defaults."""
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.json"

# Our internal field names, in the order they appear in the exported CSV.
FIELDS = [
    "lot", "title", "description", "quantity", "seller_code", "category",
    "low_estimate", "high_estimate", "starting_bid", "reserve",
]

_DEFAULTS = {
    "columns": {f: f for f in FIELDS},
    "defaults": {"quantity": "1"},
    "images": {
        "max_width": 2048, "max_height": 1536, "jpeg_quality": 90,
        "pad_to_16x9": False, "pad_color": [255, 255, 255],
    },
    "rules": {"max_title_length": 0, "require_photos": True, "plain_ascii_text": True},
}


def load_config(path=None):
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    user = {}
    if path.exists():
        with open(path, encoding="utf-8") as fh:
            user = json.load(fh)
    cfg = {}
    for section, defaults in _DEFAULTS.items():
        merged = dict(defaults)
        merged.update(user.get(section, {}))
        cfg[section] = merged
    return cfg
