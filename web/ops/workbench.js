"use strict";

// This UI has no timer or API that can execute a reservation or send a TC.
(() => {
  const $ = (id) => document.getElementById(id);
  const svgNS = "http://www.w3.org/2000/svg";
  const utc = (seconds) => new Date(seconds * 1000).toISOString().replace(".000Z", "Z");
  const message = (id, content, error = false) => {
    $(id).textContent = content;
    $(id).classList.toggle("error", error);
  };
  const empty = (target, columns, content) => {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = columns;
    cell.className = "table-empty";
    cell.textContent = content;
    row.append(cell);
    target.replaceChildren(row);
  };
  const cell = (row, content) => {
    const node = document.createElement("td");
    node.textContent = String(content);
    row.append(node);
    return node;
  };
  const svg = (name, attributes = {}, content = "") => {
    const node = document.createElementNS(svgNS, name);
    for (const [key, val] of Object.entries(attributes)) node.setAttribute(key, String(val));
    node.textContent = content;
    return node;
  };
  let session;
  let sequences = [];
  let reservations = [];
  let selectedTm = null;
  let selectedSensor = null;
  let minutes = 30;
  let tmSource = "published";
  let lastPublishedRaw = "";
  let previewPending = false;
  let publishedReachable = false;

  function activateTab(name, focus = false) {
    const tabs = [...document.querySelectorAll("[data-ros-target]")];
    for (const tab of tabs) {
      const active = tab.dataset.rosTarget === name;
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
      $("ros-view-" + tab.dataset.rosTarget).hidden = !active;
      if (active && focus) tab.focus();
    }
  }

  function setupTabs() {
    const tabs = [...document.querySelectorAll("[data-ros-target]")];
    tabs.forEach((tab, index) => {
      tab.addEventListener("click", () => activateTab(tab.dataset.rosTarget));
      tab.addEventListener("keydown", (event) => {
        const next = event.key === "ArrowRight" ? index + 1 :
          event.key === "ArrowLeft" ? index - 1 :
          event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : null;
        if (next === null) return;
        event.preventDefault();
        activateTab(tabs[(next + tabs.length) % tabs.length].dataset.rosTarget, true);
      });
    });
    activateTab("status");
  }

  function selectedSequence() {
    return sequences.find((item) => item.sha256 === $("sequence-select").value);
  }
  function showSequence() {
    const sequence = selectedSequence();
    const steps = $("sequence-steps");
    const picker = $("reservation-step");
    steps.replaceChildren();
    picker.replaceChildren();
    if (!sequence) {
      empty(steps, 4, "MPS 시험 시퀀스를 불러오면 단계가 표시됩니다.");
      $("sequence-pass").textContent = "패스 미선택";
      return;
    }
    $("sequence-pass").textContent =
      `${sequence.pass_id} · ${utc(sequence.aos_utc_s)} → ${utc(sequence.los_utc_s)} · 시험 전용`;
    for (const step of sequence.steps) {
      const row = document.createElement("tr");
      cell(row, step.step_id);
      cell(row, step.command_id);
      cell(row, `+${step.offset_s}초`);
      cell(row, utc(sequence.aos_utc_s + step.offset_s));
      steps.append(row);
      const option = document.createElement("option");
      option.value = String(step.step_id);
      option.textContent = `#${step.step_id} · ${step.command_id}`;
      picker.append(option);
    }
    setReservationDefault();
  }
  function setReservationDefault() {
    const sequence = selectedSequence();
    const step = sequence?.steps.find((item) => item.step_id === Number($("reservation-step").value));
    if (!step) return;
    $("reservation-time").value =
      new Date((sequence.aos_utc_s + step.offset_s) * 1000).toISOString().slice(0, 19);
  }
  function showReservations() {
    const rows = $("reservation-rows");
    rows.replaceChildren();
    if (!reservations.length) {
      empty(rows, 5, "예약 초안이 없습니다.");
      return;
    }
    for (const item of reservations) {
      const sequence = sequences.find((entry) => entry.sha256 === item.sha256);
      const step = sequence?.steps.find((entry) => entry.step_id === item.step_id);
      const row = document.createElement("tr");
      cell(row, item.status === "DRAFT_ONLY" ? "초안 · 자동 실행 안 됨" : "취소");
      cell(row, step?.command_id || `#${item.step_id}`);
      cell(row, utc(item.target_utc_s));
      cell(row, item.created_by);
      const control = document.createElement("td");
      if (item.status === "DRAFT_ONLY" && item.created_by === session.username) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "outline";
        button.textContent = "취소";
        button.addEventListener("click", async () => {
          try {
            await api("/api/ros/test-reservations/cancel", {
              method: "POST", headers: {"Content-Type": "application/json", "X-CSRF-Token": session.csrf},
              body: JSON.stringify({id: item.id})
            });
            await refreshDrafts();
            message("reservation-message", "예약 초안을 취소했습니다.");
          } catch (error) { message("reservation-message", error.message, true); }
        });
        control.append(button);
      }
      row.append(control);
      rows.append(row);
    }
  }
  async function refreshDrafts(selectDigest = "") {
    const [sequenceData, reservationData] = await Promise.all([
      api("/api/ros/test-sequences"), api("/api/ros/test-reservations")
    ]);
    sequences = sequenceData.items;
    reservations = reservationData.items;
    const picker = $("sequence-select");
    const keep = selectDigest || picker.value;
    picker.replaceChildren(new Option("없음", ""));
    for (const item of sequences) {
      picker.add(new Option(`${item.pass_id} · r${item.revision} · ${item.sha256.slice(0, 10)}`, item.sha256));
    }
    if (sequences.some((item) => item.sha256 === keep)) picker.value = keep;
    showSequence();
    showReservations();
  }
  async function importSequence() {
    const bodyFile = $("sequence-file").files[0];
    const manifestFile = $("manifest-file").files[0];
    if (!bodyFile || !manifestFile || bodyFile.size > 32768 || manifestFile.size > 4096) {
      message("sequence-message", "시퀀스와 매니페스트 파일을 선택하세요. 크기 제한은 32 KiB / 4 KiB입니다.", true);
      return;
    }
    try {
      const result = await api("/api/ros/test-sequences", {
        method: "POST", headers: {"Content-Type": "application/json", "X-CSRF-Token": session.csrf},
        body: JSON.stringify({sequence_text: await bodyFile.text(), manifest_text: await manifestFile.text()})
      });
      await refreshDrafts(result.sha256);
      message("sequence-message", "시험 시퀀스와 매니페스트의 해시를 확인해 초안 저장소에 등록했습니다.");
    } catch (error) { message("sequence-message", `불러오기 실패: ${error.message}`, true); }
  }
  async function reserve(event) {
    event.preventDefault();
    const sequence = selectedSequence();
    const stamp = $("reservation-time").value;
    const when = Date.parse(stamp + "Z") / 1000;
    if (!sequence || !Number.isSafeInteger(when)) {
      message("reservation-message", "시험 시퀀스와 유효한 UTC 시각을 선택하세요.", true);
      return;
    }
    try {
      await api("/api/ros/test-reservations", {
        method: "POST", headers: {"Content-Type": "application/json", "X-CSRF-Token": session.csrf},
        body: JSON.stringify({sha256: sequence.sha256, step_id: Number($("reservation-step").value),
                              target_utc_s: when})
      });
      await refreshDrafts(sequence.sha256);
      message("reservation-message", "예약 초안을 저장했습니다. 자동 실행과 TC 송출은 없습니다.");
    } catch (error) { message("reservation-message", `초안 저장 실패: ${error.message}`, true); }
  }

  function normalizedTm(data) {
    const iso = (value) => typeof value === "string" && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/.test(value) &&
      Number.isFinite(Date.parse(value));
    const number = (value) => typeof value === "number" && Number.isFinite(value);
    if (!data || data.type !== "TEST_ONLY_TM_SNAPSHOT" || data.non_operational !== true ||
        !iso(data.received_utc) || !Array.isArray(data.sensors) ||
        data.sensors.length < 1 || data.sensors.length > 64) throw Error("시험 TM 형식이 아닙니다.");
    const ids = new Set();
    for (const sensor of data.sensors) {
      if (!sensor || typeof sensor.id !== "string" || !/^[A-Za-z0-9._-]{1,48}$/.test(sensor.id) ||
          ids.has(sensor.id) || typeof sensor.name !== "string" || sensor.name.length > 64 ||
          typeof sensor.group !== "string" || sensor.group.length > 64 ||
          typeof sensor.unit !== "string" || sensor.unit.length > 12 ||
          !number(sensor.lower) || !number(sensor.upper) || sensor.lower >= sensor.upper ||
          !Array.isArray(sensor.samples) || !sensor.samples.length || sensor.samples.length > 3600) {
        throw Error("시험 센서 항목이 유효하지 않습니다.");
      }
      ids.add(sensor.id);
      let previous = -Infinity;
      for (const sample of sensor.samples) {
        const stamp = Date.parse(sample?.utc);
        if (!iso(sample?.utc) || !number(sample?.value) || stamp <= previous ||
            stamp > Date.parse(data.received_utc)) throw Error("TM 시각이나 값이 유효하지 않습니다.");
        previous = stamp;
      }
    }
    if (data.attitude !== null && data.attitude !== undefined) {
      const a = data.attitude;
      if (!a || !iso(a.utc) || Date.parse(a.utc) > Date.parse(data.received_utc) ||
          typeof a.frame !== "string" || !/^[A-Za-z0-9._-]{1,32}$/.test(a.frame) ||
          !Array.isArray(a.quaternion_wxyz) || a.quaternion_wxyz.length !== 4 ||
          !a.quaternion_wxyz.every(number) ||
          Math.abs(Math.hypot(...a.quaternion_wxyz) - 1) > 0.02) {
        throw Error("자세 쿼터니안 또는 좌표계가 유효하지 않습니다.");
      }
    }
    return data;
  }
  function renderSensors() {
    if (!selectedTm) return;
    const list = $("sensor-list");
    list.replaceChildren();
    const query = $("sensor-filter").value.trim().toLowerCase();
    const visible = selectedTm.sensors.filter((sensor) =>
      `${sensor.name} ${sensor.group}`.toLowerCase().includes(query));
    if (!visible.includes(selectedSensor)) selectedSensor = visible[0] || null;
    for (const sensor of visible) {
      const sample = sensor.samples.at(-1);
      const breached = sample.value < sensor.lower || sample.value > sensor.upper;
      const button = document.createElement("button");
      button.type = "button";
      button.setAttribute("aria-current", String(sensor === selectedSensor));
      for (const [text, className] of [
        [sensor.name, "sensor-name"], [`${sample.value} ${sensor.unit}`, "sensor-value"],
        [sensor.group, "sensor-group"], [breached ? "범위 초과" : "시험 범위 내", "sensor-state"]
      ]) {
        const span = document.createElement("span");
        span.className = className;
        if (breached && className === "sensor-state") span.classList.add("alert");
        span.textContent = text;
        button.append(span);
      }
      button.addEventListener("click", () => { selectedSensor = sensor; renderSensors(); });
      const row = document.createElement("li");
      row.append(button);
      list.append(row);
    }
    if (!visible.length) {
      const row = document.createElement("li");
      row.className = "sensor-no-match";
      row.textContent = "일치하는 센서가 없습니다.";
      list.append(row);
    }
    renderTrend();
  }
  function renderTrend() {
    const sensor = selectedSensor;
    if (!sensor) return;
    const end = Date.parse(selectedTm.received_utc);
    const samples = sensor.samples.filter((sample) => Date.parse(sample.utc) >= end - minutes * 60000);
    const latest = sensor.samples.at(-1);
    $("selected-sensor").textContent = sensor.name;
    $("latest-value").textContent = `${latest.value} ${sensor.unit}`;
    $("limit-values").textContent = `${sensor.lower} / ${sensor.upper} ${sensor.unit}`;
    $("selected-state").textContent =
      latest.value < sensor.lower || latest.value > sensor.upper ? "범위 초과 · 시험" : "범위 내 · 시험";
    const chart = $("telemetry-chart");
    chart.replaceChildren(svg("path", {class: "chart-grid",
      d: "M40 35H770 M40 95H770 M40 155H770 M40 215H770 M40 35V215 M220 35V215 M400 35V215 M580 35V215 M770 35V215"}));
    if (!samples.length) {
      chart.append(svg("text", {x: 400, y: 134, "text-anchor": "middle", class: "chart-placeholder"},
        "선택한 구간에 시험 표본이 없습니다"));
    } else {
      const spread = Math.max((sensor.upper - sensor.lower) * 0.2, 0.1);
      const bottom = Math.min(sensor.lower - spread, ...samples.map((item) => item.value));
      const top = Math.max(sensor.upper + spread, ...samples.map((item) => item.value));
      const x = (sample) => 40 + (Date.parse(sample.utc) - (end - minutes * 60000)) / (minutes * 60000) * 730;
      const y = (value) => 215 - (value - bottom) / (top - bottom) * 180;
      chart.append(svg("rect", {class: "chart-limit", x: 40, y: y(sensor.upper),
        width: 730, height: y(sensor.lower) - y(sensor.upper)}));
      chart.append(svg("path", {class: "chart-line", d: samples.map((sample, index) =>
        `${index ? "L" : "M"}${x(sample).toFixed(1)} ${y(sample.value).toFixed(1)}`).join(" ")}));
      for (const sample of samples) if (sample.value < sensor.lower || sample.value > sensor.upper) {
        chart.append(svg("circle", {class: "chart-breach", cx: x(sample), cy: y(sample.value), r: 4}));
      }
      chart.append(svg("text", {x: 45, y: 25, fill: "#b8a76d", "font-size": 12},
        `상한 ${sensor.upper} ${sensor.unit}`));
      chart.append(svg("text", {x: 45, y: 235, fill: "#b8a76d", "font-size": 12},
        `하한 ${sensor.lower} ${sensor.unit}`));
    }
    const events = selectedTm.sensors.flatMap((item) => item.samples
      .filter((sample) => Date.parse(sample.utc) >= end - minutes * 60000 &&
        (sample.value < item.lower || sample.value > item.upper))
      .map((sample) => ({sensor: item, sample})))
      .sort((a, b) => b.sample.utc.localeCompare(a.sample.utc));
    $("event-count").textContent = `선택 구간 · 시험 ${events.length}건`;
    const rows = $("alarm-rows");
    rows.replaceChildren();
    if (!events.length) empty(rows, 4, "선택 구간에 시험 한계 초과가 없습니다.");
    for (const event of events.slice(0, 30)) {
      const row = document.createElement("tr");
      cell(row, event.sample.utc);
      cell(row, event.sensor.name);
      cell(row, event.sample.value > event.sensor.upper ? "상한 초과" : "하한 미달");
      cell(row, `${event.sample.value} / ${event.sample.value > event.sensor.upper ?
        event.sensor.upper : event.sensor.lower} ${event.sensor.unit}`);
      rows.append(row);
    }
  }

  // Geometry provider boundary: a future vetted CAD mesh adapter can supply vertices/faces.
  const boxModel = {
    vertices: [[-0.75,-0.4,-1.05],[0.75,-0.4,-1.05],[0.75,0.4,-1.05],[-0.75,0.4,-1.05],
      [-0.75,-0.4,1.05],[0.75,-0.4,1.05],[0.75,0.4,1.05],[-0.75,0.4,1.05]],
    faces: [[0,1,2,3],[4,5,6,7],[0,1,5,4],[2,3,7,6],[1,2,6,5],[0,3,7,4]]
  };
  function renderAttitude(attitude) {
    const canvas = $("attitude-canvas");
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "#091824";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.strokeStyle = "#253f50";
    for (let x = 20; x < 560; x += 36) {
      ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, 310); ctx.stroke();
    }
    if (!attitude) {
      ctx.fillStyle = "#91a8b4"; ctx.font = "15px Malgun Gothic, sans-serif";
      ctx.textAlign = "center"; ctx.fillText("자세 TM 표본 없음", 280, 160);
      return;
    }
    const [w, qx, qy, qz] = attitude.quaternion_wxyz;
    const rotate = ([x,y,z]) => [
      (1-2*(qy*qy+qz*qz))*x + 2*(qx*qy-w*qz)*y + 2*(qx*qz+w*qy)*z,
      2*(qx*qy+w*qz)*x + (1-2*(qx*qx+qz*qz))*y + 2*(qy*qz-w*qx)*z,
      2*(qx*qz-w*qy)*x + 2*(qy*qz+w*qx)*y + (1-2*(qx*qx+qy*qy))*z
    ];
    const project = (v) => {
      const [x,y,z] = rotate(v);
      const cameraX = x*0.82 - y*0.57;
      const cameraY = x*0.3 + y*0.44 - z*0.85;
      const depth = x*0.48 + y*0.69 + z*0.48;
      const scale = 100 / (1 + depth*0.09);
      return {x: 280 + cameraX*scale, y: 155 + cameraY*scale, depth};
    };
    const vertices = boxModel.vertices.map(project);
    const faces = boxModel.faces.map((indices, index) => ({
      points: indices.map((vertex) => vertices[vertex]), index,
      depth: indices.reduce((sum, vertex) => sum + vertices[vertex].depth, 0) / 4
    })).sort((a,b) => b.depth-a.depth);
    const colors = ["#257590","#2b9ba6","#164b64","#1d6378","#315e7e","#287b8d"];
    for (const face of faces) {
      ctx.beginPath();
      ctx.moveTo(face.points[0].x, face.points[0].y);
      face.points.slice(1).forEach((point) => ctx.lineTo(point.x, point.y));
      ctx.closePath(); ctx.fillStyle = colors[face.index]; ctx.fill();
      ctx.lineWidth = 2; ctx.strokeStyle = "#9bd3db"; ctx.stroke();
    }
    ctx.fillStyle = "#e9f5f6"; ctx.font = "bold 14px Consolas, monospace";
    ctx.textAlign = "center"; ctx.fillText("BOX MODEL · TEST TM", 280, 292);
  }
  function loadTm(data, sourceLabel = "수동 시험용 정규화 TM") {
    selectedTm = normalizedTm(data);
    selectedSensor = selectedTm.sensors.find((item) => item.id === selectedSensor?.id) ||
      selectedTm.sensors[0];
    window.rosTestPreviewActive = true;
    $("sensor-empty").hidden = true;
    $("sensor-list").hidden = false;
    $("sensor-filter").disabled = false;
    document.querySelectorAll("[data-range]").forEach((button) => button.disabled = false);
    $("sensor-count").textContent = `${selectedTm.sensors.length}개 · 시험`;
    $("ros-data-state").textContent = "시험 TM 스냅샷";
    $("ros-last-tm").textContent = selectedTm.received_utc;
    $("chart-source").textContent = `데이터 소스: ${sourceLabel} · 실제 위성 TM 아님`;
    $("demo-toggle").textContent = "합성 예시 보기";
    $("attitude-source").textContent = selectedTm.attitude ? "시험 TM · BOX" : "데이터 없음";
    $("attitude-quaternion").textContent = selectedTm.attitude ?
      selectedTm.attitude.quaternion_wxyz.map((x) => x.toFixed(4)).join(", ") : "—";
    $("attitude-frame").textContent = selectedTm.attitude?.frame || "미정";
    $("attitude-time").textContent = selectedTm.attitude?.utc || "없음";
    renderAttitude(selectedTm.attitude);
    renderSensors();
    renderCurrentAlerts();
  }
  function renderCurrentAlerts() {
    const count = $("ros-current-count");
    const list = $("ros-current-alerts");
    list.replaceChildren();
    if (!selectedTm) {
      count.textContent = "입력 대기";
      list.textContent = "시험 TM 입력 대기";
      $("ros-alarm-state").textContent = "평가 불가";
      return;
    }
    if (tmSource === "published" && (!publishedReachable ||
        Date.now() - Date.parse(selectedTm.received_utc) > 5 * 60 * 1000)) {
      count.textContent = "현재 판정 불가";
      list.textContent = publishedReachable ?
        "게시된 시험 TM이 5분 이상 갱신되지 않았습니다." :
        "게시된 시험 TM의 새 입력을 확인할 수 없습니다.";
      $("ros-alarm-state").textContent = "현재 판정 불가";
      return;
    }
    const breached = selectedTm.sensors.filter((sensor) => {
      const value = sensor.samples.at(-1).value;
      return value < sensor.lower || value > sensor.upper;
    });
    const label = tmSource === "published" ? "게시 시험 표본" : "불러온 시험 표본";
    count.textContent = `${label} · ${breached.length}개 초과`;
    $("ros-alarm-state").textContent = breached.length ?
      `시험 표본 초과 ${breached.length}개` : "시험 표본 범위 내";
    if (!breached.length) {
      list.textContent = "최신 표본에서 시험 한계를 벗어난 센서가 없습니다.";
      return;
    }
    for (const sensor of breached) {
      const value = sensor.samples.at(-1).value;
      const row = document.createElement("button");
      row.type = "button";
      row.className = "current-alert-row";
      const name = document.createElement("strong");
      name.textContent = `${sensor.group} · ${sensor.name}`;
      const detail = document.createElement("span");
      detail.textContent = `${value} ${sensor.unit} · ${value > sensor.upper ?
        "상한" : "하한"} ${value > sensor.upper ? sensor.upper : sensor.lower} ${sensor.unit}`;
      row.append(name, detail);
      row.addEventListener("click", () => {
        $("sensor-filter").value = "";
        selectedSensor = sensor;
        renderSensors();
        activateTab("telemetry");
      });
      list.append(row);
    }
  }
  function markPublishedAge() {
    if (tmSource !== "published" || !selectedTm) return;
    const old = Date.now() - Date.parse(selectedTm.received_utc) > 5 * 60 * 1000;
    $("ros-data-state").textContent = old ? "게시된 시험 TM 오래됨" : "게시된 시험 TM";
    if (old) $("chart-source").textContent =
      "데이터 소스: 게시된 시험 TM · 5분 이상 갱신 없음 · 실제 위성 TM 아님";
    renderCurrentAlerts();
  }
  async function refreshPublishedTm() {
    if (tmSource !== "published" || previewPending) return;
    previewPending = true;
    try {
      const snapshot = await api("/api/ros/test-tm-snapshot");
      if (tmSource !== "published") return;
      const recovered = !publishedReachable;
      publishedReachable = true;
      const raw = JSON.stringify(snapshot);
      if (raw !== lastPublishedRaw) {
        loadTm(snapshot, "게시된 시험용 정규화 TM");
        lastPublishedRaw = raw;
      } else if (recovered) {
        renderTrend();
      }
      markPublishedAge();
    } catch (_) {
      if (tmSource === "published") {
        publishedReachable = false;
        $("ros-data-state").textContent = "시험 TM 입력 대기";
        $("chart-source").textContent = selectedTm ?
          "데이터 소스: 이전 시험 TM · 새 입력 확인 불가" : "데이터 소스: 게시된 시험 TM 없음";
        $("selected-state").textContent = "현재 값 확인 불가";
        renderCurrentAlerts();
      }
    } finally { previewPending = false; }
  }
  function syntheticTm() {
    const end = Date.now();
    const stamp = (offset) => new Date(end + offset * 60000).toISOString().slice(0, 19) + "Z";
    const sensor = (id, name, group, unit, lower, upper, center, spread) => ({
      id, name, group, unit, lower, upper,
      samples: Array.from({length: 31}, (_, index) => ({
        utc: stamp(index - 30),
        value: Number((center + Math.sin(index * 0.38) * spread +
          (id === "temp" && index > 22 && index < 27 ? 13 : 0)).toFixed(2))
      }))
    });
    return {type: "TEST_ONLY_TM_SNAPSHOT", non_operational: true, received_utc: stamp(0),
      sensors: [sensor("bus_v", "버스 전압 예시", "전원", "V", 25, 31, 28, 1.2),
        sensor("temp", "온도 예시", "열", "°C", 12, 36, 25, 4.5),
        sensor("rate", "각속도 예시", "자세", "°/s", -3, 3, 0, 1.3)],
      attitude: {utc: stamp(0), frame: "TEST_FRAME", quaternion_wxyz: [0.9238795,0,0.3826834,0]}};
  }
  async function init() {
    setupTabs();
    renderAttitude(null);
    const publishedButton = document.createElement("button");
    publishedButton.type = "button";
    publishedButton.className = "outline";
    publishedButton.textContent = "게시된 시험 TM 보기";
    publishedButton.addEventListener("click", () => {
      tmSource = "published";
      lastPublishedRaw = "";
      refreshPublishedTm();
    });
    $("demo-toggle").parentElement.prepend(publishedButton);
    $("demo-toggle").addEventListener("click", () => {
      tmSource = "example";
      loadTm(syntheticTm(), "합성 예시");
    });
    $("tm-file").addEventListener("change", async (event) => {
      const file = event.target.files[0];
      if (!file || file.size > 1024 * 1024) { showError("시험 TM JSON은 1 MiB 이하여야 합니다."); return; }
      const previousSource = tmSource;
      try {
        tmSource = "manual";
        loadTm(JSON.parse(await file.text()));
        $("api-error").hidden = true;
      }
      catch (error) {
        tmSource = previousSource;
        showError(`시험 TM 불러오기 실패: ${error.message}`);
      }
    });
    $("sensor-filter").addEventListener("input", renderSensors);
    document.querySelectorAll("[data-range]").forEach((button) => button.addEventListener("click", () => {
      minutes = Number(button.dataset.range);
      document.querySelectorAll("[data-range]").forEach((item) =>
        item.setAttribute("aria-pressed", String(item === button)));
      renderTrend();
    }));
    $("sequence-select").addEventListener("change", showSequence);
    $("reservation-step").addEventListener("change", setReservationDefault);
    $("sequence-import").addEventListener("click", importSequence);
    $("reservation-form").addEventListener("submit", reserve);
    try {
      session = await api("/api/session");
      try { await refreshDrafts(); }
      catch (error) { message("sequence-message", `ROS 초안 저장소 연결 실패: ${error.message}`, true); }
      if (session.role === "auditor") {
        $("sequence-import").disabled = true;
        $("reservation-form").querySelector("button").disabled = true;
      }
    } catch (error) { message("sequence-message", `ROS 초안 저장소 연결 실패: ${error.message}`, true); }
    await refreshPublishedTm();
    setInterval(() => {
      if (document.visibilityState === "visible") refreshPublishedTm();
    }, 5000);
  }
  init();
})();
