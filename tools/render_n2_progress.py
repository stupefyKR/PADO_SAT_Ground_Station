"""Render the evidence-based N2 interface progress chart.

The four segments are verification stages, not equal units of work.  The
source of the per-interface assessment is docs/N2_ICD/progress.json.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "interfaces" / "n2_chart.json"
PROGRESS = ROOT / "docs" / "N2_ICD" / "progress.json"
OUTPUT = ROOT / "docs" / "N2_ICD" / "N2_개발진행도.png"

WIDTH, HEIGHT = 1920, 1380
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)

FONT_PAIRS = [
    (Path("C:/Windows/Fonts/malgun.ttf"), Path("C:/Windows/Fonts/malgunbd.ttf")),
    (Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
     Path("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf")),
    (Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
     Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc")),
]
FONTS = next(((regular, bold) for regular, bold in FONT_PAIRS
              if regular.exists() and bold.exists()), None)
if FONTS is None:
    raise RuntimeError("A Korean-capable regular and bold font is required")


def font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS[1 if bold else 0]), size)


def fit_font(draw: ImageDraw.ImageDraw, value: str, width: int,
             size: int, minimum: int = 17, *, bold: bool = False
             ) -> ImageFont.FreeTypeFont:
    while size >= minimum:
        result = font(size, bold=bold)
        if draw.textlength(value, font=result) <= width:
            return result
        size -= 1
    raise ValueError(f"Text cannot fit in {width}px: {value!r}")


def wrap(draw: ImageDraw.ImageDraw, value: str,
         selected_font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    """Break labels at spaces, falling back to character breaks if needed."""
    parts = value.split()
    if not parts:
        return [""]
    lines: list[str] = []
    current = ""
    for part in parts:
        candidate = f"{current} {part}" if current else part
        if draw.textlength(candidate, font=selected_font) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        for character in part:
            candidate = current + character
            if current and draw.textlength(candidate, font=selected_font) > width:
                lines.append(current)
                current = character
            else:
                current = candidate
    if current:
        lines.append(current)
    return lines


def center_label(draw: ImageDraw.ImageDraw, value: str,
                 box: tuple[int, int, int, int], *, size: int = 22,
                 minimum: int = 17, bold: bool = False,
                 maximum_lines: int = 2) -> None:
    left, top, right, bottom = box
    for candidate_size in range(size, minimum - 1, -1):
        selected = font(candidate_size, bold=bold)
        lines = wrap(draw, value, selected, right - left - 12)
        line_height = candidate_size + 6
        if len(lines) <= maximum_lines and len(lines) * line_height <= bottom - top - 6:
            first_y = (top + bottom) / 2 - (len(lines) - 1) * line_height / 2
            for index, line in enumerate(lines):
                draw.text(((left + right) / 2, first_y + index * line_height),
                          line, font=selected, fill=BLACK, anchor="mm")
            return
    raise ValueError(f"Label cannot fit in {box}: {value!r}")


def segment(image: Image.Image, box: tuple[int, int, int, int],
            state: str) -> None:
    left, top, right, bottom = box
    draw = ImageDraw.Draw(image)
    if state == "full":
        draw.rectangle(box, fill=BLACK)
    elif state == "none":
        draw.rectangle(box, outline=BLACK, fill=WHITE, width=2)
    elif state == "partial":
        width, height = right - left + 1, bottom - top + 1
        tile = Image.new("RGB", (width, height), WHITE)
        hatch = ImageDraw.Draw(tile)
        for x in range(-height, width + height, 10):
            hatch.line((x, height - 1, x + height - 1, 0),
                       fill=BLACK, width=2)
        hatch.rectangle((0, 0, width - 1, height - 1), outline=BLACK, width=2)
        image.paste(tile, (left, top))
    else:
        raise ValueError(f"Unknown state: {state}")


def read_progress() -> dict:
    progress = json.loads(PROGRESS.read_text(encoding="utf-8"))
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    expected = [entry["id"] for entry in source["approved_flows"]]
    rows = progress["rows"]
    actual = [row["id"] for row in rows]
    if len(progress["stages"]) != 4:
        raise ValueError("Exactly four stage labels are required")
    if actual != expected or len(set(actual)) != len(actual):
        raise ValueError("Progress rows must cover the N2 interface IDs once in source order")
    for row in rows:
        if len(row["states"]) != 4 or set(row["states"]) - {"full", "partial", "none"}:
            raise ValueError(f"Invalid four-stage state for {row['id']}")
        if not all(isinstance(row[key], str) and row[key].strip()
                   for key in ("label", "note")):
            raise ValueError(f"A label and note are required for {row['id']}")
    return progress


def render() -> None:
    progress = read_progress()
    image = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(image)

    left, right = 72, 1848
    label_left, label_right = 170, 608
    bar_left, bar_right = 628, 1420
    note_left, note_right = 1448, right
    cell_width = (bar_right - bar_left) // 4

    # The legend remains outside the bars so every glyph is black on white.
    segment(image, (76, 33, 117, 55), "full")
    draw.text((130, 28), "확인", font=font(21), fill=BLACK)
    segment(image, (247, 33, 288, 55), "partial")
    draw.text((302, 28), "부분 확인", font=font(21), fill=BLACK)
    segment(image, (455, 33, 496, 55), "none")
    draw.text((509, 28), "미확인 · 미연결", font=font(21), fill=BLACK)

    header_top, row_start = 76, 151
    draw.line((left, row_start, right, row_start), fill=BLACK, width=2)
    center_label(draw, "번호", (left, header_top, label_left - 4, row_start),
                 size=23, bold=True)
    center_label(draw, "자료 경로", (label_left, header_top, label_right, row_start),
                 size=23, bold=True)
    for index, stage in enumerate(progress["stages"]):
        x0 = bar_left + index * cell_width
        center_label(draw, stage, (x0, header_top, x0 + cell_width, row_start),
                     size=22, bold=True)
    center_label(draw, "현재 범위", (note_left, header_top, note_right, row_start),
                 size=23, bold=True)

    row_height = 57
    for index, row in enumerate(progress["rows"]):
        top = row_start + index * row_height
        bottom = top + row_height
        y_center = (top + bottom) / 2
        draw.text((left + 11, y_center), row["id"],
                  font=font(24, bold=True), fill=BLACK, anchor="lm")
        route_font = fit_font(draw, row["label"], label_right - label_left - 18,
                              22, minimum=17)
        draw.text((label_left, y_center), row["label"],
                  font=route_font, fill=BLACK, anchor="lm")
        for stage_index, state in enumerate(row["states"]):
            x0 = bar_left + stage_index * cell_width
            x1 = bar_left + (stage_index + 1) * cell_width - 1
            segment(image, (x0, top + 16, x1, bottom - 16), state)
        center_label(draw, row["note"],
                     (note_left + 3, top + 3, note_right - 3, bottom - 3),
                     size=20, minimum=16, maximum_lines=2)
        draw.line((left, bottom, right, bottom), fill=BLACK, width=1)

    if row_start + len(progress["rows"]) * row_height >= HEIGHT:
        raise ValueError("Rows do not fit within the image")

    red, green, blue = image.split()
    if (ImageChops.difference(red, green).getbbox()
            or ImageChops.difference(red, blue).getbbox()):
        raise ValueError("The chart contains a colored pixel")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUTPUT, format="PNG", optimize=True)
    print(f"{OUTPUT} | {WIDTH}x{HEIGHT} | {OUTPUT.stat().st_size} bytes")


if __name__ == "__main__":
    render()
