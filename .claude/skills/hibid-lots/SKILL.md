---
name: hibid-lots
description: Write HiBid lot titles and descriptions from photos into an auction's lots.csv, then build the Auction Flex 360 import package. Use when the user says "list these on HiBid", "do the HiBid lots", "build the AF360 import", or drops photo folders into auctions/<name>/photos/.
---

# HiBid lots via Auction Flex 360

The pipeline: photos in `auctions/<name>/photos/<lot>/` -> `lots.csv` -> `python -m af360 build`
-> `output/af360_lots.csv` + `output/images/<lot>-<n>.jpg` -> user imports into AF360 -> AF360 uploads to HiBid.

## Steps

1. **Find the auction folder.** Ask which one if it's unclear. A new one starts with
   `python -m af360 new auctions/<yyyy-mm-dd-name>`.
2. **Make sure there's a row for every photo folder:** `python -m af360 scan auctions/<name>`.
   Existing rows are never changed.
3. **Look at every photo of every lot** with the Read tool before writing anything for it.
4. **Fill in `lots.csv`** for each row that has a blank title:
   - `title`: short and specific, with the key words a bidder would search for: brand, model,
     what it is, the main detail. No ALL CAPS, no emojis, no "L@@K".
   - `description`: what it is, brand/model/markings you can actually read, material, size
     (only if it's measured or printed), quantity of pieces, and the condition you can see,
     including damage. End with a line saying what was and wasn't tested.
   - `category`: a plain category (Furniture, Tools, Electronics...) unless the user has given
     a category list from their AF360/HiBid account; then use theirs exactly.
   - `quantity`: 1 unless the user says otherwise. A "lot of 5 mugs" sold together is quantity 1.
5. **Leave `seller_code`, `starting_bid`, `reserve` and estimates blank** unless the user gave them.
   Ask for the seller code once per auction (in AF360: Customers > Customer List > Customer Code).
6. **Run `python -m af360 check auctions/<name>`**, fix what you can, and ask the user about the rest.
7. **Run `python -m af360 build auctions/<name>`** and give the user the upload steps from
   `output/report.txt`.

## Rules (no exceptions)

- **Never invent facts.** If you can't read the brand, don't name one. If you can't tell the
  size, leave it out. Never say "works", "tested" or "authentic" unless the user told you so.
  Default to "Untested" / "Sold as-is".
- If a photo is unclear, or a lot's photos look like more than one lot, stop and ask.
- Show any damage you can see. Bidders who get surprised leave bad feedback and open disputes.
- Never commit anything under `auctions/` except `auctions/example/`. This repo is public.
