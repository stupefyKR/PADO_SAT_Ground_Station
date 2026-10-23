"""Render review-ready PNG diagrams for the proposed PADO-SAT UHF TC/TM ICD.

Uses only the checked-in draft catalog and fonts available on the local host.
The diagrams are a human-readable summary; draft_v0.json remains authoritative
for the exact proposed field order and codec behavior.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
CATALOG = json.loads((ROOT / "interfaces/pado_packets/draft_v0.json").read_text(encoding="utf-8"))
OUT = ROOT / "docs/TC_TM"
OUT.mkdir(parents=True, exist_ok=True)

FONT_REGULAR = Path("C:/Windows/Fonts/malgun.ttf")
FONT_BOLD = Path("C:/Windows/Fonts/malgunbd.ttf")
if not FONT_REGULAR.exists():
    FONT_REGULAR = Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf")
    FONT_BOLD = Path("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf")

BG = "#091320"
CARD = "#132437"
CARD_2 = "#172c42"
LINE = "#2d455a"
WHITE = "#f3f7fa"
MUTED = "#a9b9c6"
CYAN = "#5bdbdf"
BLUE = "#70a7ff"
AMBER = "#ffbd69"
GREEN = "#7bd9ac"
RED = "#ff8585"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = FONT_BOLD if bold else FONT_REGULAR
    return ImageFont.truetype(str(path), size)


def text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], value: str,
         size: int = 26, color: str = WHITE, bold: bool = False) -> None:
    draw.text(xy, value, font=font(size, bold), fill=color)


def box(draw: ImageDraw.ImageDraw, xy: tuple[int, int, int, int],
        fill: str = CARD, outline: str | None = None, radius: int = 20) -> None:
    draw.rounded_rectangle(xy, radius=radius, fill=fill,
                           outline=outline or LINE, width=2)


def badge(draw: ImageDraw.ImageDraw, xy: tuple[int, int], label: str,
          fill: str, text_color: str = BG) -> None:
    f = font(21, True)
    width = int(draw.textlength(label, font=f)) + 30
    box(draw, (xy[0], xy[1], xy[0] + width, xy[1] + 39), fill=fill,
        outline=fill, radius=12)
    draw.text((xy[0] + 15, xy[1] + 6), label, font=f, fill=text_color)


def header(draw: ImageDraw.ImageDraw, title: str, subtitle: str,
           page: str, width: int) -> None:
    text(draw, (72, 59), title, 49, WHITE, True)
    text(draw, (74, 127), subtitle, 25, MUTED)
    badge(draw, (width - 304, 64), "DRAFT / 비운용", AMBER)
    text(draw, (width - 142, 128), page, 21, MUTED)
    draw.line((72, 188, width - 72, 188), fill=LINE, width=2)


def footer(draw: ImageDraw.ImageDraw, width: int, y: int, source: str) -> None:
    draw.line((72, y, width - 72, y), fill=LINE, width=2)
    text(draw, (74, y + 17), source, 20, MUTED)
    text(draw, (width - 465, y + 17), "PADO-SAT · UHF 패킷 설계 검토용", 20, MUTED)


def arrow(draw: ImageDraw.ImageDraw, x1: int, y: int, x2: int,
          color: str = CYAN) -> None:
    draw.line((x1, y, x2, y), fill=color, width=5)
    draw.polygon([(x2, y), (x2 - 16, y - 9), (x2 - 16, y + 9)], fill=color)


def save(image: Image.Image, name: str) -> None:
    path = OUT / name
    image.save(path, format="PNG", optimize=True)
    print(f"{path} | {image.width}x{image.height} | {path.stat().st_size} bytes")


def overview() -> None:
    w, h = 1920, 1100
    im = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(im)
    header(d, "01  UHF 프레임과 응용 패킷의 경계",
           "PDR에 기재된 무선 링크와 이번에 정의한 지상국 패킷 초안을 분리해서 표시", "01 / 04", w)

    box(d, (72, 222, 924, 850))
    badge(d, (104, 253), "PDR 명시", CYAN)
    text(d, (104, 313), "UHF RF / AX100 경로", 34, WHITE, True)
    text(d, (104, 363), "435–438 MHz  ·  9.6 kbps  ·  GMSK", 26, MUTED)
    for y, title, detail in [
        (424, "1  지상국 ↔ AX100", "TM · TC · Beacon (UHF)"),
        (527, "2  AX100 Mode 5", "ASM + GOLAY 길이 / RS FEC / CCSDS 랜덤화"),
        (630, "3  OBC ↔ AX100", "CSP over CAN  ·  내부 링크 1 Mbps"),
    ]:
        box(d, (105, y, 888, y + 82), CARD_2, radius=13)
        text(d, (129, y + 12), title, 26, CYAN, True)
        text(d, (129, y + 44), detail, 22, WHITE)
    text(d, (108, 752), "PDR 비콘 RF 프레임", 23, MUTED, True)
    sizes = [("ASM", 4, BLUE), ("GOLAY", 3, CYAN), ("Initial payload", 190, GREEN),
             ("CRC", 4, AMBER), ("RS", 32, BLUE)]
    x = 108
    widths = [97, 116, 298, 98, 107]
    for (label, size, color), width in zip(sizes, widths):
        box(d, (x, 786, x + width - 5, 823), color, color, 6)
        text(d, (x + 8, 790), f"{label} {size}B", 17, BG, True)
        x += width

    box(d, (956, 222, 1848, 850))
    badge(d, (989, 253), "지상국 제안", AMBER)
    text(d, (989, 313), "UHF 응용 PDU · draft v0", 34, WHITE, True)
    text(d, (989, 363), "CSP / AX100에 전달하기 전 최대 200 B", 26, MUTED)
    for y, label, detail, color in [
        (424, "Header  20 B", "PS · 버전 · 종류 · ID · 순번 · UTC · 길이 · 플래그", BLUE),
        (527, "Payload  0–176 B", "TC 실행시각+인자  /  TM 센서값·상태값", GREEN),
        (630, "CRC-32  4 B", "응용 PDU 헤더+본문의 오류 검출", AMBER),
    ]:
        box(d, (990, y, 1811, y + 82), CARD_2, radius=13)
        box(d, (1003, y + 13, 1012, y + 69), color, color, 4)
        text(d, (1036, y + 11), label, 27, color, True)
        text(d, (1036, y + 46), detail, 20, WHITE)
    box(d, (990, 756, 1811, 824), "#203545", "#456279", 12)
    text(d, (1014, 774), "실제 RF 프레임·CSP 헤더와 동일한 구조라는 뜻이 아님", 23, WHITE)

    box(d, (72, 880, 1848, 1000), "#332c29", "#795748", 16)
    text(d, (103, 905), "검토 필요", 25, AMBER, True)
    text(d, (242, 905), "PDR의 비콘 payload 182–196 B와 200 B CSP 패킷 제한은 어느 경계의 길이인지 확인 필요", 24, WHITE)
    text(d, (103, 948), "제안 PDU의 CRC-32는 인증이 아님 · TC 인증·재전송·순번 규칙은 CS/CDHS ICD 확정 전", 22, MUTED)
    footer(d, w, 1022, "근거: PDR 발표자료 pp.65–69 · 별첨 pp.27–30 · 제안서 pp.181–184, 194–197")
    save(im, "01_uhf_frame_overview.png")


def tc() -> None:
    w, h = 1920, 1150
    im = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(im)
    header(d, "02  TC 패킷 구조와 명령 카탈로그",
           "Immediate / Scheduled 명령을 같은 응용 PDU로 표현 · 실제 비행 송출은 차단", "02 / 04", w)

    text(d, (75, 220), "제안 PDU 바이트 배치", 30, WHITE, True)
    segments = [
        (72, 530, "20 B HEADER", BLUE, "offset 0–19"),
        (540, 792, "8 B 실행시각", CYAN, "TC payload 앞부분"),
        (802, 1575, "명령 인자 0–168 B", GREEN, "카탈로그 필드 순서"),
        (1585, 1848, "4 B CRC-32", AMBER, "마지막 4 B"),
    ]
    for x1, x2, title, color, detail in segments:
        box(d, (x1, 273, x2, 371), color, color, 15)
        text(d, (x1 + 18, 289), title, 25, BG, True)
        text(d, (x1 + 18, 328), detail, 19, BG)
    text(d, (75, 390), "전체 ≤ 200 B · Big Endian · 헤더 flag bit7은 DRAFT 필수", 25, MUTED)

    box(d, (72, 445, 924, 686))
    badge(d, (102, 472), "IMMEDIATE", BLUE)
    text(d, (104, 528), "execute_at_utc_ms = 0", 31, WHITE, True)
    text(d, (104, 579), "발행 시각(issue UTC) 기준으로 즉시 명령", 25, MUTED)
    text(d, (104, 622), "헤더 flag bit0 = 0", 23, BLUE)
    box(d, (956, 445, 1848, 686))
    badge(d, (988, 472), "SCHEDULED", CYAN)
    text(d, (990, 528), "execute_at_utc_ms > issue UTC", 31, WHITE, True)
    text(d, (990, 579), "예약 시각은 패킷 본문에 명시", 25, MUTED)
    text(d, (990, 622), "헤더 flag bit0 = 1", 23, CYAN)

    box(d, (72, 714, 621, 986))
    text(d, (102, 742), "명령 함수 58개", 31, WHITE, True)
    text(d, (102, 797), "36개", 58, GREEN, True)
    text(d, (274, 820), "초안 인자 배치 정의", 23, MUTED)
    text(d, (102, 874), "22개", 58, AMBER, True)
    text(d, (274, 898), "CDR 인자 미정 · 조립 거부", 23, MUTED)

    box(d, (648, 714, 1848, 986))
    text(d, (679, 742), "대표 후보 명령 ID", 31, WHITE, True)
    rows = [
        ("0x1001", "mm_set_mode", "CDHS 모드 전환"),
        ("0x1010", "hk_get", "HK 요청"),
        ("0x1031", "eps_ch_off", "전원 채널 차단"),
        ("0x1052", "aocs_set_tle", "TLE 갱신 · 69자 × 2"),
    ]
    for i, (code, name, purpose) in enumerate(rows):
        y = 793 + 43 * i
        text(d, (681, y), code, 23, CYAN, True)
        text(d, (823, y), name, 23, WHITE, True)
        text(d, (1200, y), purpose, 22, MUTED)

    box(d, (72, 1009, 1848, 1072), "#332c29", "#795748", 13)
    text(d, (100, 1025), "주의: 후보 ID와 CRC는 자체 제안 · 위성 인증/인가 방식, ACK 코드, 재전송 정책은 미확정", 23, WHITE)
    footer(d, w, 1086, "원본: PDR 발표자료 p.73 FSW 함수 목록 · 별첨 p.28 CDHS00800 인증 요구")
    save(im, "02_tc_packet_and_commands.png")


TM_SUMMARY = {
    "BEACON_CORE": "모드 · 카운터 · FDIR 요약",
    "EPS_P60": "배터리 모드 · 전압/전류/온도",
    "EPS_PDU1": "전원 채널 · 래치업 · 버스 전압",
    "EPS_PDU2": "전원 채널 · 래치업 · 버스 전압",
    "EPS_ACU": "태양전지 6채널 입력 전압/전류",
    "EPS_BPX": "배터리 전압 · 4채널 온도",
    "AOCS_STATE": "쿼터니안 · 3축 각속도",
    "GNSS_PVT": "ECEF 위치/속도 각 3축",
    "OBC_HEALTH": "부팅 · WDT · CPU/RAM/NOR",
    "AX100_LINK": "PA 온도 · RSSI · 송수신 수",
    "SIREN_ALERT": "위험/이상 점수 · 오류 코드",
    "COMMAND_RESULT": "TC ID/순번 · 실행 결과/시각",
    "FDIR_EVENT": "고장 레벨 · 원인 · 복구 상태",
    "CREST_HEALTH": "전압/전류/온도 · UART 상태",
    "SIREN_HEALTH": "하드웨어 · 백업모드 · 오류",
    "FULL_HK_CHUNK": "160 B 데이터 · 레코드 CRC",
}


def tm() -> None:
    w, h = 1920, 1280
    im = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(im)
    header(d, "03  TM 패킷 카탈로그와 데이터 활용",
           "16종 후보 패킷 · 같은 20 B 헤더 + 가변 본문 + 4 B CRC-32 · UHF 응용 PDU", "03 / 04", w)
    text(d, (74, 217), "모든 ID·필드 인코딩은 제안 초안이며 실제 센서 캘리브레이션/한계값은 CDR 검토 대상", 25, MUTED)

    entries = CATALOG["telemetry"]
    assert len(entries) == 16 and set(TM_SUMMARY) == {entry["name"] for entry in entries}
    x0, y0 = 72, 279
    card_w, card_h, gap_x, gap_y = 422, 176, 21, 19
    colors = {"CDHS": BLUE, "EPS": GREEN, "AOCS": CYAN,
              "CS": AMBER, "SIREN": "#c6a0ef", "CREST": "#ef9fac"}
    for i, entry in enumerate(entries):
        col, row = i % 4, i // 4
        x = x0 + col * (card_w + gap_x)
        y = y0 + row * (card_h + gap_y)
        box(d, (x, y, x + card_w, y + card_h), CARD)
        color = colors[entry["group"]]
        box(d, (x + 16, y + 18, x + 22, y + card_h - 18), color, color, 3)
        text(d, (x + 42, y + 19), f"0x{entry['id']:04X}", 21, color, True)
        text(d, (x + card_w - 116, y + 19), entry["group"], 19, MUTED, True)
        name_size = 27 if len(entry["name"]) < 16 else 24
        text(d, (x + 42, y + 57), entry["name"], name_size, WHITE, True)
        text(d, (x + 42, y + 106), TM_SUMMARY[entry["name"]], 21, MUTED)
        if entry["name"] == "GNSS_PVT":
            text(d, (x + 42, y + 140), "→ FDS N11 정규화 입력", 18, CYAN)
        elif entry["name"] == "AOCS_STATE":
            text(d, (x + 42, y + 140), "→ ROS 자세 뷰", 18, CYAN)
        elif entry["name"] == "FULL_HK_CHUNK":
            text(d, (x + 42, y + 140), "2034 B 예시 = 13 패킷", 18, CYAN)

    box(d, (72, 1084, 1848, 1177), "#203545", "#456279", 15)
    text(d, (101, 1101), "GNSS_PVT", 23, CYAN, True)
    arrow(d, 275, 1121, 360)
    text(d, (384, 1101), "ECEF 위치·속도", 23, WHITE)
    arrow(d, 597, 1121, 677)
    text(d, (702, 1101), "FDS N11", 23, WHITE, True)
    text(d, (101, 1140), "ADCS 쿼터니안은 별도 AOCS_STATE · FULL_HK_CHUNK는 내용 해석 없이 완전성만 검증", 20, MUTED)
    footer(d, w, 1202, "근거: PDR 발표자료 pp.65–67 · 별첨 p.47 HK 항목 · 제안서 pp.181–184")
    save(im, "03_tm_packet_catalog.png")


def field_layout() -> None:
    w, h = 1920, 1190
    im = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(im)
    header(d, "04  응용 PDU 필드 정의 · 바이트 단위",
           "패킷 바이트 순서는 Big Endian · 표의 ID와 데이터 타입은 지상국 초안", "04 / 04", w)

    box(d, (72, 226, 911, 1036))
    badge(d, (102, 251), "공통 헤더", BLUE)
    text(d, (103, 310), "모든 TC / TM에 동일하게 적용", 29, WHITE, True)
    text(d, (108, 373), "OFFSET", 20, MUTED, True)
    text(d, (256, 373), "BYTES", 20, MUTED, True)
    text(d, (385, 373), "필드", 20, MUTED, True)
    fields = [
        ("0–1", "2", "Magic 'PS'"),
        ("2", "1", "버전 0"),
        ("3", "1", "종류: 1=TC, 2=TM"),
        ("4–5", "2", "명령 / TM 후보 ID"),
        ("6–7", "2", "시퀀스 번호"),
        ("8–15", "8", "발행 / 샘플 UTC ms"),
        ("16–17", "2", "본문 길이"),
        ("18", "1", "flags: bit7 draft, bit0 예약"),
        ("19", "1", "예약 필드 = 0"),
        ("20…", "0–176", "가변 본문"),
        ("끝-4", "4", "CRC-32"),
    ]
    for i, (offset, size, label) in enumerate(fields):
        y = 410 + i * 51
        if i % 2 == 0:
            box(d, (102, y - 4, 877, y + 42), CARD_2, CARD_2, 5)
        text(d, (110, y + 2), offset, 23, CYAN, True)
        text(d, (260, y + 2), size, 23, WHITE, True)
        text(d, (388, y + 2), label, 22, WHITE)
    text(d, (105, 990), "TC=발행 시각  ·  TM=측정 시각", 21, MUTED)

    box(d, (941, 226, 1848, 487))
    badge(d, (970, 251), "TC 본문", CYAN)
    text(d, (973, 309), "execute_at_utc_ms  ·  8 B", 30, WHITE, True)
    text(d, (973, 360), "0이면 즉시 / 발행 시각 이후면 예약", 24, MUTED)
    text(d, (973, 407), "뒤에 명령별 인자 0–168 B", 25, CYAN, True)

    box(d, (941, 511, 1848, 744))
    badge(d, (970, 536), "TM 예: GNSS_PVT", GREEN)
    text(d, (973, 592), "norad_id 4 B  +  status 1 B  +  age 2 B", 24, WHITE)
    text(d, (973, 633), "ECEF 위치 3 × f64  +  속도 3 × f64", 24, WHITE)
    text(d, (973, 679), "본문 55 B  →  전체 79 B", 28, GREEN, True)

    box(d, (941, 768, 1848, 1036))
    badge(d, (970, 793), "TM 예: FULL_HK_CHUNK", AMBER)
    text(d, (973, 849), "record_id 4 · index 2 · count 2 · total 2", 23, WHITE)
    text(d, (973, 888), "data_len 2 · record CRC 4 · 데이터 160", 23, WHITE)
    text(d, (973, 932), "본문 176 B  →  전체 200 B", 28, AMBER, True)
    text(d, (973, 983), "2,034 B HK 레코드 예: 13개 패킷", 22, MUTED)

    box(d, (72, 1058, 1848, 1117), "#332c29", "#795748", 13)
    text(d, (100, 1073), "CRC는 오류 검출만 수행 · OBC 인증, 비콘 전체 필드, 실제 RF 프레이밍은 CS/CDHS와 확정 필요", 23, WHITE)
    footer(d, w, 1135, "원본 계약: interfaces/pado_packets/draft_v0.json · tools/pado_packet_codec.py")
    save(im, "04_byte_field_layout.png")


if __name__ == "__main__":
    overview()
    tc()
    tm()
    field_layout()
