"""Render presentation diagrams for the N2 data-flow interfaces.

The diagrams show data direction, not network connection initiators or the
current deployment state.  The complete machine-readable contract remains in
interfaces/n2_chart.json.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "N2_ICD"
SOURCE = ROOT / "interfaces" / "n2_chart.json"
WIDTH, HEIGHT = 1920, 1080
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)

FONT_PAIRS = [
    (Path("C:/Windows/Fonts/malgun.ttf"), Path("C:/Windows/Fonts/malgunbd.ttf")),
    (Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
     Path("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf")),
    (Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
     Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc")),
]
FONT_REGULAR, FONT_BOLD = next(
    ((regular, bold) for regular, bold in FONT_PAIRS if regular.exists() and bold.exists()),
    (None, None),
)
if FONT_REGULAR is None:
    raise RuntimeError("A Korean-capable regular and bold font is required")


def face(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_BOLD if bold else FONT_REGULAR), size)


def centered(draw: ImageDraw.ImageDraw, text: str, box: tuple[int, int, int, int],
             size: int, *, bold: bool = False) -> None:
    left, top, right, bottom = box
    font = face(size, bold=bold)
    while draw.textlength(text, font=font) > right - left - 24 and size > 19:
        size -= 1
        font = face(size, bold=bold)
    draw.text(((left + right) / 2, (top + bottom) / 2), text,
              font=font, fill=BLACK, anchor="mm")


def left_text(draw: ImageDraw.ImageDraw, text: str, left: int, top: int,
              right: int, size: int, *, bold: bool = False) -> None:
    font = face(size, bold=bold)
    while draw.textlength(text, font=font) > right - left and size > 19:
        size -= 1
        font = face(size, bold=bold)
    if draw.textlength(text, font=font) > right - left:
        raise ValueError(f"Text does not fit: {text}")
    draw.text((left, top), text, font=font, fill=BLACK)


def canvas(title: str, subtitle: str, number: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(image)
    left_text(draw, title, 72, 52, 1848, 54, bold=True)
    left_text(draw, subtitle, 75, 131, 1848, 27)
    draw.line((72, 193, 1848, 193), fill=BLACK, width=3)
    draw.line((72, 1002, 1848, 1002), fill=BLACK, width=2)
    left_text(draw, "PADO-SAT  ·  N2 자료 흐름", 75, 1021, 1300, 20)
    left_text(draw, f"{number:02d} / 02", 1746, 1021, 1848, 20)
    return image, draw


def outlined_card(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int],
                  title: str, detail: str) -> None:
    left, top, right, bottom = box
    draw.rectangle(box, outline=BLACK, fill=WHITE, width=3)
    centered(draw, title, (left + 12, top + 33, right - 12, top + 91), 35, bold=True)
    centered(draw, detail, (left + 12, top + 105, right - 12, bottom - 20), 25)


def arrow(draw: ImageDraw.ImageDraw, x1: int, x2: int, y: int,
          *, both: bool = False) -> None:
    draw.line((x1, y, x2, y), fill=BLACK, width=4)
    draw.polygon(((x2, y), (x2 - 19, y - 11), (x2 - 19, y + 11)), fill=BLACK)
    if both:
        draw.polygon(((x1, y), (x1 + 19, y - 11), (x1 + 19, y + 11)), fill=BLACK)


def overview() -> None:
    image, draw = canvas(
        "N2 인터페이스 전체 구조",
        "외부 기관과의 자료 교환은 DGS가 담당한다.",
        1,
    )
    columns = ((72, 468), (614, 1106), (1252, 1848))
    for (left, right), label in zip(columns,
                                    ("외부 기관", "DGS  ·  외부 접점", "내부 시스템")):
        centered(draw, label, (left, 217, right, 261), 29, bold=True)

    rows = [
        (("지상 안테나", "연세대 UHF · 항우연 S-band"),
         ("DGS-GS", "TM · TC · 지향 · 원시데이터"),
         ("ROS · FDS", "ROS: TM·TC  /  FDS: 패스·지향"), True, True),
        (("궤도 자료원", "Space-Track · 발사기관"),
         ("DGS-NET", "TLE · 상태벡터 수집"),
         ("FDS", "궤도 예측 · 패스 산출"), False, False),
        (("외부 데이터 교환", "SDC"),
         ("DGS-Archive", "자료 교환 · 보관"),
         ("ROS · FDS · MPS", "상태 · 궤도력 · 임무 요청"), True, True),
    ]
    for top, row in zip((283, 500, 717), rows):
        left_content, middle_content, right_content, left_both, right_both = row
        bottom = top + 174
        for (left, right), (title, detail) in zip(
                columns, (left_content, middle_content, right_content)):
            outlined_card(draw, (left, top, right, bottom), title, detail)
        mid = (top + bottom) // 2
        arrow(draw, columns[0][1] + 19, columns[1][0] - 19, mid,
              both=left_both)
        arrow(draw, columns[1][1] + 19, columns[2][0] - 19, mid,
              both=right_both)

    save(image, "N2_전체구조.png")


GROUPS = [
    (("N01", "N02"), "지상 안테나 ↔ DGS-GS",
     "TM · TC · 안테나 지향 · 원시데이터"),
    (("N03", "N04", "N05"), "궤도 자료원 → DGS-NET → FDS", "TLE · 상태벡터"),
    (("N06", "N07"), "DGS-GS ↔ ROS", "TM · TC"),
    (("N08",), "FDS → DGS-GS", "패스 요청 · 안테나 지향"),
    (("N09",), "DGS-GS → DGS-Archive", "CREST · SIREN 원시데이터"),
    (("N10", "N11", "N12"), "ROS ↔ MPS  ·  ROS → FDS", "ACK · 상태 · 일정 · GNSS"),
    (("N13",), "FDS → MPS", "컨택 일정 · 궤도 이벤트"),
    (("N14", "N15", "N16", "N17", "N18"),
     "ROS·FDS → DGS-Archive ↔ SDC\nDGS-Archive → MPS",
     "상태 · 궤도력 · 임무 요청"),
    (("N19", "N20", "N21"), "ROS·MPS·FDS → DGS-Archive", "백업"),
]


def validate_source() -> None:
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    actual = [flow["id"] for flow in source["approved_flows"]]
    grouped = [item for ids, _, _ in GROUPS for item in ids]
    expected = [f"N{number:02d}" for number in range(1, 22)]
    if actual != expected or grouped != expected:
        raise ValueError("The diagram groups must cover N01–N21 exactly once in source order")


def display_ids(ids: tuple[str, ...]) -> str:
    return ids[0] if len(ids) == 1 else f"{ids[0]}–{ids[-1][1:]}"


def flow_table() -> None:
    image, draw = canvas(
        "N2 자료 흐름",
        "21개 자료 흐름을 이동 경로별로 묶어 표시했다.",
        2,
    )
    x0, x1, x2, x3 = 72, 276, 1180, 1848
    top, header_bottom, bottom = 253, 315, 945
    row_height = (bottom - header_bottom) // len(GROUPS)
    draw.rectangle((x0, top, x3, bottom), outline=BLACK, width=3)
    for x in (x1, x2):
        draw.line((x, top, x, bottom), fill=BLACK, width=2)
    draw.line((x0, header_bottom, x3, header_bottom), fill=BLACK, width=2)
    left_text(draw, "N2", x0 + 24, top + 13, x1 - 10, 27, bold=True)
    left_text(draw, "자료 경로", x1 + 25, top + 13, x2 - 10, 27, bold=True)
    left_text(draw, "주요 자료", x2 + 25, top + 13, x3 - 10, 27, bold=True)

    for index, (ids, route, data) in enumerate(GROUPS):
        row_top = header_bottom + index * row_height
        row_bottom = bottom if index == len(GROUPS) - 1 else row_top + row_height
        if index:
            draw.line((x0, row_top, x3, row_top), fill=BLACK, width=2)
        centered(draw, display_ids(ids), (x0 + 14, row_top, x1 - 14, row_bottom),
                 29, bold=True)
        route_lines = route.split("\n")
        if len(route_lines) == 1:
            centered(draw, route, (x1 + 24, row_top, x2 - 24, row_bottom), 28)
        else:
            for line_index, line in enumerate(route_lines):
                centered(draw, line,
                         (x1 + 24, row_top + 5 + line_index * 31,
                          x2 - 24, row_top + 38 + line_index * 31), 24)
        centered(draw, data, (x2 + 22, row_top, x3 - 22, row_bottom), 26)
    save(image, "N2_자료흐름.png")


def save(image: Image.Image, name: str) -> None:
    red, green, blue = image.split()
    if (ImageChops.difference(red, green).getbbox()
            or ImageChops.difference(red, blue).getbbox()):
        raise ValueError(f"{name}: a colored pixel was found")
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    image.save(path, format="PNG", optimize=True)
    print(f"{path} | {image.width}x{image.height} | {path.stat().st_size} bytes")


if __name__ == "__main__":
    validate_source()
    overview()
    flow_table()
