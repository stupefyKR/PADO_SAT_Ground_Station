"""Build standalone, non-operational ROS/MPS layout previews for local review."""

from pathlib import Path
import re


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
        html = html.replace('<script src="/assets/validation-console.js" defer></script>', '')
        html = html.replace('<link rel="stylesheet" href="/assets/validation-console.css">', '')
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
        html = html.replace('<div class="shell">',
                            f'{banner}<div class="shell">', 1)
        if source == "ops":
            html = html.replace('id="ros-mode">확인 중', 'id="ros-mode">SIM_ONLY')
            html = html.replace('id="ros-tm">확인 중', 'id="ros-tm">시험 규격')
            html = html.replace('id="ros-tc">차단', 'id="ros-tc">차단')
            packet_browser = (ROOT / "web/ops/packet_browser.js").read_text(encoding="utf-8")
            workbench = (ROOT / "web/ops/workbench.js").read_text(encoding="utf-8")
            offline_startup = r'''
  setupTabs();
  tmSource = "example";
  loadTm(syntheticTm(), "오프라인 합성 예시");
  packetBrowser = window.rosPacketBrowser.create({api});
  $("trend-details").open = true;
  $("sensor-filter").addEventListener("input", renderSensors);
  document.querySelectorAll("[data-range]").forEach((button) => button.addEventListener("click", () => {
    minutes = Number(button.dataset.range);
    document.querySelectorAll("[data-range]").forEach((item) =>
      item.setAttribute("aria-pressed", String(item === button)));
    renderSensors();
  }));
  document.querySelectorAll("#ros-view-tc button, #ros-view-tc input, #ros-view-tc select, #demo-toggle, #tm-file, .state-history-controls button, .state-history-controls input, .state-history-controls select")
    .forEach((node) => { node.disabled = true; });
  $("overview-beacon-state").textContent = "서버 연결 없음";
  $("overview-beacon-summary").textContent = "비콘·TC·수신 원문은 VM 서버에서 확인합니다.";
  $("packet-browser-state").textContent = "서버/API 연결 없음 · 수신 원문 조회는 VM 서버에서 가능합니다.";
  $("spacecraft-event-count").textContent = "서버 연결 없음";
  $("attitude-model-state").textContent = "3D CAD는 서버 화면에서 확인";
  $("state-history-mode").textContent = "오프라인 미리보기";
  $("state-history-selected").textContent = "서버 연결 필요";
  $("state-history-status").textContent = "최근 72시간의 위치·자세 이력 재생은 VM 서버 연결 후 사용할 수 있습니다.";
  const canvas = $("attitude-canvas");
  const context = canvas.getContext("2d");
  if (context) {
    context.fillStyle = "#091824"; context.fillRect(0, 0, canvas.width, canvas.height);
    context.fillStyle = "#9dbdc9"; context.font = "18px sans-serif"; context.textAlign = "center";
    context.fillText("3D CAD · VM 서버 연결 후 표시", canvas.width / 2, canvas.height / 2);
  }
'''
            workbench, count = re.subn(r"\s+init\(\);\s*\}\)\(\);\s*$",
                                       offline_startup + "\n})();", workbench)
            if count != 1:
                raise ValueError("ROS preview could not replace the API startup")
            startup = ('setupUtcClock();\n'
                       'api = async () => { throw new Error("서버/API 연결 없음"); };\n' +
                       packet_browser + "\n" + workbench)
            html = html.replace('<script src="/assets/ros-workbench.js" defer></script>', '')
            for script in ("ros-packet-browser.js", "ros-attitude-math.js",
                           "ros-attitude-gl.js", "ros-attitude-viewer.js", "ros-state-history.js",
                           "ros-sensor-settings.js"):
                html = html.replace(f'<script src="/assets/{script}" defer></script>', '')
        else:
            html = html.replace('<script src="/assets/mps-live.js" defer></script>', '')
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
