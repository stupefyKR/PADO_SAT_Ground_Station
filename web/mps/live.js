"use strict";

// MPS uses only FDS-published Earth-fixed positions; no TLE propagation runs here.
let mpsOrbitView = null;
let mpsSelectedPass = null;
let mpsN10Feedback = null;

function mpsRenderN10Feedback(feedback = mpsN10Feedback) {
  const state = document.getElementById("mps-n10-state");
  const sohState = document.getElementById("mps-n10-soh-state");
  const sensorList = document.getElementById("mps-n10-soh-sensors");
  const ackRows = document.getElementById("mps-n10-ack-rows");
  if (!state || !sohState || !sensorList || !ackRows) return;
  ackRows.replaceChildren();
  sensorList.replaceChildren();
  if (!feedback || feedback.schema !== "pado.sim-n10-feedback.v1" ||
      feedback.source !== "ROS_SIM_ONLY" || feedback.non_operational !== true ||
      !Array.isArray(feedback.ack_items)) {
    state.textContent = "ROS 조회 불가";
    sohState.textContent = "판정 불가";
    sensorList.textContent = "ROS 시험 자료를 읽을 수 없습니다.";
    const row = document.createElement("tr");
    const cell = document.createElement("td"); cell.colSpan = 4;
    cell.textContent = "ACK 판정 불가"; row.append(cell); ackRows.append(row);
    return;
  }
  state.textContent = "SIM ONLY · 조회 중";
  const recent = feedback.ack_items.slice(0, 12);
  if (!recent.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td"); cell.colSpan = 4;
    cell.textContent = "수신된 시험 ACK가 없습니다."; row.append(cell); ackRows.append(row);
  }
  for (const item of recent) {
    const row = document.createElement("tr");
    if (item.pass_id === mpsSelectedPass?.pass_id) row.classList.add("n10-selected-pass");
    const result = item.status === "ACKED" ? "수락" :
      item.status === "REJECTED" ? `거절 · 이유 ${item.ack?.reason ?? "?"}` : "판정 불가";
    const ackDate = Number.isSafeInteger(item.ack?.executed_at_utc_ms) ?
      new Date(item.ack.executed_at_utc_ms) : null;
    const stamp = ackDate && Number.isFinite(ackDate.getTime()) ?
      ackDate.toISOString() : "시각 오류";
    for (const content of [item.pass_id, item.command_name, result, stamp]) {
      const cell = document.createElement("td"); cell.textContent = String(content); row.append(cell);
    }
    ackRows.append(row);
  }
  const soh = feedback.bus_soh;
  const quality = feedback.bus_soh_state;
  sohState.textContent = quality === "CURRENT" ? "최신 시험 TM" :
    quality === "STALE" ? "자료 오래됨 · 판정 불가" :
    quality === "INVALID" ? "자료 오류 · 판정 불가" : "자료 없음 · 판정 불가";
  if (!soh || !Array.isArray(soh.sensors) || !["CURRENT", "STALE"].includes(quality)) {
    sensorList.textContent = "EPS 시험 TM을 확인할 수 없습니다.";
    return;
  }
  for (const sensor of soh.sensors) {
    const row = document.createElement("div"); row.className = "n10-sensor";
    const name = document.createElement("span"); name.textContent = sensor.name;
    const value = document.createElement("strong");
    value.textContent = `${sensor.value} ${sensor.unit}`;
    const limit = document.createElement("small");
    limit.textContent = quality === "STALE" ? "자료 오래됨" :
      sensor.limit_state === "OUT_OF_RANGE" ? "시험 한계 초과" :
      sensor.limit_state === "IN_RANGE" ? "시험 한계 내" : "판정 불가";
    if (quality === "CURRENT" && sensor.limit_state === "OUT_OF_RANGE")
      row.classList.add("n10-alarm");
    row.append(name, value, limit); sensorList.append(row);
  }
}

function mpsSetN10Feedback(feedback) {
  mpsN10Feedback = feedback;
  mpsRenderN10Feedback();
}

