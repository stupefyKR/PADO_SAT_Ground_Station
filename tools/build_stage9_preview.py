"""Build standalone, non-operational ROS/MPS layout previews for local review."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "out/stage9-preview"


def build() -> list[Path]:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    common_css = (ROOT / "web/common/style.css").read_text(encoding="utf-8")
    app_js = (ROOT / "web/common/app.js").read_text(encoding="utf-8")
    # The preview keeps chart drawing helpers but never starts the portal/API client.
    app_helpers = app_js.split("const page = document.body.dataset.page;", 1)[0]
    results = []
    for source, name in (("ops", "ros-preview.html"),
                         ("mps", "mps-preview.html")):
        html = (ROOT / f"web/{source}/index.html").read_text(encoding="utf-8")
        html = html.replace('<link rel="stylesheet" href="/assets/style.css">',
                            f"<style>{common_css}</style>")
        extra = (ROOT / ("web/ops/workbench.css" if source == "ops" else
                         "web/mps/workflow.css")).read_text(encoding="utf-8")
        route = "/assets/ros-workbench.css" if source == "ops" else "/assets/mps-workflow.css"
        html = html.replace(f'<link rel="stylesheet" href="{route}">',
                            f"<style>{extra}</style>")
        html = html.replace('href="/ops"', 'href="ros-preview.html"')
        html = html.replace('href="/mps"', 'href="mps-preview.html"')
        html = html.replace('href="/orbit"', 'href="#"')
        html = html.replace('id="user-badge" class="user-badge">확인 중',
                            'id="user-badge" class="user-badge">화면 미리보기')
        html = html.replace('<button id="logout" class="ghost">로그아웃</button>',
                            '<button id="logout" class="ghost" disabled>로그아웃</button>')
        banner = ('<div style="position:relative;z-index:10;background:#fef3c7;'
                  'color:#78350f;text-align:center;padding:10px;font:600 14px system-ui">'
                  '화면 미리보기 · 합성 예시 · 서버/API 연결 없음 · 명령 실행 불가</div>')
        html = html.replace(f'<body data-page="{source}">',
                            f'<body data-page="{source}">{banner}')
        if source == "ops":
            html = html.replace('id="ros-mode">확인 중', 'id="ros-mode">SIM_ONLY')
            html = html.replace('id="ros-tm">확인 중', 'id="ros-tm">시험 규격')
            html = html.replace('id="ros-tc">차단', 'id="ros-tc">차단')
            startup = r'''
setupUtcClock();
setupRosExample();
document.getElementById("demo-toggle").click();
document.getElementById("ros-current-count").textContent = "합성 예시";
document.getElementById("ros-current-alerts").textContent =
  "시험용 합성 그래프입니다. TM·자세 탭에서 센서 추세를 확인하세요.";
document.querySelectorAll("[data-ros-target]").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll("[data-ros-target]").forEach((item) => {
      const active = item === tab;
      item.setAttribute("aria-selected", String(active));
      item.tabIndex = active ? 0 : -1;
      document.getElementById("ros-view-" + item.dataset.rosTarget).hidden = !active;
    });
  });
});
'''
            html = html.replace('<script src="/assets/ros-workbench.js" defer></script>', '')
        else:
            html = html.replace('id="mps-version">확인 중', 'id="mps-version">시험 초안')
            html = html.replace('id="mps-commands">—', 'id="mps-commands">0')
            html = html.replace('id="mps-passes">—', 'id="mps-passes">입력 대기')
            startup = 'setupUtcClock();'
        html = html.replace('<script src="/assets/app.js" defer></script>',
                            f'<script>{app_helpers}\n{startup}</script>')
        target = OUTPUT / name
        target.write_bytes(html.encode("utf-8"))
        results.append(target)
    return results


if __name__ == "__main__":
    for result in build():
        print(result)
