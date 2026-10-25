"""Render four simple black-and-white PADO-SAT TC/TM explanation diagrams.

The images explain data flow and PDU purpose. Exact field layouts remain in
interfaces/pado_packets/draft_v0.json and docs/TC_TM/DESIGN.md.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/TC_TM"
OUT.mkdir(parents=True, exist_ok=True)

FONT_REGULAR = Path("C:/Windows/Fonts/malgun.ttf")
FONT_BOLD = Path("C:/Windows/Fonts/malgunbd.ttf")
if not FONT_REGULAR.exists():
    FONT_REGULAR = Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf")
    FONT_BOLD = Path("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf")

BLACK = "#000000"
WHITE = "#ffffff"
WIDTH = 1920
HEIGHT = 1080
FOOTER = "개발용 패킷 도식 · 운용 규격은 CS/CDHS와 확정 예정"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_BOLD if bold else FONT_REGULAR), size)


def write(draw: ImageDraw.ImageDraw, xy: tuple[int, int], value: str,
          size: int = 28, bold: bool = False) -> None:
    draw.text(xy, value, font=font(size, bold), fill=BLACK)


def centered(draw: ImageDraw.ImageDraw, y: int, value: str,
             left: int, right: int, size: int = 28, bold: bool = False) -> None:
    face = font(size, bold)
    x = left + (right - left - draw.textlength(value, font=face)) / 2
    write(draw, (round(x), y), value, size, bold)


def outline(draw: ImageDraw.ImageDraw, xy: tuple[int, int, int, int],
            width: int = 2) -> None:
    draw.rectangle(xy, fill=WHITE, outline=BLACK, width=width)


def header(draw: ImageDraw.ImageDraw, number: int, title: str,
           subtitle: str) -> None:
    write(draw, (72, 54), f"{number:02d}  {title}", 55, True)
    write(draw, (75, 132), subtitle, 27)
    draw.line((72, 192, 1848, 192), fill=BLACK, width=3)


def footer(draw: ImageDraw.ImageDraw, number: int) -> None:
    draw.line((72, 1008, 1848, 1008), fill=BLACK, width=2)
    write(draw, (75, 1025), FOOTER, 21)
    write(draw, (1750, 1025), f"{number:02d} / 04", 21)


def card(draw: ImageDraw.ImageDraw, xy: tuple[int, int, int, int],
         title: str, detail: str, title_size: int = 32,
         detail_size: int = 25) -> None:
    outline(draw, xy)
    x1, y1, x2, _ = xy
    centered(draw, y1 + 51, title, x1, x2, title_size, True)
    centered(draw, y1 + 111, detail, x1, x2, detail_size)


def arrow(draw: ImageDraw.ImageDraw, x1: int, x2: int, y: int,
          reverse: bool = False) -> None:
    if reverse:
        draw.line((x2, y, x1, y), fill=BLACK, width=4)
        draw.polygon(((x1, y), (x1 + 16, y - 9), (x1 + 16, y + 9)), fill=BLACK)
    else:
        draw.line((x1, y, x2, y), fill=BLACK, width=4)
        draw.polygon(((x2, y), (x2 - 16, y - 9), (x2 - 16, y + 9)), fill=BLACK)


def save(image: Image.Image, filename: str) -> None:
    red, green, blue = image.split()
    if ImageChops.difference(red, green).getbbox() or ImageChops.difference(red, blue).getbbox():
        raise ValueError(f"{filename}: colored pixels are forbidden")
    path = OUT / filename
    image.save(path, format="PNG", optimize=True)
    print(f"{path} | {image.width}x{image.height} | {path.stat().st_size} bytes")


def new_canvas(number: int, title: str, subtitle: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(image)
    header(draw, number, title, subtitle)
    footer(draw, number)
    return image, draw


def overview() -> None:
    image, draw = new_canvas(1, "UHF 통신 경로", "TC 명령과 TM 상태정보가 위성과 지상 사이를 오가는 경로")
    write(draw, (76, 243), "TC 업링크  →", 31, True)
    positions = [
        (72, 330, 444, 580),
        (540, 330, 912, 580),
        (1008, 330, 1380, 580),
        (1476, 330, 1848, 580),
    ]
    contents = [
        ("운용 시스템", "PDU 구성·상태 확인"),
        ("지상 안테나", "UHF 송신·수신"),
        ("위성 AX100", "무선 프레임 처리"),
        ("위성 OBC", "명령 실행·TM 생성"),
    ]
    for position, (title, detail) in zip(positions, contents):
        card(draw, position, title, detail)
    for left, right in zip(positions, positions[1:]):
        arrow(draw, left[2] + 20, right[0] - 20, 455)
    write(draw, (76, 631), "←  TM 다운링크", 31, True)

    card(draw, (72, 730, 924, 947), "TC", "위성으로 보내는 명령", 40, 28)
    card(draw, (956, 730, 1848, 947), "TM", "위성이 보내는 상태와 명령 결과", 40, 28)
    save(image, "01_uhf_frame_overview.png")


def tc() -> None:
    image, draw = new_canvas(2, "TC 구성과 실행", "TC PDU는 수행할 명령과 실행 시점을 전달한다")
    write(draw, (75, 239), "TC PDU", 35, True)
    columns = [
        (72, 314, 640, 572, "명령", "무엇을 할 것인가"),
        (676, 314, 1244, 572, "인자", "대상과 설정값"),
        (1280, 314, 1848, 572, "실행 시각", "언제 수행할 것인가"),
    ]
    for x1, y1, x2, y2, title, detail in columns:
        card(draw, (x1, y1, x2, y2), title, detail, 35, 28)

    write(draw, (75, 637), "실행 방식", 31, True)
    card(draw, (72, 706, 924, 945), "즉시 실행", "수신 후 수행할 명령", 39, 28)
    card(draw, (956, 706, 1848, 945), "예약 실행", "지정한 시각에 수행할 명령", 39, 28)
    save(image, "02_tc_packet_and_commands.png")


def tm() -> None:
    image, draw = new_canvas(3, "TM 데이터와 활용", "위성 상태를 필요한 시스템에서 읽을 수 있도록 분류한다")
    outline(draw, (72, 228, 1848, 323))
    centered(draw, 252, "위성에서 내려오는 TM PDU", 72, 1848, 34, True)

    entries = [
        ("CDHS", "운용 모드·기기 상태"),
        ("EPS", "전력·온도"),
        ("AOCS", "위성 자세"),
        ("GNSS", "위치·속도"),
        ("CS", "무선 링크 상태"),
        ("CREST / SIREN", "탑재체 상태·알림"),
    ]
    x_positions = (72, 676, 1280)
    y_positions = (355, 590)
    for index, (title, detail) in enumerate(entries):
        x = x_positions[index % 3]
        y = y_positions[index // 3]
        card(draw, (x, y, x + 568, y + 204), title, detail, 34, 26)

    card(draw, (72, 806, 924, 976), "ROS", "센서 그래프·경보·자세", 32, 26)
    card(draw, (956, 806, 1848, 976), "FDS", "GNSS 기반 궤도 예측", 32, 26)
    save(image, "03_tm_packet_catalog.png")


def pdu() -> None:
    image, draw = new_canvas(4, "PDU에 담기는 정보", "TC와 TM은 같은 기본 구조를 사용한다")
    write(draw, (75, 239), "PDU 구성", 34, True)
    columns = [
        (72, 308, 640, 530, "헤더", "종류·ID·시각·순번"),
        (676, 308, 1244, 530, "데이터", "명령 또는 위성 상태"),
        (1280, 308, 1848, 530, "오류 검출", "수신 내용 확인"),
    ]
    for x1, y1, x2, y2, title, detail in columns:
        card(draw, (x1, y1, x2, y2), title, detail, 35, 27)

    write(draw, (75, 588), "데이터 예시", 31, True)
    examples = [
        ("TC PDU", "명령 · 인자 · 실행 시각"),
        ("GNSS TM PDU", "위치 · 속도"),
        ("EPS TM PDU", "전압 · 전류 · 온도"),
    ]
    for index, (title, detail) in enumerate(examples):
        y = 654 + index * 108
        outline(draw, (72, y, 1848, y + 91))
        write(draw, (103, y + 23), title, 29, True)
        write(draw, (591, y + 23), detail, 29)
    save(image, "04_byte_field_layout.png")


if __name__ == "__main__":
    overview()
    tc()
    tm()
    pdu()
