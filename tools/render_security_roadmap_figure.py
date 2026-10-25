"""Render a publication-ready security roadmap and target data boundary."""

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

INK = "#182937"
MUTED = "#526473"
ACCENT = "#245F86"
LINE = "#8A9AA6"
PALE = "#F7F9FA"
BLUE_PALE = "#EAF3F8"
WHITE = "#FFFFFF"


def font(size: float, *, bold: bool = False) -> FontProperties:
    return FontProperties(fname=str(FONTS[1 if bold else 0]), size=size)


def label(ax, x: float, y: float, value: str, *, size: float = 13,
          bold: bool = False, color: str = INK, ha: str = "left",
          va: str = "center", rotation: float = 0) -> None:
    ax.text(x, y, value, ha=ha, va=va, color=color,
            fontproperties=font(size, bold=bold), rotation=rotation,
            linespacing=1.34, zorder=6)


def box(ax, x: float, y: float, w: float, h: float, *,
        fill: str = WHITE, edge: str = LINE, lw: float = 1.25,
        dashed: bool = False) -> None:
    ax.add_patch(Rectangle((x, y), w, h, facecolor=fill, edgecolor=edge,
                           linewidth=lw, linestyle="--" if dashed else "-",
                           zorder=2))


def arrow(ax, start: tuple[float, float], end: tuple[float, float], *,
          both: bool = False, planned: bool = False, color: str = INK,
          lw: float = 1.6) -> None:
    ax.add_patch(FancyArrowPatch(
        start, end, arrowstyle="<->" if both else "-|>",
        mutation_scale=13, linewidth=lw, color=color,
        linestyle=(0, (4, 3)) if planned else "-", zorder=4,
        shrinkA=0, shrinkB=0))


def draw_roadmap(ax) -> None:
    label(ax, 0.48, 7.93, "A", size=18, bold=True, color=ACCENT)
    label(ax, 0.84, 7.93, "개발·검증 순서", size=17, bold=True)
    steps = (
        ("01", "항우연 요구사항", "교환 단위·인증·PASS 절차\nN01/N02/N08 ICD"),
        ("02", "논리적 망 격리", "DGS 전용 외부 경로\n비DGS 우회 거부 시험"),
        ("03", "DGS 검증 게이트", "TC·N08 송출 내용 확인\nTM 오류 격리·기록"),
        ("04", "연동 수락", "모의 상대·FlatSat·공동 시험\n재시작·감사 결과 대조"),
    )
    x_positions = (0.48, 3.82, 7.16, 10.50)
    y, w, h = 6.19, 3.02, 1.36
    for i, (number, heading, detail) in enumerate(steps):
        x = x_positions[i]
        box(ax, x, y, w, h, fill=BLUE_PALE if i == 2 else WHITE,
            edge=ACCENT if i == 2 else LINE, lw=1.75 if i == 2 else 1.25)
        label(ax, x + 0.20, y + 1.08, number, size=11.5, bold=True,
              color=ACCENT)
        label(ax, x + 0.20, y + 0.78, heading, size=16, bold=True)
        label(ax, x + 0.20, y + 0.35, detail, size=11.2, color=MUTED)
        if i < 3:
            arrow(ax, (x + w + 0.05, y + 0.68),
                  (x_positions[i + 1] - 0.07, y + 0.68),
                  color=ACCENT, lw=1.4)


def node(ax, x: float, y: float, w: float, h: float, title: str,
         subtitle: str = "", *, fill: str = WHITE,
         edge: str = LINE, dashed: bool = False) -> None:
    box(ax, x, y, w, h, fill=fill, edge=edge,
        lw=1.5 if edge == ACCENT else 1.2, dashed=dashed)
    label(ax, x + 0.18, y + h - 0.25, title, size=15, bold=True)
    if subtitle:
        label(ax, x + 0.18, y + 0.20, subtitle, size=10.9, color=MUTED)


