"""Render the future external-integration and security work plan as a vector figure."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.patches import FancyArrowPatch, Rectangle


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs"
PNG = OUT / "향후계획_보안연동_로드맵.png"
SVG = OUT / "향후계획_보안연동_로드맵.svg"

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

INK = "#172735"
MUTED = "#536573"
ACCENT = "#245F86"
RULE = "#9BAAB5"
PALE = "#F5F8FA"
WHITE = "#FFFFFF"


def font(size: float, *, bold: bool = False) -> FontProperties:
    return FontProperties(fname=str(FONTS[1 if bold else 0]), size=size)


def label(ax, x: float, y: float, value: str, *, size: float = 13,
          bold: bool = False, color: str = INK, ha: str = "left") -> None:
    ax.text(x, y, value, ha=ha, va="center", color=color,
            fontproperties=font(size, bold=bold), linespacing=1.31, zorder=5)


def rect(ax, x: float, y: float, w: float, h: float, *,
         fill: str = WHITE, edge: str = RULE, lw: float = 1.1) -> None:
    ax.add_patch(Rectangle((x, y), w, h, facecolor=fill,
                           edgecolor=edge, linewidth=lw, zorder=2))


def line(ax, x0: float, y0: float, x1: float, y1: float, *,
         color: str = RULE, lw: float = 1.0) -> None:
    ax.plot((x0, x1), (y0, y1), color=color, linewidth=lw, zorder=3)


def arrow(ax, start: tuple[float, float], end: tuple[float, float], *,
          color: str = ACCENT, lw: float = 1.5) -> None:
    ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>",
                                 mutation_scale=12, linewidth=lw,
                                 color=color, shrinkA=0, shrinkB=0, zorder=4))


def card(ax, x: float, y: float, w: float, h: float, *, title: str,
         description: str, deliverable: str, emphasis: bool = False) -> None:
    rect(ax, x, y, w, h, fill=PALE if emphasis else WHITE,
         edge=ACCENT if emphasis else RULE, lw=1.4 if emphasis else 1.1)
    label(ax, x + 0.20, y + h - 0.32, title, size=15.5, bold=True)
    label(ax, x + 0.20, y + h - 0.81, description, size=10.5,
          color=MUTED)
    line(ax, x + 0.20, y + 0.42, x + w - 0.20, y + 0.42,
         color=RULE, lw=0.8)
    label(ax, x + 0.20, y + 0.21, deliverable, size=10.1, color=ACCENT)


def draw(ax) -> None:
    card(ax, 0.58, 4.55, 6.20, 1.55,
         title="외부 기관 연동 기준",
         description="교환 프레임·프로토콜·주소·인증\nPASS 예약·TC 송출 책임",
         deliverable="산출물  N01·N02 상대 ICD · N08 인계 규격")
    card(ax, 7.22, 4.55, 6.20, 1.55,
         title="비행 TC/TM 기준",
         description="CS/CDHS 패킷 필드·인증·ACK\n기준 바이트열과 오류 사례",
         deliverable="산출물  비행용 ICD · 기준 패킷")

    # Both agreements feed the two implementation tracks.
    line(ax, 3.68, 4.55, 3.68, 4.31, color=ACCENT, lw=1.25)
    line(ax, 10.32, 4.55, 10.32, 4.31, color=ACCENT, lw=1.25)
    line(ax, 3.68, 4.31, 10.32, 4.31, color=ACCENT, lw=1.25)
    arrow(ax, (3.68, 4.31), (3.68, 3.98), lw=1.3)
    arrow(ax, (10.32, 4.31), (10.32, 3.98), lw=1.3)

    card(ax, 0.58, 1.93, 6.20, 1.98,
         title="망 분리·접근 정책",
         description="DGS만 외부 경로 허용 · 그 외 기본 차단\n컨테이너·호스트·IPv4/IPv6·DNS 우회 시험",
         deliverable="산출물  허용 흐름표 · 차단 및 재시작 시험")
    card(ax, 7.22, 1.93, 6.20, 1.98,
         title="DGS 외부 송출 방화벽",
         description="TC 프레임: 형식·SCID·PASS 시간창·송출 빈도\n안테나 요청: 대상·스키마 확인, 불일치 차단\n수신 TM 오류는 별도 격리·기록",
         deliverable="산출물  송출 허용 규칙 · 차단 사유 · 감사 기록",
         emphasis=True)

    line(ax, 3.68, 1.93, 3.68, 1.70, color=ACCENT, lw=1.25)
    line(ax, 10.32, 1.93, 10.32, 1.70, color=ACCENT, lw=1.25)
    line(ax, 3.68, 1.70, 10.32, 1.70, color=ACCENT, lw=1.25)
    arrow(ax, (7.00, 1.70), (7.00, 1.39), lw=1.3)

    rect(ax, 0.58, 0.25, 12.84, 1.10, fill=WHITE, edge=RULE, lw=1.1)
    milestones = ((1.73, "합성·변조 입력"), (5.18, "FlatSat"),
                  (8.40, "기관 공동 시험"), (11.79, "운용 환경 확인"))
    for x, value in milestones:
        label(ax, x, 0.80, value, size=12.9, bold=True, ha="center")
    for left, right in ((3.34, 3.87), (6.49, 6.84), (10.04, 10.35)):
        arrow(ax, (left, 0.80), (right, 0.80), color=RULE, lw=1.1)


def main() -> None:
    plt.rcParams["svg.fonttype"] = "path"
    plt.rcParams["svg.hashsalt"] = "pado-gs-future-security-plan"
    fig = plt.figure(figsize=(12, 5.5), dpi=360, facecolor=WHITE)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(0, 14)
    ax.set_ylim(0, 6.40)
    ax.axis("off")
    draw(ax)
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(PNG, dpi=360, facecolor=WHITE)
    fig.savefig(SVG, facecolor=WHITE, metadata={"Date": "2026-10-25"})
    plt.close(fig)
    svg = "\n".join(line.rstrip() for line in SVG.read_text(encoding="utf-8").splitlines()) + "\n"
    with SVG.open("w", encoding="utf-8", newline="\n") as output:
        output.write(svg)
    print(f"{PNG} | {SVG}")


if __name__ == "__main__":
    main()
