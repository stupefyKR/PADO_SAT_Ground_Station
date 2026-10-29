"use strict";

// Only monitoring bounds can be edited here. Physical ratings stay in the shared file.
(() => {
  function create({api, session, onSaved = () => {}}) {
    const get = id => document.getElementById(id);
    const panel = get("sensor-settings");
    if (!panel) return null;
    const body = get("sensor-settings-rows");
    const status = get("sensor-settings-status");
    const save = get("sensor-settings-save");
    const reload = get("sensor-settings-reload");
    const editable = ["planner", "operator", "lead"].includes(session.role);
    let current = null, busy = false, dirty = false, conflict = false;
    let snapshot = null, source = "published";
    const rows = new Map();
    const tell = (text, error = false) => {
      status.textContent = text;
      status.classList.toggle("error", error);
    };
    function controls() {
      save.disabled = !editable || busy || !current || !dirty || conflict;
      reload.disabled = busy;
      for (const row of rows.values()) {
        row.lower.disabled = row.upper.disabled = !editable || busy || conflict;
      }
    }
    function measurements() {
      const values = new Map((source === "published" ? snapshot?.sensors || [] : [])
        .map(sensor => [sensor.id, sensor.samples.at(-1)]));
      for (const [id, row] of rows) {
        const sample = values.get(id);
        row.measured.textContent = sample ? `${sample.value} ${row.unit}` : "수신값 없음";
        row.time.textContent = sample ? sample.utc : "—";
      }
    }
    function render(view) {
      rows.clear(); body.replaceChildren();
      for (const sensor of view.sensors) {
        const tr = document.createElement("tr");
        const td = text => {
          const node = document.createElement("td"); node.textContent = text;
          tr.append(node); return node;
        };
        const name = td(`${sensor.group} · ${sensor.name}`);
        const key = document.createElement("small"); key.textContent = sensor.key;
        name.append(key);
        const input = (value, label) => {
          const node = document.createElement("input");
          node.type = "number"; node.step = "any"; node.value = String(value);
          node.required = true;
          node.setAttribute("aria-label", `${sensor.name} ${label}`);
          node.addEventListener("input", () => {
            dirty = true; tell("변경 사항을 저장하면 게시된 센서 판정에 바로 적용됩니다."); controls();
          });
          td("").append(node); return node;
        };
        const lower = input(sensor.lower, "최소 허용값");
        const upper = input(sensor.upper, "최대 허용값");
        td(sensor.unit);
        const measured = td("수신값 없음"), time = td("—");
        rows.set(sensor.id, {key:sensor.key, lower, upper, measured, time, unit:sensor.unit});
        body.append(tr);
      }
      measurements(); controls();
    }
    async function refresh() {
      if (busy) return;
      busy = true; controls(); tell("설정 파일을 읽는 중입니다.");
      try {
        current = await api("/api/ros/settings");
        dirty = false; conflict = false; render(current);
        tell(editable ? "공통 설정 파일의 센서 허용 범위입니다." : "조회 전용 계정입니다.");
      } catch (error) {tell(`설정 읽기 실패: ${error.message}`, true);}
      finally {busy = false; controls();}
    }
    async function submit(event) {
      event.preventDefault();
      if (!editable || busy || !current || !dirty || conflict) return;
      const limits = {};
      for (const row of rows.values()) {
        const lower = Number(row.lower.value), upper = Number(row.upper.value);
        if (!row.lower.value.trim() || !row.upper.value.trim() ||
            !Number.isFinite(lower) || !Number.isFinite(upper) || lower >= upper) {
          tell("최소·최대 허용값에 숫자를 입력하고 최소값을 최대값보다 작게 설정하세요.", true);
          return;
        }
        limits[row.key] = {lower, upper};
      }
      busy = true; controls(); tell("센서 허용 범위를 저장하는 중입니다.");
      try {
        current = await api("/api/ros/settings", {method:"POST",
          headers:{"Content-Type":"application/json", "X-CSRF-Token":session.csrf},
          body:JSON.stringify({expected_sha256:current.sha256, limits})});
        dirty = false; render(current);
        onSaved(current);
        tell("저장했습니다. 수신된 센서 값과 그래프의 한계를 다시 평가했습니다.");
      } catch (error) {
        conflict = /settings_conflict/.test(error.message);
        tell(conflict ? "다른 작업에서 설정이 바뀌었습니다. 다시 읽기를 눌러 최신 설정을 확인한 뒤 수정하세요." :
          `저장 실패: ${error.message}`, true);
      } finally {busy = false; controls();}
    }
    get("sensor-settings-form").addEventListener("submit", submit);
    reload.addEventListener("click", refresh);
    panel.addEventListener("toggle", () => {if (panel.open && !current && !busy) refresh();});
    controls();
    return {refresh, updateMeasurements(value, kind = "published") {
      snapshot = value; source = kind; measurements();
    }};
  }
  window.rosSensorSettings = Object.freeze({create});
})();
