"use strict";

// Simulation commands remain separate from the legacy draft and flight TC paths.
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
  let simSequences = [];
  let simReservations = [];
  let simTmRequests = [];
  let simTmAvailable = false;
  let simRequestPending = false;
  let simSequencePending = false;
  const simCancellationPending = new Set();
  let selectedTm = null;
  let selectedSensor = null;
  let minutes = 30;
  let tmSource = "published";
  let lastPublishedRaw = "";
  let previewPending = false;
  let publishedReachable = false;
  let attitudeViewer = null;
  let attitudeOrbitView = null;
  let attitudeOrbitPending = false;
  let historyController = null;
  let historyCoverageKey = "";
  let packetBrowser = null;
  let spacecraftEventsPending = false;
  const CURRENT_SAMPLE_AGE_MS = 120_000;

  function freshSample(sample, now = Date.now()) {
    const age = now - Date.parse(sample.utc);
    return Number.isFinite(age) && age >= -30_000 && age <= CURRENT_SAMPLE_AGE_MS;
  }

  function tmTiming(snapshot, now = Date.now()) {
    const latestUtc = snapshot.sensors.map((sensor) => sensor.samples.at(-1).utc)
      .sort().at(-1);
    const latestMs = Date.parse(latestUtc);
    const receivedMs = Date.parse(snapshot.received_utc);
    return {latestUtc, delayed: receivedMs - latestMs > CURRENT_SAMPLE_AGE_MS,
      stale: !freshSample({utc: latestUtc}, now)};
  }

  function showTmTiming(snapshot, now = Date.now()) {
    const state = tmTiming(snapshot, now);
    $("ros-tm-timing").textContent =
      `수신 UTC ${snapshot.received_utc} · 최근 계측 UTC ${state.latestUtc}` +
      (state.delayed ? " · 과거 측정 TM 포함" :
        state.stale ? " · 현재 계측으로 판정하지 않음" : "");
    return state;
  }

  function activateTab(name, focus = false) {
    const tabs = [...document.querySelectorAll("[data-ros-target]")];
    for (const tab of tabs) {
      const active = tab.dataset.rosTarget === name;
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
      $("ros-view-" + tab.dataset.rosTarget).hidden = !active;
      if (active && focus) tab.focus();
    }
    if (name === "telemetry") packetBrowser?.refresh();
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
      cell(row, `+${step.offset_s}초 · ${utc(sequence.aos_utc_s + step.offset_s)}`);
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

  function selectedSimSequence() {
    return simSequences.find((item) => item.sha256 === $("sim-sequence-select").value);
  }
  function showSimSequence() {
    const sequence = selectedSimSequence();
    const rows = $("sim-sequence-steps");
    const controls = $("sim-sequence-controls");
    rows.replaceChildren(); controls.replaceChildren();
    if (!sequence) {
      empty(rows, 3, "MPS에서 전달한 시험 시퀀스가 없습니다.");
      $("sim-sequence-pass").textContent = "패스 미선택";
      $("sim-sequence-timing").textContent = "시퀀스 시작 시각 미정";
      return;
    }
    $("sim-sequence-pass").textContent =
      `${sequence.pass_id} · ${utc(sequence.aos_utc_s)} → ${utc(sequence.los_utc_s)}`;
    const offsets = new Set(sequence.steps.map(step => step.offset_s));
    const shared = offsets.size === 1;
    const offset = sequence.steps[0]?.offset_s;
    const target = sequence.aos_utc_s + offset;
    $("sim-sequence-timing").textContent = shared ?
      `시퀀스 전체 · AOS ${offset}초 후 · ${utc(target)} UTC` :
      "TC별 시각이 다른 기존 초안입니다. MPS에서 전체 시작 시각을 정해 다시 저장·전달하세요.";
    for (const step of sequence.steps) {
      const row = document.createElement("tr");
      cell(row, step.step_id);
      cell(row, step.command_name);
      cell(row, Object.entries(step.parameters).map(([key, value]) => `${key}=${value}`).join(", ") || "없음");
      rows.append(row);
    }
    const already = simReservations.some(item => item.sha256 === sequence.sha256 && item.status !== "CANCELLED");
    const button = document.createElement("button");
    button.type = "button"; button.className = "outline"; button.id = "sim-sequence-reserve";
    button.textContent = already ? "시퀀스 예약 기록 있음" : "시퀀스 전체 예약";
    button.disabled = already || simSequencePending || !shared || session?.role === "auditor" ||
      !sequence.steps.length || target <= Math.floor(Date.now() / 1000);
    button.addEventListener("click", async () => {
      if (simSequencePending) return;
      simSequencePending = true; showSimSequence();
      try {
        const result = await api("/api/ros/test-sim-reservations", {
          method: "POST", headers: {"Content-Type": "application/json", "X-CSRF-Token": session.csrf},
          body: JSON.stringify({sha256: sequence.sha256})
        });
        await refreshSimDrafts(sequence.sha256);
        message("sim-sequence-message", `TC ${result.count}개를 한 시퀀스로 예약했습니다. AOS ${offset}초 후 · ${utc(result.target_utc_s)} UTC부터 순서대로 전송합니다.`);
      } catch (error) {
        message("sim-sequence-message", `시퀀스 예약 실패: ${error.message}`, true);
      } finally { simSequencePending = false; showSimSequence(); }
    });
    const note = document.createElement("small");
    note.textContent = "앞선 TC의 ACK 수신 후 다음 TC를 전송합니다. 실패하면 남은 TC를 중단합니다.";
    controls.append(button, note);
  }
  function showSimReservations() {
    const rows = $("sim-reservation-rows");
    rows.replaceChildren();
    if (!simReservations.length) {
      empty(rows, 5, "가상 위성 시험 예약이 없습니다.");
      return;
    }
    const statusText = {
      SCHEDULED: "예약 대기", SENDING: "가상 위성 전송 중", ACKED: "ACK 수신",
      REJECTED: "거절", TIMEOUT: "ACK 시간 초과", SEND_FAILED: "전송 실패",
      MISSED: "예약 시각 경과", UNKNOWN: "결과 불명", CANCELLED: "취소"
    };
    const cancelShown = new Set();
    const ordered = [...simReservations].sort((a, b) => {
      if (a.batch_id != null && a.batch_id === b.batch_id) return a.step_id - b.step_id;
      return b.id - a.id;
    });
    for (const item of ordered) {
      const row = document.createElement("tr");
      cell(row, item.status === "CANCELLED" && item.blocked_by_step_id ?
        `TC ${item.blocked_by_step_id} 이후 중단` : statusText[item.status] || item.status);
      cell(row, item.batch_id != null ? `${item.step_id}/${item.batch_size} · ${item.command_name}` : item.command_name);
      cell(row, `${Number.isInteger(item.start_offset_s) ? `AOS ${item.start_offset_s}초 후 · ` : ""}${utc(item.target_utc_s)}`);
      const detail = typeof item.ack === "string" ? item.ack :
        item.ack ? JSON.stringify(item.ack) : item.batch_stop_reason ? "시퀀스 중단" : "—";
      cell(row, detail.length > 180 ? detail.slice(0, 180) + "…" : detail);
      const control = document.createElement("td");
      const key = item.batch_id != null ? `batch:${item.batch_id}` : `row:${item.id}`;
      if (item.status === "SCHEDULED" && session?.role !== "auditor" && !cancelShown.has(key)) {
        cancelShown.add(key);
        const button = document.createElement("button");
        button.type = "button"; button.className = "outline";
        button.textContent = item.batch_id != null ? "남은 시퀀스 취소" : "예약 취소";
        button.disabled = simCancellationPending.has(key);
        button.addEventListener("click", async () => {
          simCancellationPending.add(key); button.disabled = true;
          try {
            await api("/api/ros/test-sim-reservations/cancel", {
              method: "POST", headers: {"Content-Type": "application/json", "X-CSRF-Token": session.csrf},
              body: JSON.stringify(item.batch_id != null ? {batch_id: item.batch_id} : {id: item.id})
            });
            await refreshSimDrafts();
            message("sim-reservation-message", item.batch_id != null ? "남은 시퀀스 예약을 취소했습니다." : "가상 위성 시험 예약을 취소했습니다.");
          } catch (error) {
            message("sim-reservation-message", `취소 실패: ${error.message}`, true);
          } finally { simCancellationPending.delete(key); showSimReservations(); }
        });
        control.append(button);
      }
      row.append(control); rows.append(row);
    }
  }
  function showSimTmRequests() {
    const rows = $("sim-tm-request-rows");
    rows.replaceChildren();
    if (!simTmRequests.length) empty(rows, 4, "저장 TM 요청이 없습니다.");
    const states = {QUEUED: "전송 대기", SENDING: "명령 전송 중", ACKED: "요청 수락 · ACK 수신",
      REJECTED: "거절", TIMEOUT: "ACK 시간 초과", SEND_FAILED: "전송 실패 · 교신 상태 확인",
      MISSED: "요청 시각 경과", UNKNOWN: "결과 불명 · 자동 재전송 안 함"};
    const reasons = {1: "지원하지 않는 명령", 2: "예약 TC 미지원", 3: "중복 명령",
      4: "오래된 명령", 5: "명령 인자 오류", 6: "처리기 오류"};
    for (const item of simTmRequests) {
      const row = document.createElement("tr");
      cell(row, utc(item.created_at)); cell(row, item.created_by);
      cell(row, states[item.status] || item.status);
      cell(row, item.ack ? (item.ack.result === 0 ? "hk_dump 수락 · 자료 내려받기 시작" :
        reasons[item.ack.reason] || `거절 사유 ${item.ack.reason}`) : "—");
      rows.append(row);
    }
    $("sim-tm-request").disabled = !session || session.role === "auditor" || !simTmAvailable ||
      simRequestPending || simTmRequests.some((item) => ["QUEUED", "SENDING"].includes(item.status));
  }
  async function refreshSimTmRequests() {
    const data = await api("/api/ros/test-sim-tm-requests");
    simTmRequests = data.items; simTmAvailable = data.sim_tc_available === true;
    showSimTmRequests();
  }
  async function requestSimTm() {
    if (!session || simRequestPending) return;
    simRequestPending = true; showSimTmRequests();
    try {
      await api("/api/ros/test-sim-tm-requests", {method: "POST",
        headers: {"Content-Type": "application/json", "X-CSRF-Token": session.csrf},
        body: "{}"});
      message("sim-tm-request-message", "저장 TM 요청을 등록했습니다. 교신 중인 가상 위성의 ACK를 기다립니다.");
      await refreshSimTmRequests();
    } catch (error) {
      message("sim-tm-request-message", `저장 TM 요청 실패: ${error.message}`, true);
    } finally { simRequestPending = false; showSimTmRequests(); }
  }
  async function refreshBeacon() {
    try {
      const beacon = await api("/api/ros/test-beacon");
      const age = Date.now() - Date.parse(beacon.received_utc);
      const fresh = age >= 0 && age <= Math.max(15, 3 * beacon.fields.beacon_period_s) * 1000;
      $("sim-beacon-state").textContent = fresh ? "비콘 수신 중" : "최근 비콘 오래됨";
      $("sim-beacon-summary").textContent = `비콘 #${beacon.fields.beacon_counter} · 주기 ${beacon.fields.beacon_period_s}초 · 모드 ${beacon.fields.mode}`;
      $("sim-beacon-time").textContent = `측정 UTC ${beacon.sampled_utc} · 수신 UTC ${beacon.received_utc}`;
    } catch (_) {
      $("sim-beacon-state").textContent = "비콘 대기";
      $("sim-beacon-summary").textContent = "최근 비콘을 확인할 수 없습니다";
      $("sim-beacon-time").textContent = "교신 구간과 비콘 수신 상태를 확인하세요.";
    }
    $("overview-beacon-state").textContent = $("sim-beacon-state").textContent;
    $("overview-beacon-summary").textContent = $("sim-beacon-summary").textContent;
    $("overview-beacon-time").textContent = $("sim-beacon-time").textContent;
  }

  async function refreshSpacecraftEvents() {
    if (spacecraftEventsPending) return;
    spacecraftEventsPending = true;
    try {
      const result = await api("/api/ros/tm-events?limit=30");
      if (!Array.isArray(result.items) || result.items.length > 30) throw Error("invalid events");
      const rows = $("spacecraft-event-rows");
      rows.replaceChildren();
      const events = result.items.filter((event) => event.fields?.level > 0 || event.fields?.fault_code > 0);
      $("spacecraft-event-count").textContent = `최근 ${events.length}건`;
      if (!events.length) empty(rows, 5, "수신한 FDIR 이벤트가 없습니다.");
      for (const event of events) {
        const row = document.createElement("tr");
        cell(row, event.sampled_utc); cell(row, `${event.label} · ${event.name}`);
        cell(row, event.fields?.level ?? "—");
        cell(row, event.fields?.source_node ?? "—");
        cell(row, `${event.fields?.fault_code ?? "—"} / ${event.fields?.action_code ?? "—"}`);
        rows.append(row);
      }
    } catch (_) {
      $("spacecraft-event-count").textContent = "조회 대기";
    } finally { spacecraftEventsPending = false; }
  }
  async function refreshSimDrafts(selectDigest = "") {
    const [sequenceData, reservationData] = await Promise.all([
      api("/api/ros/test-sim-sequences"), api("/api/ros/test-sim-reservations")
    ]);
    simSequences = sequenceData.items;
    simReservations = reservationData.items;
    const picker = $("sim-sequence-select");
    const keep = selectDigest || picker.value;
    picker.replaceChildren(new Option("없음", ""));
    for (const item of simSequences) {
      picker.add(new Option(`${item.pass_id} · r${item.revision} · ${item.sha256.slice(0, 10)}`, item.sha256));
    }
    if (simSequences.some((item) => item.sha256 === keep)) picker.value = keep;
    else if (simSequences.length) picker.value = simSequences[0].sha256;
    showSimSequence();
    showSimReservations();
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
  function renderPowerSummary() {
    const byId = new Map((selectedTm?.sensors || []).map(sensor => [sensor.id, sensor.samples.at(-1)]));
    const fields = [["eps-soc", "battery_soc", "%"], ["eps-voltage", "p60_voltage", "V"],
      ["eps-solar", "solar_bus_power", "W"], ["eps-load", "equipment_load", "W"],
      ["eps-net", "battery_net_power", "W"], ["eps-energy", "battery_energy", "Wh"]];
    for (const [element, id, unit] of fields) {
      const sample = byId.get(id);
      $(element).textContent = sample ? `${sample.value.toFixed(2)} ${unit}` : "—";
    }
    const latest = byId.get("battery_soc");
    const net = byId.get("battery_net_power");
    $("eps-sample-state").textContent = latest ?
      `${freshSample(latest, Date.now()) ? "최근 측정" : "과거 측정"} · ${latest.utc} · ${
        !net ? "상태 대기" : net.value > 0.01 ? "충전 중" : net.value < -0.01 ? "방전 중" : "충방전 없음"}` :
      "전력 TM 수신 대기 · 저장 TM 요청 후 값을 표시합니다";
  }
  const plotInspectors = new WeakMap();
  function measurementUtc(value) {
    return new Date(value).toISOString().slice(0, 19).replace("T", " ") + " UTC";
  }
  function clockUtc(milliseconds) {
    return new Date(milliseconds).toISOString().slice(11, 19);
  }
  function plotDateContext(start, end) {
    const first = new Date(start).toISOString().slice(0, 10);
    const last = new Date(end).toISOString().slice(0, 10);
    return `${first === last ? first : `${first} → ${last}`} UTC`;
  }
  function nearestSampleIndex(samples, target) {
    let low = 0, high = samples.length - 1;
    while (low < high) {
      const middle = Math.floor((low + high) / 2);
      if (Date.parse(samples[middle].utc) < target) low = middle + 1;
      else high = middle;
    }
    if (!low) return 0;
    return target - Date.parse(samples[low - 1].utc) <= Date.parse(samples[low].utc) - target ? low - 1 : low;
  }
  function installSampleInspector(chart, sensor, samples, geometry, readout = null) {
    const previous = plotInspectors.get(chart);
    previous?.dispose();
    if (!samples.length || typeof chart.addEventListener !== "function") return;
    const {width, left, right, top, bottom, start, end, x, y, keyboard = false} = geometry;
    const compact = width < 400;
    const layer = svg("g", {class: "sample-inspection-layer", visibility: "hidden", "pointer-events": "none"});
    const line = svg("line", {class: "sample-crosshair", y1: top, y2: bottom});
    const marker = svg("circle", {class: "sample-hover-dot", r: compact ? 3 : 4});
    const boxWidth = compact ? width - 12 : 300, boxHeight = compact ? 29 : 42;
    const box = svg("rect", {class: "sample-tooltip-box", width: boxWidth, height: boxHeight, rx: 3});
    const stamp = svg("text", {class: "sample-tooltip-time", "font-size": compact ? 9 : 12});
    const value = svg("text", {class: "sample-tooltip-value", "font-size": compact ? 9 : 12});
    layer.append(line, marker, box, stamp, value); chart.append(layer);
    let index = samples.length - 1;
    if (previous?.sampleUtc && previous.sensorId === sensor.id) index = nearestSampleIndex(samples, Date.parse(previous.sampleUtc));
    let focused = keyboard && document.activeElement === chart;
    let visibleIndex = null;
    const state = {sampleUtc: samples[index].utc, sensorId: sensor.id, dispose() {}};
    const show = next => {
      index = Math.max(0, Math.min(samples.length - 1, next));
      if (index === visibleIndex) return;
      visibleIndex = index;
      const sample = samples[index], px = x(sample), py = y(sample.value);
      state.sampleUtc = sample.utc;
      line.setAttribute("x1", px); line.setAttribute("x2", px);
      marker.setAttribute("cx", px); marker.setAttribute("cy", py);
      const boxX = Math.max(6, Math.min(width - boxWidth - 6, px - boxWidth / 2));
      const boxY = Math.max(top + 2, Math.min(bottom - boxHeight - 2, py - boxHeight - 8));
      box.setAttribute("x", boxX); box.setAttribute("y", boxY);
      stamp.setAttribute("x", boxX + 7); stamp.setAttribute("y", boxY + (compact ? 11 : 16));
      value.setAttribute("x", boxX + 7); value.setAttribute("y", boxY + (compact ? 23 : 32));
      stamp.textContent = measurementUtc(sample.utc);
      value.textContent = `${sample.value} ${sensor.unit}`;
      layer.setAttribute("visibility", "visible");
      if (readout) readout.textContent = `측정 ${measurementUtc(sample.utc)} · ${sample.value} ${sensor.unit} · 표본 ${index + 1}/${samples.length}`;
    };
    const hide = () => { if (!focused) { layer.setAttribute("visibility", "hidden"); visibleIndex = null; } };
    const move = event => {
      const bounds = chart.getBoundingClientRect();
      if (!bounds.width) return;
      const px = Math.max(left, Math.min(right, (event.clientX - bounds.left) * width / bounds.width));
      const target = start + (px - left) / (right - left) * (end - start);
      show(nearestSampleIndex(samples, target));
    };
    const focus = () => { focused = true; show(index); };
    const blur = () => { focused = false; hide(); };
    const keydown = event => {
      const next = event.key === "ArrowLeft" ? index - 1 : event.key === "ArrowRight" ? index + 1 :
        event.key === "Home" ? 0 : event.key === "End" ? samples.length - 1 : null;
      if (next !== null) { event.preventDefault(); show(next); }
      else if (event.key === "Escape") { layer.setAttribute("visibility", "hidden"); visibleIndex = null; }
    };
    const listeners = [["pointermove", move], ["pointerleave", hide]];
    if (keyboard) listeners.push(["focus", focus], ["blur", blur], ["keydown", keydown]);
    for (const [name, listener] of listeners) chart.addEventListener(name, listener);
    state.dispose = () => {
      for (const [name, listener] of listeners) chart.removeEventListener?.(name, listener);
    };
    plotInspectors.set(chart, state);
    if (readout) readout.textContent = `최근 측정 ${measurementUtc(samples.at(-1).utc)} · ${samples.at(-1).value} ${sensor.unit}`;
    if (focused) show(index);
  }
  function renderSensors() {
    renderPowerSummary();
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
      const current = freshSample(sample);
      const button = document.createElement("button");
      button.type = "button";
      button.setAttribute("aria-current", String(sensor === selectedSensor));
      for (const [text, className] of [
        [sensor.name, "sensor-name"], [`${sample.value} ${sensor.unit}`, "sensor-value"],
        [sensor.group, "sensor-group"],
        [current ? (breached ? "범위 초과" : "시험 범위 내") :
          (breached ? "과거 측정 · 범위 초과" : "과거 측정"), "sensor-state"]
      ]) {
        const span = document.createElement("span");
        span.className = className;
        if (breached && current && className === "sensor-state") span.classList.add("alert");
        span.textContent = text;
        button.append(span);
      }
      const measured = document.createElement("time");
      measured.className = "sensor-measured-time"; measured.dateTime = sample.utc;
      measured.textContent = `측정 ${measurementUtc(sample.utc)}`;
      button.append(measured);
      button.append(sensorSparkline(sensor));
      button.addEventListener("click", () => {
        selectedSensor = sensor;
        $("trend-details").open = true;
        renderSensors();
      });
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
  function sensorSparkline(sensor) {
    const latest = sensor.samples.at(-1);
    const end = Date.parse(latest.utc);
    const samples = sensor.samples.filter((sample) => Date.parse(sample.utc) >= end - minutes * 60000);
    // Preserve bucket extrema so narrow limit excursions remain visible while
    // bounding the SVG work for an entire equipment dashboard.
    let plotted = samples;
    if (samples.length > 120) {
      const stride = Math.ceil(samples.length / 50);
      const selected = new Set([0, samples.length - 1]);
      for (let first = 0; first < samples.length; first += stride) {
        let low = first, high = first;
        for (let index = first + 1; index < Math.min(samples.length, first + stride); index++) {
          if (samples[index].value < samples[low].value) low = index;
          if (samples[index].value > samples[high].value) high = index;
        }
        selected.add(low); selected.add(high);
      }
      plotted = [...selected].sort((a, b) => a - b).map((index) => samples[index]);
    }
    const start = end - minutes * 60000;
    const chart = svg("svg", {viewBox: "0 0 300 115", class: "sensor-sparkline",
      role: "img", "aria-label": `${sensor.name} 측정 추세 · ${plotDateContext(start, end)}`, preserveAspectRatio: "none"});
    const margin = Math.max((sensor.upper - sensor.lower) * 0.15, 0.1);
    const bottom = Math.min(sensor.lower - margin, ...samples.map((item) => item.value));
    const top = Math.max(sensor.upper + margin, ...samples.map((item) => item.value));
    const x = (sample) => 5 + (Date.parse(sample.utc) - start) / (end - start) * 290;
    const y = (value) => 73 - (value - bottom) / (top - bottom) * 66;
    chart.append(svg("rect", {class: "sparkline-limit", x: 5, y: y(sensor.upper),
      width: 290, height: y(sensor.lower) - y(sensor.upper)}));
    chart.append(svg("path", {class: "sparkline-grid", d: "M5 40H295 M5 73H295"}));
    chart.append(svg("path", {class: "sparkline-value", d: plotted.map((sample, index) =>
      `${index ? "L" : "M"}${x(sample).toFixed(1)} ${y(sample.value).toFixed(1)}`).join(" ")}));
    for (const sample of plotted) if (sample.value < sensor.lower || sample.value > sensor.upper)
      chart.append(svg("circle", {class: "chart-breach", cx: x(sample), cy: y(sample.value), r: 2.5}));
    [0, .5, 1].forEach(fraction => chart.append(svg("text", {class: "sparkline-time-tick",
      x: 5 + fraction * 290, y: 89, "text-anchor": fraction === 0 ? "start" : fraction === 1 ? "end" : "middle"},
      clockUtc(start + fraction * (end - start)))));
    chart.append(svg("text", {class: "sparkline-time-context", x: 5, y: 108}, plotDateContext(start, end)));
    installSampleInspector(chart, sensor, samples, {width: 300, left: 5, right: 295, top: 7, bottom: 73,
      start, end, x, y});
    return chart;
  }
  function renderTrend() {
    const sensor = selectedSensor;
    if (!sensor) {
      const chart = $("telemetry-chart");
      plotInspectors.get(chart)?.dispose(); plotInspectors.delete(chart);
      chart.setAttribute("aria-label", "선택한 센서 없음");
      chart.replaceChildren(svg("text", {x: 400, y: 134, "text-anchor": "middle", class: "chart-placeholder"},
        "센서를 선택하면 측정 시각과 값을 표시합니다"));
      $("selected-sensor").textContent = "선택 안 됨";
      $("latest-value").textContent = "—"; $("limit-values").textContent = "—";
      $("selected-state").textContent = "평가 불가"; $("selected-measured-time").textContent = "—";
      $("chart-time-range").textContent = "측정 시간축 UTC · 센서 선택 대기";
      $("chart-inspection").textContent = "선택한 센서가 없습니다";
      return;
    }
    const latest = sensor.samples.at(-1);
    // A batch received during contact may contain older measurements. Chart
    // time follows the measurement window, never the later downlink time.
    const end = Date.parse(latest.utc);
    const start = end - minutes * 60000;
    const samples = sensor.samples.filter((sample) => Date.parse(sample.utc) >= start);
    $("selected-sensor").textContent = sensor.name;
    $("latest-value").textContent = `${latest.value} ${sensor.unit}`;
    $("selected-measured-time").textContent = measurementUtc(latest.utc);
    $("limit-values").textContent = `${sensor.lower} / ${sensor.upper} ${sensor.unit}`;
    $("selected-state").textContent = freshSample(latest) ?
      (latest.value < sensor.lower || latest.value > sensor.upper ?
        "범위 초과 · 시험" : "범위 내 · 시험") :
      "과거 측정 · 현재 판정 불가";
    const chart = $("telemetry-chart");
    chart.setAttribute("viewBox", "0 0 800 280");
    chart.setAttribute("aria-label", `${sensor.name} UTC 측정 추세. 좌우 방향키로 실제 표본을 선택합니다.`);
    $("chart-time-range").textContent = `측정 시간축 · ${measurementUtc(start)} → ${measurementUtc(end)}`;
    chart.replaceChildren(svg("path", {class: "chart-grid",
      d: "M40 35H770 M40 95H770 M40 155H770 M40 215H770 M40 35V215 M222.5 35V215 M405 35V215 M587.5 35V215 M770 35V215"}));
    if (!samples.length) {
      chart.append(svg("text", {x: 400, y: 134, "text-anchor": "middle", class: "chart-placeholder"},
        "선택한 구간에 시험 표본이 없습니다"));
    } else {
      const spread = Math.max((sensor.upper - sensor.lower) * 0.2, 0.1);
      const bottom = Math.min(sensor.lower - spread, ...samples.map((item) => item.value));
      const top = Math.max(sensor.upper + spread, ...samples.map((item) => item.value));
      const x = (sample) => 40 + (Date.parse(sample.utc) - start) / (end - start) * 730;
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
      [0, .25, .5, .75, 1].forEach(fraction => chart.append(svg("text", {class: "chart-time-tick",
        x: 40 + fraction * 730, y: 258, "text-anchor": fraction === 0 ? "start" : fraction === 1 ? "end" : "middle"},
        clockUtc(start + fraction * (end - start)))));
      installSampleInspector(chart, sensor, samples, {width: 800, left: 40, right: 770, top: 35, bottom: 215,
        start, end, x, y, keyboard: true}, $("chart-inspection"));
    }
    const eventEnd = Date.parse(tmTiming(selectedTm).latestUtc);
    const events = selectedTm.sensors.flatMap((item) => item.samples
      .filter((sample) => Date.parse(sample.utc) >= eventEnd - minutes * 60000 &&
        Date.parse(sample.utc) <= eventEnd &&
        (sample.value < item.lower || sample.value > item.upper))
      .map((sample) => ({sensor: item, sample})))
      .sort((a, b) => b.sample.utc.localeCompare(a.sample.utc));
    $("event-count").textContent =
      `${tmTiming(selectedTm).stale ? "과거 측정" : "선택"} 구간 · 시험 ${events.length}건`;
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

  function drawHistoryCoverage(state) {
    const key = `${state.cacheRevision}:${Math.floor(state.endMs/60000)}`;
    if (historyCoverageKey === key || !historyController) return;
    historyCoverageKey = key;
    const canvas = $("state-history-coverage"), context = canvas.getContext("2d");
    if (!context) return;
    context.fillStyle = "#17303d"; context.fillRect(0,0,canvas.width,canvas.height);
    context.fillStyle = "#80cdb5";
    for (const [first,last] of historyController.cache.coverage()) {
      const x = Math.max(0,(first-state.startMs)/(state.endMs-state.startMs)*canvas.width);
      const end = Math.min(canvas.width,(last-state.startMs)/(state.endMs-state.startMs)*canvas.width);
      context.fillRect(x,2,Math.max(1,end-x),canvas.height-4);
    }
  }
  function renderHistoryState(state) {
    $("state-history-slider").min = String(Math.floor(state.startMs/1000));
    $("state-history-slider").max = String(Math.floor(state.endMs/1000));
    $("state-history-slider").value = String(Math.floor(state.selectedMs/1000));
    $("state-history-selected").textContent = measurementUtc(state.selectedMs);
    $("state-history-selected").dateTime = state.utc;
    $("state-history-slider").setAttribute("aria-valuetext", measurementUtc(state.selectedMs));
    $("state-history-start").textContent = measurementUtc(state.startMs);
    $("state-history-end").textContent = measurementUtc(state.endMs);
    $("state-history-live").setAttribute("aria-pressed",String(state.live));
    $("state-history-play").setAttribute("aria-pressed",String(state.playing));
    $("state-history-play").textContent = state.playing ? "일시정지" : "재생";
    $("state-history-previous").disabled=state.previousSampleMs===null;
    $("state-history-next").disabled=state.nextSampleMs===null;
    $("state-history-mode").textContent = state.live ? "현재 시각" : state.playing ? "과거 이력 재생 중" : "과거 시각 선택";
    $("state-history-status").textContent = `${state.loading ? "이력 읽는 중" : state.error || "수신 이력 반영"}${
      state.backfilled ? ` · 지연 수신 표본 ${state.backfilled}건 반영` : ""}${state.truncated ? " · 표본 상한으로 일부 구간 제외" : ""}`;
    drawHistoryCoverage(state);
    if (tmSource !== "published") {
      $("state-history-status").textContent = "수동 시험 자료 표시 중 · 현재 버튼이나 이력 바를 선택하면 수신 이력으로 돌아갑니다.";
      return;
    }
    const sourceLabels = {GNSS_TM:"GNSS 측정",GNSS_INTERPOLATED:"GNSS 표본 사이 보간",
      FDS_TLE:"FDS · TLE 예측",FDS_GNSS:"FDS · GNSS 수치 전파",FDS_MIXED:"FDS 예측 경계 보간",UNKNOWN:"위치 정보 없음"};
    $("state-history-position").textContent = state.position_itrf_m ?
      state.position_itrf_m.map(value => value.toFixed(0)).join(", ") : "위치 정보 없음";
    $("state-history-position-source").textContent = `${sourceLabels[state.positionSource] || state.positionSource}${
      state.positionReceivedUtc ? ` · 수신 ${measurementUtc(state.positionReceivedUtc)}` : ""}`;
    const labels = {MEASURED:"수신 자세 TM",INTERPOLATED:"자세 TM 사이 보간",RECENT_SAMPLE:"최근 수신 자세 TM",UNKNOWN:"자세 정보 없음"};
    $("attitude-source").textContent = (labels[state.attitudeStatus] || "자세 정보 없음") +
      (state.attitudeStatus === "RECENT_SAMPLE" ? ` · ${Math.max(0,(state.selectedMs-Date.parse(state.attitude.utc))/1000).toFixed(1)}초 전 표본` : "");
    $("attitude-quaternion").textContent = state.attitude ? state.attitude.quaternion_wxyz.map(value => value.toFixed(4)).join(", ") : "—";
    $("attitude-frame").textContent = state.attitude?.frame || "자세 자료 없음";
    $("attitude-time").textContent = state.attitude ? `${measurementUtc(state.attitude.utc)}${
      state.attitudeStatus === "INTERPOLATED" ? " · 보간 시각" : ""}` : "선택 시각에 수신한 자세 자료가 없습니다";
    $("attitude-received-time").textContent = state.attitude?.received_utc ? measurementUtc(state.attitude.received_utc) : "—";
    let directions = null;
    try {directions = window.rosAttitudeMath?.computeState(state) || null;} catch (_) {}
    attitudeViewer?.setDirections(directions);
    $("attitude-direction-state").textContent = !attitudeViewer ? "3D 뷰어 사용 불가" :
      !directions ? "방향 표시 자료·좌표계 검증 대기" : state.attitudeStatus === "UNKNOWN" ?
      state.position_itrf_m ? "자세 정보 없음 · 위치 표식만 표시" : "위치·자세 자료 없음" : state.position_itrf_m ?
        state.sun_itrf ? "측정 자세·위치 기준 지구·태양 방향" : "태양 방향 자료 없음" : "위치 정보 없음";
  }
  function setupStateHistory() {
    if (!window.rosStateHistory) return;
    historyController = new window.rosStateHistory.Controller({api,onChange:renderHistoryState});
    const enter = action => {tmSource="published"; action();};
    $("state-history-live").addEventListener("click",()=>enter(()=>historyController.goLive()));
    $("state-history-play").addEventListener("click",()=>enter(()=>historyController.togglePlay()));
    $("state-history-previous").addEventListener("click",()=>enter(()=>historyController.stepSample(-1)));
    $("state-history-next").addEventListener("click",()=>enter(()=>historyController.stepSample(1)));
    $("state-history-speed").addEventListener("change",()=>historyController.setSpeed($("state-history-speed").value));
    $("state-history-slider").addEventListener("input",()=>enter(()=>historyController.seek(Number($("state-history-slider").value)*1000)));
    historyController.emit();
  }
  function renderAttitude(attitude) {
    if (tmSource === "published") {
      if (historyController) renderHistoryState(historyController.state());
      else {
        attitudeViewer?.setDirections(null);
        $("attitude-source").textContent="자세 정보 없음";
        $("attitude-direction-state").textContent="위치·자세 이력 연결 대기";
      }
      return;
    }
    let directions = null;
    try {directions = attitude && window.rosAttitudeMath?.compute({attitude,orbitView:attitudeOrbitView}) || null;} catch (_) {}
    attitudeViewer?.setDirections(directions);
    $("attitude-source").textContent = tmSource === "manual" ? "불러온 시험 자세 TM" : "합성 예시 TM";
    $("attitude-direction-state").textContent = directions ? "수동 시험 자료의 지구·태양 방향" : "수동 시험 자료의 좌표계·위치 연결 없음";
    $("attitude-received-time").textContent = "수동 시험 입력";
    $("state-history-position").textContent = directions?.satelliteItrf ? directions.satelliteItrf.map(value=>value.toFixed(0)).join(", ") : "수동 자료의 위치 연결 없음";
    $("state-history-position-source").textContent = "수동 시험 입력 · 수신 이력 아님";
  }

  async function refreshAttitudeOrbit() {
    if (attitudeOrbitPending) return;
    attitudeOrbitPending = true;
    try {
      const orbit = await api("/api/fds/orbit?target=synthetic");
      attitudeOrbitView = orbit?.schema === "fds.orbit-view.v1" &&
        orbit.norad_id === 99999 ? orbit : null;
      historyController?.cache.setCurrentOrbit(attitudeOrbitView);
    } catch (_) {
      attitudeOrbitView = null;
    } finally {
      attitudeOrbitPending = false;
      renderAttitude(selectedTm?.attitude);
    }
  }
  function loadTm(data, sourceLabel = "수동 시험용 정규화 TM") {
    selectedTm = normalizedTm(data);
    const timing = showTmTiming(selectedTm);
    selectedSensor = selectedTm.sensors.find((item) => item.id === selectedSensor?.id) ||
      selectedTm.sensors[0];
    window.rosTestPreviewActive = true;
    $("sensor-empty").hidden = true;
    $("sensor-list").hidden = false;
    $("sensor-filter").disabled = false;
    document.querySelectorAll("[data-range]").forEach((button) => button.disabled = false);
    $("sensor-count").textContent = `${selectedTm.sensors.length}개 · 시험`;
    $("ros-data-state").textContent = timing.delayed ? "과거 TM 수신 · 시험" :
      timing.stale ? "과거 측정 · 시험" : "시험 TM 스냅샷";
    $("ros-last-tm").textContent = selectedTm.received_utc;
    $("chart-source").textContent =
      `데이터 소스: ${sourceLabel} · 측정 UTC ${timing.latestUtc} · 실제 위성 TM 아님`;
    $("demo-toggle").textContent = "합성 예시 보기";
    if (tmSource !== "published") {
      $("attitude-quaternion").textContent = selectedTm.attitude ?
      selectedTm.attitude.quaternion_wxyz.map((x) => x.toFixed(4)).join(", ") : "—";
      $("attitude-frame").textContent = selectedTm.attitude?.frame || "미정";
      $("attitude-time").textContent = selectedTm.attitude?.utc || "없음";
    }
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
    const now = Date.now();
    const fresh = selectedTm.sensors.filter((sensor) => freshSample(sensor.samples.at(-1), now));
    if (!fresh.length) {
      count.textContent = "현재 판정 불가 · 과거 TM";
      list.textContent = "수신된 계측은 과거 표본입니다. 한계 초과 이력은 이벤트에서 확인할 수 있습니다.";
      $("ros-alarm-state").textContent = "현재 판정 불가";
      return;
    }
    const breached = fresh.filter((sensor) => {
      const value = sensor.samples.at(-1).value;
      return value < sensor.lower || value > sensor.upper;
    });
    const label = tmSource === "published" ? "게시 시험 표본" : "불러온 시험 표본";
    const olderCount = selectedTm.sensors.length - fresh.length;
    count.textContent = `${label} · ${breached.length}개 초과` +
      (olderCount ? ` · ${olderCount}개 과거` : "");
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
        $("trend-details").open = true;
        activateTab("status");
      });
      list.append(row);
    }
  }
  function markPublishedAge() {
    if (tmSource !== "published" || !selectedTm) return;
    const old = Date.now() - Date.parse(selectedTm.received_utc) > 5 * 60 * 1000;
    const timing = showTmTiming(selectedTm);
    $("ros-data-state").textContent = old ? "게시된 시험 TM 오래됨" :
      timing.delayed ? "과거 TM 수신 · 시험" :
        timing.stale ? "최근 계측 없음 · 시험" : "게시된 시험 TM";
    $("chart-source").textContent = "데이터 소스: 게시된 시험 TM · " +
      `측정 UTC ${timing.latestUtc}` +
      (old ? " · 5분 이상 새 수신 없음" : timing.delayed ? " · 과거 TM 포함" : "") +
      " · 실제 위성 TM 아님";
    renderSensors();
    renderCurrentAlerts();
    renderAttitude(selectedTm.attitude);
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
        renderAttitude(selectedTm?.attitude);
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
        renderAttitude(selectedTm?.attitude);
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
    if (window.rosAttitudeViewer) {
      try {
        attitudeViewer = window.rosAttitudeViewer.create($("attitude-canvas"),
          (state) => { $("attitude-model-state").textContent = state; });
        attitudeViewer.load("/assets/pado-sat-deployed-mesh.json").then((loaded) => {
          if (loaded) return attitudeViewer.loadAppearance("/assets/pado-sat-appearance.json");
          return false;
        }).catch(() => {});
        $("attitude-reset-camera").addEventListener("click", () => attitudeViewer.reset());
      } catch (_) {
        attitudeViewer = null;
        $("attitude-model-state").textContent = "이 브라우저에서 3D 표시를 시작하지 못했습니다";
      }
    } else {
      $("attitude-model-state").textContent = "3D 표시 모듈 없음";
    }
    setupStateHistory();
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
      renderSensors();
    }));
    $("sequence-select").addEventListener("change", showSequence);
    $("sim-sequence-select").addEventListener("change", showSimSequence);
    $("sim-tm-request").addEventListener("click", requestSimTm);
    $("reservation-step").addEventListener("change", setReservationDefault);
    $("sequence-import").addEventListener("click", importSequence);
    $("reservation-form").addEventListener("submit", reserve);
    try {
      session = await api("/api/session");
      if (window.rosPacketBrowser) packetBrowser = window.rosPacketBrowser.create({api});
      try { await refreshDrafts(); }
      catch (error) { message("sequence-message", `ROS 초안 저장소 연결 실패: ${error.message}`, true); }
      try { await refreshSimDrafts(); }
      catch (error) { message("sim-sequence-message", `가상 위성 시험 저장소 연결 실패: ${error.message}`, true); }
      try { await refreshSimTmRequests(); }
      catch (error) { message("sim-tm-request-message", `저장 TM 요청 연결 실패: ${error.message}`, true); }
      if (session.role === "auditor") {
        $("sequence-import").disabled = true;
        $("reservation-form").querySelector("button").disabled = true;
      }
    } catch (error) { message("sequence-message", `ROS 초안 저장소 연결 실패: ${error.message}`, true); }
    refreshAttitudeOrbit();
    if (session) historyController?.refresh();
    await refreshPublishedTm();
    refreshBeacon();
    refreshSpacecraftEvents();
    setInterval(() => {
      if (document.visibilityState === "visible") refreshAttitudeOrbit();
    }, 60000);
    setInterval(() => {
      if (document.visibilityState === "visible") refreshPublishedTm();
    }, 5000);
    setInterval(() => {
      if (document.visibilityState === "visible" && session) historyController?.refresh();
    },5000);
    setInterval(() => {
      if (document.visibilityState === "visible") historyController?.tick();
    },250);
    setInterval(() => {
      if (document.visibilityState === "visible" && session) {
        refreshBeacon();
        refreshSpacecraftEvents();
        if (!$("ros-view-telemetry").hidden) packetBrowser?.refresh();
        refreshSimTmRequests().catch((error) =>
          message("sim-tm-request-message", `요청 상태 갱신 실패: ${error.message}`, true));
        refreshSimDrafts().catch((error) =>
          message("sim-reservation-message", `시험 상태 갱신 실패: ${error.message}`, true));
      }
    }, 5000);
  }
  init();
})();
