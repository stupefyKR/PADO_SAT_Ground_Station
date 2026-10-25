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


def section(ax, y: float, number: str, heading: str) -> None:
    label(ax, 0.58, y, number, size=11.8, bold=True, color=ACCENT)
    label(ax, 1.02, y, heading, size=12.8, bold=True)


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
    label(ax, 0.58, 7.74, "향후 외부 연동·보안 개발 계획", size=19, bold=True)
    label(ax, 13.42, 7.74, "결정 → 구현 → 수락", size=10.4,
          color=MUTED, ha="right")
    line(ax, 0.58, 7.43, 13.42, 7.43, color=INK, lw=1.0)

    section(ax, 7.10, "01", "선행 협의")
    card(ax, 0.58, 5.28, 6.20, 1.55,
         title="외부 기관 연동 기준",
         description="교환 프레임·프로토콜·주소·인증\nPASS 예약·TC 송출 책임",
         deliverable="산출물  N01·N02 상대 ICD · N08 인계 규격")
    card(ax, 7.22, 5.28, 6.20, 1.55,
         title="비행 TC/TM 기준",
         description="CS/CDHS 패킷 필드·인증·ACK\n기준 바이트열과 오류 사례",
         deliverable="산출물  비행용 ICD · 기준 패킷")

    # Both agreements feed the two implementation tracks.
    line(ax, 3.68, 5.28, 3.68, 5.04, color=ACCENT, lw=1.25)
    line(ax, 10.32, 5.28, 10.32, 5.04, color=ACCENT, lw=1.25)
    line(ax, 3.68, 5.04, 10.32, 5.04, color=ACCENT, lw=1.25)
    arrow(ax, (3.68, 5.04), (3.68, 4.50), lw=1.3)
    arrow(ax, (10.32, 5.04), (10.32, 4.50), lw=1.3)

    section(ax, 4.83, "02", "병행 개발")
    card(ax, 0.58, 2.67, 6.20, 1.80,
         title="망 분리·접근 정책",
         description="DGS만 외부 경로 허용 · 그 외 기본 차단\n컨테이너·호스트·IPv4/IPv6·DNS 우회 시험",
         deliverable="산출물  허용 흐름표 · 차단 및 재시작 시험")
    card(ax, 7.22, 2.67, 6.20, 1.80,
         title="DGS 패킷 검증",
         description="TC: 형식·SCID·시간창·송출 빈도\nN08: 스키마·대상  /  TM: 오류 격리·기록",
         deliverable="산출물  검사 규칙 · 거부 사유 · 감사 기록",
         emphasis=True)

    line(ax, 3.68, 2.67, 3.68, 2.45, color=ACCENT, lw=1.25)
    line(ax, 10.32, 2.67, 10.32, 2.45, color=ACCENT, lw=1.25)
    line(ax, 3.68, 2.45, 10.32, 2.45, color=ACCENT, lw=1.25)
    arrow(ax, (7.00, 2.45), (7.00, 1.99), lw=1.3)

    section(ax, 2.24, "03", "단계별 수락")
    rect(ax, 0.58, 0.80, 12.84, 1.16, fill=WHITE, edge=RULE, lw=1.1)
    milestones = ((1.73, "합성·변조 입력"), (5.18, "FlatSat"),
                  (8.40, "기관 공동 시험"), (11.79, "현장 수락"))
    for x, value in milestones:
        label(ax, x, 1.59, value, size=12.9, bold=True, ha="center")
    for left, right in ((3.34, 3.87), (6.49, 6.84), (10.04, 10.35)):
        arrow(ax, (left, 1.59), (right, 1.59), color=RULE, lw=1.1)
    label(ax, 7.00, 1.08,
          "정상 경로·거부·감사 일치  /  비DGS 우회 차단  /  재시작 후 정책 유지",
          size=10.6, color=MUTED, ha="center")

    label(ax, 7.00, 0.36,
          "외부 연결 개방은 합의된 규격과 수락 증거를 승인한 뒤 결정",
          size=10.9, bold=True, color=ACCENT, ha="center")


def main() -> None:
    plt.rcParams["svg.fonttype"] = "path"
    plt.rcParams["svg.hashsalt"] = "pado-gs-future-security-plan"
    fig = plt.figure(figsize=(12, 7.1), dpi=360, facecolor=WHITE)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(0, 14)
    ax.set_ylim(0, 8.25)
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
