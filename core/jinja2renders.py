from jinja2 import pass_context

from core.configs import logos_dir


def _clean_exif_value(value):
    if value is None:
        return ""
    return str(value).strip()


def _compact_number(value):
    value = _clean_exif_value(value)
    if not value:
        return ""
    value = value.replace(" mm", "").replace("mm", "").strip()
    try:
        number = float(value)
    except ValueError:
        return value
    if number.is_integer():
        return str(int(number))
    return f"{number:.1f}".rstrip("0").rstrip(".")


def format_photo_time(exif):
    exif = exif or {}
    candidates = [
        "DateTimeOriginal",
        "CreateDate",
        "DigitalCreationDateTime",
        "DigitalCreationDate",
        "DateCreated",
        "DateTimeCreated",
        "FileInodeChangeDateTime",
        "FileAccessDateTime",
    ]
    value = next((_clean_exif_value(exif.get(key)) for key in candidates if _clean_exif_value(exif.get(key))), "")
    if not value:
        return "-"

    value = value.replace("T", " ")
    timezone = ""
    if len(value) >= 5 and value[-5:].replace(":", "").lstrip("+-").isdigit() and value[-5] in "+-":
        timezone = value[-5:]
        value = value[:-5].strip()
    elif len(value) >= 6 and value[-6] in "+-" and value[-5:].replace(":", "").isdigit():
        timezone = value[-6:]
        value = value[:-6].strip()

    if len(value) >= 19 and value[4] in ":-" and value[7] in ":-":
        value = f"{value[:4]}.{value[5:7]}.{value[8:10]} {value[11:19]}"

    return f"{value}({timezone})" if timezone else value


def format_focal_length(exif):
    exif = exif or {}
    value = _compact_number(exif.get("FocalLengthIn35mmFormat") or exif.get("FocalLength"))
    return f"{value}mm" if value else "-"


def format_shutter_speed(exif):
    import math

    exif = exif or {}
    value = _clean_exif_value(exif.get("ShutterSpeed"))
    if not value:
        shutter_value = _clean_exif_value(exif.get("ShutterSpeedValue"))
        try:
            exposure = math.pow(2, -float(shutter_value))
            value = f"{exposure:.1f}" if exposure >= 1 else f"1/{round(1 / exposure)}"
        except (ValueError, TypeError, ZeroDivisionError):
            value = shutter_value
    if not value:
        return "-"
    if value.endswith("s"):
        return value
    return f"{value}s"


def format_aperture(exif):
    exif = exif or {}
    value = _compact_number(exif.get("ApertureValue") or exif.get("AperatureValue") or exif.get("FNumber"))
    if not value:
        return "-"
    return value if value.lower().startswith("f/") else f"f/{value}"


def format_iso(exif):
    exif = exif or {}
    value = _compact_number(exif.get("ISO"))
    return f"ISO {value}" if value else "-"


@pass_context
def vw(context, percent):
    exif = context.get('exif', {})
    return int(int(exif.get('ImageWidth', 0)) * percent / 100)


@pass_context
def vh(context, percent):
    exif = context.get('exif', {})
    return int(int(exif.get('ImageHeight', 0)) * percent / 100)


@pass_context
def auto_logo(context, brand: str = None):
    exif = context.get('exif', {})
    brand = (brand or exif.get('Make', 'default')).lower()


    for f in logos_dir.iterdir():
        if f.suffix.lower() in {'.png', '.jpg', '.jpeg'} and f.stem.lower() in brand:
            return str(f.absolute()).replace('\\', '/')
    return None