function mpsPassPoint(rows, at) {
  let right = rows.findIndex((row) => Date.parse(row[0]) >= at);
  if (right < 0) right = rows.length - 1;
  if (right === 0) return rows[0];
  const a = rows[right - 1], b = rows[right];
  const fraction = Math.max(0, Math.min(1, (at - Date.parse(a[0])) /
    (Date.parse(b[0]) - Date.parse(a[0]))));
  return [new Date(at).toISOString(), ...a.slice(1).map((value, index) => {
    const next = b[index + 1];
    if (index === 3) {
      let delta = next - value;
      if (delta > 180) delta -= 360;
      if (delta < -180) delta += 360;
      return ((value + fraction * delta + 540) % 360) - 180;
    }
    return value + fraction * (next - value);
  })];
}

function mpsPassSamples(orbit, pass) {
  const aos = pass.aos_utc_s * 1000, los = pass.los_utc_s * 1000;
  const station = orbit.stations?.find((item) => item.id === pass.station_id);
  const matched = station?.passes?.some((item) =>
    Math.abs(Date.parse(item.aos_utc) - aos) <= 1500 &&
    Math.abs(Date.parse(item.los_utc) - los) <= 1500);
  if (orbit.schema !== "fds.orbit-view.v1" || !matched ||
      !Array.isArray(orbit.samples) || orbit.samples.length < 2 ||
      Date.parse(orbit.start_utc) > aos || Date.parse(orbit.end_utc) < los ||
      Date.now() - Date.parse(orbit.input_epoch_utc) >= 72 * 3600000) {
    throw new Error("FDS 위치 자료와 선택한 N13 패스가 일치하지 않거나 TLE가 만료됐습니다.");
  }
  const rows = orbit.samples;
  return { station, rows: [mpsPassPoint(rows, aos),
    ...rows.filter((row) => Date.parse(row[0]) > aos && Date.parse(row[0]) < los),
    mpsPassPoint(rows, los)] };
}

function mpsGroundContext(orbit, pass) {
  const periodMs = orbit.orbital_period_s * 1000;
  if (!Number.isFinite(periodMs) || periodMs < 60_000 || periodMs > 86_400_000) {
    throw new Error("FDS 궤도 주기를 확인할 수 없습니다.");
  }
  const requestedStart = pass.aos_utc_s * 1000 - 2 * periodMs;
  const requestedEnd = pass.los_utc_s * 1000 + 2 * periodMs;
  const start = Math.max(requestedStart, Date.parse(orbit.start_utc));
  const end = Math.min(requestedEnd, Date.parse(orbit.end_utc));
  const samples = orbit.samples;
  return {
    rows: [mpsPassPoint(samples, start),
      ...samples.filter((row) => Date.parse(row[0]) > start && Date.parse(row[0]) < end),
      mpsPassPoint(samples, end)],
    requestedStart, requestedEnd,
    clippedBefore: start > requestedStart,
    clippedAfter: end < requestedEnd
  };
}

function mpsTraceGround(context, rows, width, height) {
  context.beginPath();
  let previousX = null;
  rows.forEach((row) => {
    const x = (row[4] + 180) / 360 * width;
    const y = (90 - row[5]) / 180 * height;
    if (previousX === null || Math.abs(x - previousX) > width / 2) context.moveTo(x, y);
    else context.lineTo(x, y);
    previousX = x;
  });
  context.stroke();
}

function mpsObserve(row, station) {
  const rad = Math.PI / 180;
  const lat = station.latitude_deg * rad, lon = station.longitude_deg * rad;
  const a = 6378137, e2 = 0.00669437999014;
  const n = a / Math.sqrt(1 - e2 * Math.sin(lat) ** 2);
  const x0 = (n + station.altitude_m) * Math.cos(lat) * Math.cos(lon);
  const y0 = (n + station.altitude_m) * Math.cos(lat) * Math.sin(lon);
  const z0 = (n * (1 - e2) + station.altitude_m) * Math.sin(lat);
  const x = row[1] - x0, y = row[2] - y0, z = row[3] - z0;
  const east = -Math.sin(lon) * x + Math.cos(lon) * y;
  const north = -Math.sin(lat) * Math.cos(lon) * x - Math.sin(lat) * Math.sin(lon) * y + Math.cos(lat) * z;
  const up = Math.cos(lat) * Math.cos(lon) * x + Math.cos(lat) * Math.sin(lon) * y + Math.sin(lat) * z;
  return { az: (Math.atan2(east, north) * 180 / Math.PI + 360) % 360,
    el: Math.atan2(up, Math.hypot(east, north)) * 180 / Math.PI };
}

