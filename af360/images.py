"""Photo processing: fix phone rotation, shrink to AF360's max size, save as JPEG
named <lot>-<n>.jpg (the pattern AF360 uses to attach photos to lots)."""
from pathlib import Path

from PIL import Image, ImageOps

try:  # iPhone .HEIC support is optional: pip install pillow-heif
    import pillow_heif
    pillow_heif.register_heif_opener()
    HEIC_SUPPORTED = True
except ImportError:
    HEIC_SUPPORTED = False

HEIC_EXTENSIONS = {".heic", ".heif"}


class PhotoError(Exception):
    pass


def process_photo(src, dest, cfg_images):
    """Read src, clean it up, write a JPEG to dest. Returns (width, height)."""
    src = Path(src)
    if src.suffix.lower() in HEIC_EXTENSIONS and not HEIC_SUPPORTED:
        raise PhotoError(f"{src.name}: iPhone HEIC photo - run 'pip install pillow-heif' "
                         "or set the iPhone camera to 'Most Compatible' (JPEG)")
    try:
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im)   # phones store rotation separately; apply it
            if im.mode != "RGB":
                # Flatten transparency onto white instead of black.
                background = Image.new("RGB", im.size, (255, 255, 255))
                rgba = im.convert("RGBA")
                background.paste(rgba, mask=rgba.split()[-1])
                im = background

            if cfg_images.get("pad_to_16x9"):
                im = _pad_to_ratio(im, 16, 9, tuple(cfg_images.get("pad_color", (255, 255, 255))))

            max_size = (int(cfg_images["max_width"]), int(cfg_images["max_height"]))
            im.thumbnail(max_size, Image.LANCZOS)   # only ever shrinks, keeps proportions

            dest.parent.mkdir(parents=True, exist_ok=True)
            im.save(dest, "JPEG", quality=int(cfg_images["jpeg_quality"]), optimize=True)
            return im.size
    except PhotoError:
        raise
    except Exception as exc:  # corrupt file, unknown format, etc.
        raise PhotoError(f"{src.name}: could not read image ({exc})") from exc


def _pad_to_ratio(im, rw, rh, color):
    w, h = im.size
    target_w = max(w, round(h * rw / rh))
    target_h = max(h, round(w * rh / rw))
    if (target_w, target_h) == (w, h):
        return im
    canvas = Image.new("RGB", (target_w, target_h), color)
    canvas.paste(im, ((target_w - w) // 2, (target_h - h) // 2))
    return canvas