def draw_boundary(ax) -> None:
    label(ax, 0.48, 5.79, "B", size=18, bold=True, color=ACCENT)
    label(ax, 0.84, 5.79, "목표 통신 경계", size=17, bold=True)
    label(ax, 13.52, 5.79, "운용 데이터 경로 · 외부 연결은 협의 전 미개방",
          size=11, color=MUTED, ha="right")

    box(ax, 0.48, 0.55, 9.36, 4.88, fill=PALE,
        edge=LINE, lw=1.4)
    label(ax, 0.79, 5.17, "단일 Linux VM  /  역할별 Docker 컨테이너", size=12.5,
          bold=True, color=MUTED)

    node(ax, 0.85, 4.00, 3.10, 0.77, "MPS", "PASS·TC 시퀀스")
    node(ax, 0.85, 2.98, 3.10, 0.77, "ROS", "TC 준비·TM 처리")
    node(ax, 0.85, 1.96, 3.10, 0.77, "FDS", "궤도·PASS 예측")

    box(ax, 5.55, 2.47, 3.83, 2.30, fill=BLUE_PALE,
        edge=ACCENT, lw=1.8)
    label(ax, 5.79, 4.47, "DGS-GS", size=16.5, bold=True, color=ACCENT)
    ax.plot([5.79, 9.14], [4.26, 4.26], color=ACCENT, linewidth=1.0)
    label(ax, 5.79, 3.99, "송출 게이트", size=12.8, bold=True)
    label(ax, 5.79, 3.72, "프레임·필드·PASS 시간창·속도", size=10.9)
    ax.plot([5.79, 9.14], [3.49, 3.49], color=LINE, linewidth=0.9)
    label(ax, 5.79, 3.22, "TM 수신 검증", size=12.8, bold=True)
    label(ax, 5.79, 2.93, "이상 프레임 격리·기록", size=10.9)

    node(ax, 5.55, 1.46, 3.83, 0.73, "DGS-NET", "승인된 TLE 출처만",
         fill=WHITE)

    # Internal N2 data paths. Arrows depict the planned data flow, not a
    # statement that the operational endpoints are already deployed.
    arrow(ax, (2.41, 4.00), (2.41, 3.75), color=ACCENT, lw=1.4)
    label(ax, 2.62, 3.88, "계획", size=10, color=MUTED)
    arrow(ax, (3.98, 3.43), (5.52, 3.43), color=ACCENT)
    label(ax, 4.61, 3.62, "TC", size=10.6, bold=True, ha="center")
    arrow(ax, (5.52, 3.12), (3.98, 3.12), color=ACCENT)
    label(ax, 4.61, 2.95, "TM", size=10.6, bold=True, ha="center")
    arrow(ax, (3.98, 2.46), (5.52, 2.75), color=INK, lw=1.3)
    label(ax, 4.66, 2.67, "N08", size=10.3, ha="center")
    arrow(ax, (5.52, 1.77), (3.98, 2.20), color=INK, lw=1.3)
    label(ax, 4.68, 1.85, "N05", size=10.3, ha="center")

    # Host and container network controls remain separate from the content
    # checks inside DGS-GS.
    box(ax, 0.85, 0.68, 8.53, 0.62, fill=WHITE, edge="#BBC6CD", lw=0.9)
    label(ax, 1.05, 1.08, "내부 전용 네트워크  ·  비DGS 외부 경로 차단",
          size=10.5, color=MUTED)
    label(ax, 1.05, 0.84, "UFW(호스트 입력)  ·  DOCKER-USER(게시·전달 경계)",
          size=10.5, color=MUTED)

    label(ax, 10.43, 5.17, "외부 상대 · 승인 전 연결 차단", size=12.5,
          bold=True, color=MUTED)
    node(ax, 10.43, 2.77, 3.12, 1.78, "항우연 · 연세대",
         "N01/N02 상대 ICD 협의", dashed=True)
    label(ax, 10.61, 3.34, "대전 S-band: 현재 PASS 조회만", size=10.4,
          color=MUTED)
    node(ax, 10.43, 1.46, 3.12, 0.73, "승인 TLE 출처", "계정·상대 미연결",
         dashed=True)
    arrow(ax, (9.41, 3.58), (10.40, 3.58), both=True,
          planned=True, color=ACCENT, lw=1.5)
    label(ax, 9.91, 3.84, "협의 후", size=9.8, color=MUTED, ha="center")
    arrow(ax, (9.41, 1.82), (10.40, 1.82), both=True,
          planned=True, color=ACCENT, lw=1.5)
    label(ax, 9.91, 2.06, "협의 후", size=9.8, color=MUTED, ha="center")


def main() -> None:
    plt.rcParams["svg.fonttype"] = "path"
    plt.rcParams["svg.hashsalt"] = "pado-gs-security-roadmap"
    plt.rcParams["figure.facecolor"] = WHITE
    fig = plt.figure(figsize=(12, 7.2), dpi=360)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 14)
    ax.set_ylim(0, 8.4)
    ax.axis("off")
    draw_roadmap(ax)
    draw_boundary(ax)
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(PNG, dpi=360, facecolor=WHITE)
    fig.savefig(SVG, facecolor=WHITE, metadata={"Date": "2026-10-25"})
    plt.close(fig)
    # Matplotlib writes CRLF and spaces at path-line ends on Windows. Keep the
    # checked-in vector artifact stable and free of Git whitespace errors.
    svg = "\n".join(line.rstrip() for line in SVG.read_text(encoding="utf-8").splitlines()) + "\n"
    with SVG.open("w", encoding="utf-8", newline="\n") as output:
        output.write(svg)
    print(f"{PNG} | {SVG}")


if __name__ == "__main__":
    main()
