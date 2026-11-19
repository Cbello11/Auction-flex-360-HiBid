# Auction Flex 360 → HiBid lot builder

Turns a folder of photos plus a simple spreadsheet into the two files **Auction Flex 360 (AF360)**
imports in bulk. AF360 then pushes the auction to **HiBid**, so you never type the lots in by hand.

```
photos + lots.csv  ──►  python -m af360 build  ──►  af360_lots.csv + 1-1.jpg, 1-2.jpg ...
                                                          │
                                       AF360: Import Lots, then Lot Images
                                                          │
                                              AF360: Upload Auction to HiBid
```

## One-time setup

```bash
pip install -r requirements.txt
```

## Every auction

**1. Make the folder**
```bash
python -m af360 new auctions/2026-11-22-estate
```

**2. Add photos.** Each lot gets its own folder, named with its lot number:
```
auctions/2026-11-22-estate/photos/1/   ← every photo of lot 1
auctions/2026-11-22-estate/photos/2/
```
Photos are ordered by file name, so the first file becomes the main HiBid picture.
Any file names work; the tool renames them.

**3. Create a row for each folder**
```bash
python -m af360 scan auctions/2026-11-22-estate
```

**4. Fill in `lots.csv`.** Use Excel/Google Sheets and save it as CSV, or open this repo in Claude
Code and say *"do the HiBid lots for 2026-11-22-estate"*. Claude follows
`.claude/skills/hibid-lots/SKILL.md`: it looks at the photos and writes titles and descriptions
without inventing anything.

| column | required | notes |
|---|---|---|
| lot | yes* | blank = next free number is assigned |
| title | yes | becomes the HiBid lot title ("Lead" in AF360) |
| description | recommended | condition, size, markings, damage |
| quantity | yes | default 1 |
| seller_code | yes | AF360: Customers → Customer List → Customer Code |
| category | no | |
| low_estimate / high_estimate | no | `$` and commas are fine |
| starting_bid / reserve | no | |
| photo_folder | no | folder under `photos/`; default = the lot number |
| notes | no | for you; not exported |

**5. Check, then build**
```bash
python -m af360 check auctions/2026-11-22-estate   # finds problems, writes nothing
python -m af360 build auctions/2026-11-22-estate   # makes the upload package
```
If there's **any** error, nothing is written, so a half-finished file never gets uploaded.

**6. Upload in AF360** (the files are in `auctions/<name>/output/`):
1. **Lots → Import Lots → Choose File →** `af360_lots.csv`. Check the field mapping, then **Import**.
2. **Lot Images →** select every file in `images/` (Ctrl+A) **→ Upload**. Lots must be imported first.
3. Spot-check a few lots, then **Upload Auction to HiBid**.

## What the build does for you
- Renames photos to `<lot>-<n>.jpg`, the pattern AF360 uses to attach them to the right lot
- Turns phone photos upright (phones store rotation separately; some sites ignore it)
- Shrinks anything larger than 2048×1536 (AF360's maximum) and converts it to JPEG
- Replaces curly quotes and long dashes with plain characters
- Catches empty titles, missing seller codes, duplicate lot numbers, bad prices, missing photos,
  and photo folders that no lot uses

## Settings: `config.json`
- **`columns`**: the header names written in the CSV. **These are a best guess until checked
  against your account's template** (AF360: Lots → Import Lots → CSV Template). Change the
  right-hand side to match the template exactly. AF360 also has a mapping screen on import,
  so a mismatch can be fixed there too.
- `images.pad_to_16x9`: AF360 recommends 16:9 landscape photos. Set this to `true` to add white
  bars instead of cropping.
- `rules.max_title_length`: set a number to enforce a title length limit (0 = off).

## Privacy
This repo is **public**. `.gitignore` keeps everything in `auctions/` off GitHub except
`auctions/example/`. Don't change that.

## Tests
```bash
python -m pytest -q
```