function mpsDrawPass(pass) {
  const state = document.getElementById("pass-view-state");
  const note = document.getElementById("pass-view-note");
  const ground = document.getElementById("mps-ground-view");
  const polar = document.getElementById("mps-polar-view");
  const g = ground.getContext("2d"), p = polar.getContext("2d");
  g.clearRect(0, 0, ground.width, ground.height);
  p.clearRect(0, 0, polar.width, polar.height);
  if (!mpsOrbitView || !pass) {
    state.textContent = "FDS 자료 대기";
    note.textContent = "FDS 위치 산출물과 패스 일정을 기다리는 중입니다.";
    return;
  }
  let geometry, contextTrack;
  try {
    geometry = mpsPassSamples(mpsOrbitView, pass);
    contextTrack = mpsGroundContext(mpsOrbitView, pass);
  }
  catch (error) { state.textContent = "경로 확인 불가"; note.textContent = error.message; return; }
  const { station, rows } = geometry;
  state.textContent = `${pass.station_id === "yonsei" ? "연세대" : "항우연"} · ${pass.band} · FDS 확인`;
  const clipped = contextTrack.clippedBefore || contextTrack.clippedAfter ?
    ` · FDS 표본 범위로 ${contextTrack.clippedBefore && contextTrack.clippedAfter ? "양쪽" :
      contextTrack.clippedBefore ? "이전" : "이후"} 궤도 일부만 표시` : "";
  note.textContent = `AOS ${new Date(pass.aos_utc_s * 1000).toISOString()} · LOS ${new Date(pass.los_utc_s * 1000).toISOString()} · 이전·이후 각 2주기${clipped} · 시험용 근사 안테나 좌표`;
  const w = ground.width, h = ground.height;
  g.fillStyle = "#0b2331"; g.fillRect(0, 0, w, h);
  const image = document.getElementById("mps-earth-image");
  if (image?.complete && image.naturalWidth) g.drawImage(image, 0, 0, w, h);
  g.strokeStyle = "#ffffff66"; g.lineWidth = 1;
  for (let lon = -120; lon <= 120; lon += 60) {
    const x = (lon + 180) / 360 * w;
    g.beginPath(); g.moveTo(x, 0); g.lineTo(x, h); g.stroke();
  }
  for (let lat = -60; lat <= 60; lat += 30) {
    const y = (90 - lat) / 180 * h;
    g.beginPath(); g.moveTo(0, y); g.lineTo(w, y); g.stroke();
  }
  g.strokeStyle = "#e5f4fab3"; g.lineWidth = 1.5; g.setLineDash([5, 4]);
  mpsTraceGround(g, contextTrack.rows, w, h);
  g.setLineDash([]);
  g.strokeStyle = "#ffde82"; g.lineWidth = 3.5;
  mpsTraceGround(g, rows, w, h);
  g.font = "bold 11px system-ui";
  for (const [label, row] of [["AOS", rows[0]], ["LOS", rows[rows.length - 1]]]) {
    const x = (row[4] + 180) / 360 * w, y = (90 - row[5]) / 180 * h;
    g.fillStyle = "#ffde82";
    g.beginPath(); g.arc(x, y, 4, 0, Math.PI * 2); g.fill();
    const tx = Math.max(5, Math.min(w - 31, x + 7));
    const ty = Math.max(13, Math.min(h - 4, y - 7));
    g.fillStyle = "#071522e6"; g.fillRect(tx - 2, ty - 11, 28, 14);
    g.fillStyle = "#ffde82"; g.fillText(label, tx, ty);
  }
  const sx = (station.longitude_deg + 180) / 360 * w;
  const sy = (90 - station.latitude_deg) / 180 * h;
  g.fillStyle = "#71ffcf"; g.beginPath(); g.arc(sx, sy, 6, 0, Math.PI * 2); g.fill();
  g.font = "bold 13px system-ui"; g.fillText(pass.station_id.toUpperCase(), sx + 9, sy - 8);
  const cx = polar.width / 2, cy = polar.height / 2, radius = Math.min(cx, cy) - 35;
  p.fillStyle = "#091b2a"; p.fillRect(0, 0, polar.width, polar.height);
  p.strokeStyle = "#45606c"; p.lineWidth = 1;
  for (const elevation of [0, 30, 60]) {
    p.beginPath(); p.arc(cx, cy, radius * (1 - elevation / 90), 0, Math.PI * 2); p.stroke();
  }
  p.beginPath(); p.moveTo(cx - radius, cy); p.lineTo(cx + radius, cy);
  p.moveTo(cx, cy - radius); p.lineTo(cx, cy + radius); p.stroke();
  p.fillStyle = "#b5d3dc"; p.font = "13px system-ui";
  [["N", cx - 5, cy - radius - 8], ["E", cx + radius + 8, cy + 4],
   ["S", cx - 5, cy + radius + 18], ["W", cx - radius - 20, cy + 4]]
    .forEach(([label, x, y]) => p.fillText(label, x, y));
  p.strokeStyle = "#ffde82"; p.lineWidth = 3; p.beginPath();
  rows.forEach((row, index) => {
    const { az, el } = mpsObserve(row, station);
    const distance = radius * (1 - Math.max(0, Math.min(90, el)) / 90);
    const x = cx + distance * Math.sin(az * Math.PI / 180);
    const y = cy - distance * Math.cos(az * Math.PI / 180);
    if (index === 0) p.moveTo(x, y); else p.lineTo(x, y);
  });
  p.stroke();
}

