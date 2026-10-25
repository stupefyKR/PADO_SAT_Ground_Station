"""Render a high-level PADO-SAT feature status image."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "core_feature_status.json"
OUTPUT = ROOT / "docs" / "핵심기능_개발현황.png"
WIDTH, HEIGHT = 2400, 1280
BLACK, WHITE = (0, 0, 0), (255, 255, 255)
SKY_BLUE = (198, 235, 250)
GREEN = (202, 237, 198)
IDS = ("DGS", "ROS", "FDS", "MPS", "WEB")
STATUS_FILL = {
    "시험 구현": SKY_BLUE,
    "예비 구현": GREEN,
    "일부 구현": GREEN,
    "운용 미완": WHITE,
    "추가 검증": WHITE,
}
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


def fitting_font(draw: ImageDraw.ImageDraw, lines: list[str], width: int,
                 start: int, minimum: int = 24) -> ImageFont.FreeTypeFont:
    for size in range(start, minimum - 1, -1):
        candidate = font(size, bold=True)
        if all(draw.textlength(line, font=candidate) <= width for line in lines):
            return candidate
    raise ValueError(f"Text does not fit in {width}px: {lines!r}")


def load_rows() -> list[dict]:
    rows = json.loads(SOURCE.read_text(encoding="utf-8"))["rows"]
    if [row["id"] for row in rows] != list(IDS):
        raise ValueError("The five components must occur in the expected order")
    for row in rows:
        if len(row["items"]) not in (3, 4):
            raise ValueError(f"{row['id']} must have three or four feature cards")
        if not row["name"].strip():
            raise ValueError(f"{row['id']} needs a name")
        for item in row["items"]:
            if item["status"] not in STATUS_FILL or not 1 <= len(item["lines"]) <= 3:
                raise ValueError(f"Invalid {row['id']} feature card")
            if any(not isinstance(line, str) or not line.strip() or "\n" in line
                   for line in item["lines"]):
                raise ValueError(f"Invalid {row['id']} text line")
        if row["items"][-1]["status"] not in {"운용 미완", "추가 검증"}:
            raise ValueError(f"{row['id']} needs a remaining-work card")
        if len(row["items"]) == 4 and any(
                len(item["lines"]) != 1 for item in row["items"][2:]):
            raise ValueError(f"{row['id']} split cards need one text line each")
    return rows


def draw_card(draw: ImageDraw.ImageDraw, image: Image.Image, item: dict,
              left: int, top: int, right: int, bottom: int,
              *, compact: bool = False) -> None:
    fill = STATUS_FILL[item["status"]]
    draw.rectangle((left, top, right, bottom), fill=fill,
                   outline=BLACK, width=3)
    if compact:
        draw.text((left + 20, top + 6), item["status"],
                  font=font(22), fill=BLACK)
        detail_font = fitting_font(draw, item["lines"], right - left - 42, 31)
        draw.text((left + 20, top + 46), item["lines"][0],
                  font=detail_font, fill=BLACK)
    else:
        draw.text((left + 23, top + 13), item["status"],
                  font=font(26), fill=BLACK)
        draw.line((left + 20, top + 56, right - 20, top + 56),
                  fill=BLACK, width=2)
        detail_font = fitting_font(draw, item["lines"], right - left - 46, 36)
        for line_index, line in enumerate(item["lines"]):
            draw.text((left + 23, top + 74 + line_index * 43), line,
                      font=detail_font, fill=BLACK)
    if image.getpixel((right - 10, bottom - 10)) != fill:
        raise ValueError("Feature card background differs from its status")


def render() -> None:
    rows = load_rows()
    image = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(image)
    cards = ((340, 984), (1000, 1644), (1660, 2344))
    draw.text((340, 38), "구현·시험", font=font(31, bold=True), fill=BLACK)
    draw.text((1660, 38), "남은 연결·검증", font=font(31, bold=True), fill=BLACK)
    draw.line((56, 87, 2344, 87), fill=BLACK, width=3)

    for row_index, row in enumerate(rows):
        top = 101 + row_index * 228
        draw.text((56, top + 47), row["id"], font=font(62, bold=True), fill=BLACK)
        draw.text((58, top + 135), row["name"], font=font(28), fill=BLACK)
        for card_index, item in enumerate(row["items"]):
            left, right = cards[min(card_index, 2)]
            compact = len(row["items"]) == 4 and card_index >= 2
            if compact:
                card_top, card_bottom = ((top + 4, top + 103)
                                         if card_index == 2 else
                                         (top + 110, top + 209))
            else:
                card_top, card_bottom = top + 4, top + 209
            draw_card(draw, image, item, left, card_top, right, card_bottom,
                      compact=compact)
        if top + 209 >= HEIGHT:
            raise ValueError("The content exceeds the image height")

    image.save(OUTPUT, format="PNG", optimize=True)
    print(f"{OUTPUT} | {WIDTH}x{HEIGHT} | {OUTPUT.stat().st_size} bytes")


if __name__ == "__main__":
    render()
