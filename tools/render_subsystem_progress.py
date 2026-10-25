"""Render a compact, monochrome subsystem development progress graphic.

The source of the assessment and its scope is docs/subsystem_progress.json.
Category scores describe development evidence, not operational readiness.
"""

from __future__ import annotations

import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "subsystem_progress.json"
OUTPUT = ROOT / "docs" / "서브시스템_개발진행도.png"
WIDTH, HEIGHT = 1920, 1440
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
CATEGORIES = ("architecture", "core", "integration", "field")
SUBSYSTEMS = ("DGS", "ROS", "FDS", "MPS", "WEB", "SIM")

FONT_PAIRS = (
    (Path("C:/Windows/Fonts/malgun.ttf"), Path("C:/Windows/Fonts/malgunbd.ttf")),
    (Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
     Path("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf")),
    (Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
     Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc")),
)
FONTS = next(((regular, bold) for regular, bold in FONT_PAIRS
              if regular.exists() and bold.exists()), None)
if FONTS is None:
    raise RuntimeError("A Korean-capable regular and bold font is required")


def font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS[1 if bold else 0]), size)


def text_fit(draw: ImageDraw.ImageDraw, value: str, width: int,
             start_size: int, minimum_size: int = 20,
             *, bold: bool = False) -> ImageFont.FreeTypeFont:
    if "\n" in value or "\r" in value:
        raise ValueError(f"Expected one line of text: {value!r}")
    for size in range(start_size, minimum_size - 1, -1):
        selected = font(size, bold=bold)
        if draw.textlength(value, font=selected) <= width:
            return selected
    raise ValueError(f"Text does not fit in {width}px: {value!r}")


def integer_percent(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
        raise ValueError(f"{label} must be an integer percentage from 0 to 100")
    return value


def read_progress() -> tuple[list[dict], dict[str, int]]:
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    weights = data["weights"]
    if set(weights) != set(CATEGORIES):
        raise ValueError("Weights must contain exactly the four development categories")
    parsed_weights = {
        key: integer_percent(weights[key], f"weight {key}") for key in CATEGORIES
    }
    if sum(parsed_weights.values()) != 100:
        raise ValueError("Development weights must total 100")

    rows = data["rows"]
    if [row["id"] for row in rows] != list(SUBSYSTEMS):
        raise ValueError("Rows must contain DGS, ROS, FDS, MPS, WEB, SIM in that order")
    for row in rows:
        if set(row["score"]) != set(CATEGORIES):
            raise ValueError(f"{row['id']} has an incomplete score")
        for key in CATEGORIES:
            integer_percent(row["score"][key], f"{row['id']} {key}")
        for key in ("name", "implemented", "remaining"):
            if not isinstance(row[key], str) or not row[key].strip():
                raise ValueError(f"{row['id']} needs nonempty {key}")
    return rows, parsed_weights


def percentage(row: dict, weights: dict[str, int]) -> int:
    weighted = sum(weights[key] * row["score"][key] for key in CATEGORIES)
    units_of_five = (Decimal(weighted) / Decimal(500)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP)
    return int(units_of_five * 5)


def render() -> None:
    rows, weights = read_progress()
    image = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(image)

    label_x = 78
    bar_left, bar_right = 516, 1588
    percent_x = 1625
    note_right = 1844
    first_top, row_height = 35, 226
    for index, row in enumerate(rows):
        top = first_top + index * row_height
        percent = percentage(row, weights)

        draw.text((label_x, top + 83), row["id"],
                  font=font(66, bold=True), fill=BLACK, anchor="lm")
        name = row["name"].strip()
        name_font = text_fit(draw, name, bar_left - label_x - 28, 29, 24)
        draw.text((label_x, top + 142), name,
                  font=name_font, fill=BLACK, anchor="lm")

        bar_top, bar_bottom = top + 48, top + 99
        fill_width = round((bar_right - bar_left) * percent / 100)
        if fill_width:
            draw.rectangle((bar_left, bar_top, bar_left + fill_width,
                            bar_bottom), fill=BLACK)
        draw.rectangle((bar_left, bar_top, bar_right, bar_bottom),
                       outline=BLACK, width=3)
        percent_label = f"약 {percent}%"
        percent_font = text_fit(draw, percent_label, note_right - percent_x,
                                59, 45, bold=True)
        draw.text((percent_x, top + 73), percent_label,
                  font=percent_font, fill=BLACK, anchor="lm")

        for label, key, center_y in (("구현", "implemented", top + 154),
                                     ("남음", "remaining", top + 199)):
            value = f"{label}  {row[key].strip()}"
            selected = text_fit(draw, value, note_right - bar_left,
                                28, 20)
            draw.text((bar_left, center_y), value,
                      font=selected, fill=BLACK, anchor="lm")
        if index < len(rows) - 1:
            draw.line((label_x, top + row_height - 8,
                       note_right, top + row_height - 8),
                      fill=BLACK, width=2)

    if first_top + row_height * len(rows) > HEIGHT:
        raise ValueError("Rows exceed the image height")
    red, green, blue = image.split()
    if (ImageChops.difference(red, green).getbbox()
            or ImageChops.difference(red, blue).getbbox()):
        raise ValueError("The image contains a colored pixel")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUTPUT, format="PNG", optimize=True)
    print(f"{OUTPUT} | {WIDTH}x{HEIGHT} | {OUTPUT.stat().st_size} bytes")


if __name__ == "__main__":
    render()