function mpsSetOrbit(orbit) {
  mpsOrbitView = orbit;
  if (mpsSelectedPass) mpsDrawPass(mpsSelectedPass);
}

function mpsFieldInput(name, type, editable) {
  const label = document.createElement("label");
  label.className = "param-field"; label.textContent = `${name} · ${type}`;
  const input = document.createElement("input");
  input.dataset.field = name; input.dataset.type = type; input.disabled = !editable;
  if (type === "bool") {
    input.type = "checkbox";
  } else if (type.startsWith("ascii")) {
    input.type = "text"; input.maxLength = Number(type.slice(5));
  } else {
    input.type = "number"; input.min = "0"; input.step = "1";
  }
  label.append(input);
  return label;
}

function mpsRenderSequenceEditor(pass, schedule, session, drafts) {
  mpsSelectedPass = pass;
  mpsDrawPass(pass);
  mpsRenderN10Feedback();
  const panel = document.querySelector(".draft-panel");
  const scroll = panel.querySelector(".table-scroll");
  const actions = panel.querySelector(".panel-actions");
  const tag = panel.querySelector(".panel-head .tag");
  const existing = drafts.find((item) => item.pass_id === pass.pass_id);
  let revision = existing?.revision ?? 0;
  let steps = structuredClone(existing?.steps ?? []);
  let savedSteps = JSON.stringify(existing?.steps ?? []);
  let needsReview = existing?.needs_review === true;
  const editable = ["planner", "operator", "lead"].includes(session.role) &&
    pass.station_id === "yonsei" && pass.band === "UHF";
  if (pass.station_id !== "yonsei" || pass.band !== "UHF") {
    actions.replaceChildren(); scroll.textContent = "대전 S-band PASS는 현재 조회 전용입니다.";
    tag.textContent = "조회 전용"; tag.className = "tag locked";
    value("mps-review-summary", "S-band 패스의 TC 구성은 아직 연결되지 않았습니다.");
    value("mps-review-commands", "구성 불가"); value("mps-review-revision", "해당 없음");
    return;
  }
  const form = document.createElement("div"); form.className = "mps-sequence-form";
  const select = document.createElement("select"); select.setAttribute("aria-label", "시험 TC 명령");
  select.add(new Option("초안 TC 선택", ""));
  mpsDraftCommands.filter((item) => item.fields_defined).forEach((item) =>
    select.add(new Option(`${item.group} · ${item.name}`, item.name)));
  select.disabled = !editable;
  const offset = document.createElement("input"); offset.type = "number";
  offset.min = "0"; offset.max = String(pass.los_utc_s - pass.aos_utc_s - 1);
  offset.step = "1"; offset.value = "0"; offset.disabled = !editable;
  const offsetLabel = document.createElement("label");
  offsetLabel.textContent = "AOS 이후 초"; offsetLabel.append(offset);
  const fields = document.createElement("div"); fields.className = "mps-sequence-actions";
  const drawFields = () => {
    fields.replaceChildren();
    const command = mpsDraftCommands.find((item) => item.name === select.value);
    for (const [name, type] of Object.entries(command?.field_types ?? {}))
      fields.append(mpsFieldInput(name, type, editable));
  };
  select.addEventListener("change", drawFields);
  const add = document.createElement("button"); add.type = "button"; add.textContent = "단계 추가";
  add.disabled = !editable;
  add.addEventListener("click", () => {
    const command = mpsDraftCommands.find((item) => item.name === select.value);
    const seconds = Number(offset.value);
    if (!command || !command.fields_defined || !offset.value.trim() || !Number.isInteger(seconds) ||
        seconds < 0 || seconds >= pass.los_utc_s - pass.aos_utc_s || steps.length >= 64) {
      showError("명령과 PASS 내부의 실행 오프셋을 확인하세요."); return;
    }
    const parameters = {};
    for (const input of fields.querySelectorAll("input")) {
      const type = input.dataset.type;
      let entry = input.value;
      if (type === "bool") entry = input.checked;
      else if (type.startsWith("u")) {
        if (!/^\d+$/.test(entry) || BigInt(entry) >= 2n ** BigInt(type.slice(1))) {
          showError(`${input.dataset.field} 인자 범위를 확인하세요.`); return;
        }
        entry = Number(entry);
        if (!Number.isSafeInteger(entry)) { showError("정수 인자 정밀도를 확인하세요."); return; }
      } else if (type.startsWith("ascii") && (!/^[\x00-\x7F]*$/.test(entry) || entry.length > Number(type.slice(5)))) {
        showError(`${input.dataset.field} ASCII 인자를 확인하세요.`); return;
      }
      parameters[input.dataset.field] = entry;
    }
    steps.push({ offset_s: seconds, command_name: command.name, parameters }); draw();
  });
  form.append(select, offsetLabel, fields, add);
  const table = document.createElement("table");
  table.innerHTML = "<thead><tr><th>#</th><th>AOS + 초</th><th>명령</th><th>인자</th><th>편집</th></tr></thead><tbody></tbody>";
  const tbody = table.querySelector("tbody");
  const note = document.createElement("p"); note.className = "inspector-note";
  const simUplink = pass.station_id === "yonsei" && pass.band === "UHF";
  const simQueries = new Set(["mm_get_mode", "hk_get", "eps_get_hk", "aocs_get_hk",
    "cs_get_hk", "siren_get_hk", "crest_get_hk"]);
  note.textContent = needsReview ?
    "FDS 일정 갱신으로 PASS가 짧아졌습니다. 범위를 벗어난 실행 오프셋을 수정한 뒤 다시 저장하세요." :
    simUplink ? "연세대 UHF의 조회 명령 7종만 가상 위성에 전달할 수 있습니다. 실제 TC 승인·운용 송출은 차단됩니다." :
      "가상 위성 TC 왕복은 연세대 UHF 패스에서만 지원합니다. 이 패스의 시퀀스 초안은 저장할 수 있습니다.";
  scroll.replaceChildren(form, note, table);
  const save = document.createElement("button"); save.type = "button";
  save.textContent = "시험 시퀀스 저장"; save.disabled = !editable;
  const handoff = document.createElement("button"); handoff.type = "button";
  handoff.textContent = "ROS 시험 초안으로 전달";
  const handoffStatus = document.createElement("span");
  handoffStatus.className = "mps-handoff-status";
  handoffStatus.setAttribute("role", "status");
  save.addEventListener("click", async () => {
    save.disabled = true;
    try {
      const saved = await api("/api/mps/test-sequences", {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": session.csrf },
        body: JSON.stringify({ pass_id: pass.pass_id, schedule_sha256: schedule.schedule_sha256,
          expected_revision: revision, steps })
      });
      revision = saved.revision;
      needsReview = false;
      savedSteps = JSON.stringify(saved.steps);
      handoffStatus.textContent = "";
      note.textContent = simUplink ?
        "연세대 UHF의 조회 명령 7종만 가상 위성에 전달할 수 있습니다. 실제 TC 승인·운용 송출은 차단됩니다." :
        "가상 위성 TC 왕복은 연세대 UHF 패스에서만 지원합니다. 이 패스의 시퀀스 초안은 저장할 수 있습니다.";
      const position = drafts.findIndex((item) => item.pass_id === pass.pass_id);
      if (position < 0) drafts.push(saved); else drafts[position] = saved;
      document.getElementById("api-error").hidden = true; draw();
    } catch (_) { showError("저장 실패: 일정 또는 리비전 변경과 인자 범위를 확인하세요."); }
    finally { save.disabled = !editable; }
  });
  handoff.addEventListener("click", async () => {
    handoff.disabled = true;
    handoffStatus.textContent = "ROS에 시험 초안을 전달하는 중입니다.";
    try {
      await api("/api/mps/test-sequences/handoff", {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": session.csrf },
        body: JSON.stringify({ pass_id: pass.pass_id,
          schedule_sha256: schedule.schedule_sha256, expected_revision: revision })
      });
      handoffStatus.replaceChildren("ROS에 전달됨 · ");
      const link = document.createElement("a"); link.href = "/ops";
      link.textContent = "ROS에서 확인"; handoffStatus.append(link);
      document.getElementById("api-error").hidden = true;
    } catch (error) {
      handoffStatus.textContent = "전달 실패";
      showError(`ROS 전달 실패: ${error.message}`);
    } finally { draw(); }
  });
  actions.replaceChildren(save, handoff, handoffStatus);
  const draw = () => {
    tbody.replaceChildren();
    value("mps-review-commands", steps.length ? steps.map((item) => item.command_name).join(" → ") : "미등록");
    value("mps-review-revision", revision || "미저장");
    value("mps-review-summary", `${pass.pass_id} · 시험 단계 ${steps.length}개 · 충돌 검사·승인·송출 차단`);
    tag.textContent = needsReview ? "시각 재검토 필요" :
      revision ? `시험 초안 · 리비전 ${revision}` : "새 시험 초안";
    tag.className = needsReview ? "tag warning" : "tag neutral";
    handoff.disabled = !editable || !simUplink || !revision || !steps.length || needsReview ||
      steps.some((step) => !simQueries.has(step.command_name)) ||
      JSON.stringify(steps) !== savedSteps;
    steps.forEach((step, index) => {
      const row = document.createElement("tr");
      [index + 1, step.offset_s, step.command_name,
        Object.entries(step.parameters).map(([key, data]) => `${key}=${data}`).join(", ") || "없음"]
        .forEach((item) => { const cell = document.createElement("td"); cell.textContent = String(item); row.append(cell); });
      const cell = document.createElement("td");
      for (const [label, next] of [["↑", index - 1], ["↓", index + 1]]) {
        const move = document.createElement("button"); move.type = "button";
        move.textContent = label;
        move.setAttribute("aria-label", `${step.command_name} ${label === "↑" ? "위로" : "아래로"}`);
        move.disabled = !editable || next < 0 || next >= steps.length;
        move.addEventListener("click", () => {
          [steps[index], steps[next]] = [steps[next], steps[index]]; draw();
        });
        cell.append(move);
      }
      const remove = document.createElement("button"); remove.type = "button";
      remove.textContent = "제거"; remove.disabled = !editable;
      remove.addEventListener("click", () => { steps.splice(index, 1); draw(); });
      cell.append(remove); row.append(cell); tbody.append(row);
    });
  };
  draw();
}
