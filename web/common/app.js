"use strict";

async function api(path, options = {}) {
  const response = await fetch(path, { credentials: "same-origin", cache: "no-store", ...options });
  if (response.status === 401 && path !== "/api/login") {
    window.location.assign("/login");
    throw new Error("세션이 만료되었습니다.");
  }
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "요청에 실패했습니다.");
  return result;
}

function value(id, content) {
  const node = document.getElementById(id);
  if (node) node.textContent = String(content);
}

function showError(message) {
  const node = document.getElementById("api-error");
  if (node) { node.textContent = message; node.hidden = false; }
}

// Deliberately synthetic values for layout review. This source never represents live TM.
const exampleSensors = [
  { id: "a", name: "예시 센서 A", group: "전원 예시", unit: "V", low: 25, high: 31, center: 28, spread: 1.15 },
  { id: "b", name: "예시 센서 B", group: "열 예시", unit: "°C", low: 14, high: 36, center: 24, spread: 5.2 },
  { id: "c", name: "예시 센서 C", group: "자세 예시", unit: "°/s", low: -3, high: 3, center: 0, spread: 1.1 }
];
const svgNS = "http://www.w3.org/2000/svg";
let exampleActive = false;
let selectedExample = exampleSensors[1];
let exampleRange = 30;
let latestRosOverview = null;
let rosSummaryUnavailable = false;

function renderRosCoreStatus() {
  if (latestRosOverview === null || exampleActive) return;
  const count = latestRosOverview.sim_frame_count;
  value("ros-data-state", count > 0 ? `시험 프레임 ${count}건` : "수신 없음");
  value("ros-last-tm", count > 0 ? `순번 ${latestRosOverview.last_sequence} · 시각 없음` : "수신 없음");
}

function svgNode(name, attributes = {}, label = "") {
  const node = document.createElementNS(svgNS, name);
  for (const [key, content] of Object.entries(attributes)) node.setAttribute(key, String(content));
  if (label) node.textContent = label;
  return node;
}

function examplePoints(sensor, count) {
  return Array.from({ length: count }, (_, index) => {
    const wave = Math.sin(index * 0.35 + sensor.id.charCodeAt(0)) * sensor.spread;
    const drift = Math.cos(index * 0.11) * sensor.spread * 0.28;
    const excursion = sensor.id === "b" && index >= count - 9 && index <= count - 6 ? 15 : 0;
    return Number((sensor.center + wave + drift + excursion).toFixed(2));
  });
}

function exampleStatus(sensor, points) {
  return points.some((point) => point < sensor.low || point > sensor.high) ? "범위 초과" : "예시 정상";
}

function renderExampleChart(sensor, points) {
  const chart = document.getElementById("telemetry-chart");
  chart.setAttribute("aria-label", `${sensor.name} 예시 추세. 실제 TM 아님.`);
  chart.replaceChildren();
  chart.append(svgNode("path", { class: "chart-grid", d: "M40 35H770 M40 95H770 M40 155H770 M40 215H770 M40 35V215 M220 35V215 M400 35V215 M580 35V215 M770 35V215" }));
  const margin = Math.max((sensor.high - sensor.low) * 0.3, 1);
  const minimum = Math.min(sensor.low - margin, ...points);
  const maximum = Math.max(sensor.high + margin, ...points);
  const y = (number) => 215 - (number - minimum) / (maximum - minimum) * 180;
  const x = (index) => 40 + index / (points.length - 1) * 730;
  chart.append(svgNode("rect", { class: "chart-limit", x: 40, y: y(sensor.high), width: 730, height: y(sensor.low) - y(sensor.high) }));
  chart.append(svgNode("path", { class: "chart-line", d: points.map((point, index) => `${index ? "L" : "M"}${x(index).toFixed(1)} ${y(point).toFixed(1)}`).join(" ") }));
  points.forEach((point, index) => {
    if (point < sensor.low || point > sensor.high) chart.append(svgNode("circle", { class: "chart-breach", cx: x(index).toFixed(1), cy: y(point).toFixed(1), r: 4 }));
  });
  chart.append(svgNode("text", { x: 45, y: 28, fill: "#b8a76d", "font-size": 13 }, `상한 ${sensor.high} ${sensor.unit}`));
  chart.append(svgNode("text", { x: 45, y: 233, fill: "#b8a76d", "font-size": 13 }, `하한 ${sensor.low} ${sensor.unit}`));
  chart.append(svgNode("text", { x: 40, y: 251, fill: "#7898a8", "font-size": 11 }, `-${exampleRange}분`));
  chart.append(svgNode("text", { x: 705, y: 251, fill: "#7898a8", "font-size": 11 }, "예시 현재"));
  chart.append(svgNode("text", { x: 660, y: 28, fill: "#dfb47c", "font-size": 12 }, "EXAMPLE DATA"));
}

function renderExample() {
  if (!exampleActive) return;
  const list = document.getElementById("sensor-list");
  list.replaceChildren();
  const query = document.getElementById("sensor-filter").value.trim().toLowerCase();
  const visible = exampleSensors.filter((item) => `${item.name} ${item.group}`.toLowerCase().includes(query));
  if (visible.length && !visible.includes(selectedExample)) selectedExample = visible[0];
  if (!visible.length) {
    const row = document.createElement("li");
    row.className = "sensor-no-match";
    row.textContent = "일치하는 예시 센서가 없습니다.";
    list.append(row);
  }
  for (const sensor of visible) {
    const points = examplePoints(sensor, exampleRange);
    const button = document.createElement("button");
    button.type = "button";
    button.setAttribute("aria-current", String(sensor.id === selectedExample.id));
    for (const [content, className] of [[sensor.name, "sensor-name"], [`${points.at(-1)} ${sensor.unit}`, "sensor-value"], [sensor.group, "sensor-group"], [exampleStatus(sensor, points), "sensor-state"]]) {
      const span = document.createElement("span");
      span.className = className;
      if (content === "범위 초과") span.classList.add("alert");
      span.textContent = content;
      button.append(span);
    }
    button.addEventListener("click", () => { selectedExample = sensor; renderExample(); });
    const row = document.createElement("li");
    row.append(button);
    list.append(row);
  }
  const points = examplePoints(selectedExample, exampleRange);
  const breaches = points.map((point, index) => ({ point, index })).filter(({ point }) => point < selectedExample.low || point > selectedExample.high);
  value("selected-sensor", selectedExample.name);
  value("latest-value", `${points.at(-1)} ${selectedExample.unit}`);
  value("limit-values", `${selectedExample.low} / ${selectedExample.high} ${selectedExample.unit}`);
  value("selected-state", exampleStatus(selectedExample, points));
  value("ros-alarm-state", `예시 ${breaches.length}건`);
  value("ros-last-tm", "예시 전용");
  value("event-count", `예시 ${breaches.length}건`);
  renderExampleChart(selectedExample, points);
  const rows = document.getElementById("alarm-rows");
  rows.replaceChildren();
  if (!breaches.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 4;
    td.className = "table-empty";
    td.textContent = "선택한 예시 구간에 범위 초과가 없습니다. 실제 경보 상태는 확인할 수 없습니다.";
    tr.append(td);
    rows.append(tr);
  } else {
    for (const { point, index } of breaches.slice(-5).reverse()) {
      const tr = document.createElement("tr");
      const cells = [`T-${points.length - index - 1}분 (예시)`, selectedExample.name, point > selectedExample.high ? "상한 초과" : "하한 미달", `${point} / ${point > selectedExample.high ? selectedExample.high : selectedExample.low} ${selectedExample.unit}`];
      cells.forEach((content) => { const td = document.createElement("td"); td.textContent = content; tr.append(td); });
      rows.append(tr);
    }
  }
}

function resetExample() {
  exampleActive = false;
  document.getElementById("sensor-list").hidden = true;
  document.getElementById("sensor-empty").hidden = false;
  document.getElementById("sensor-filter").value = "";
  document.getElementById("sensor-filter").disabled = true;
  document.querySelectorAll("[data-range]").forEach((button) => { button.disabled = true; });
  value("sensor-count", "0개");
  value("ros-data-state", "연결 대기");
  value("ros-alarm-state", "평가 불가");
  value("ros-last-tm", "수신 없음");
  value("event-count", "확인 불가");
  value("selected-sensor", "선택 안 됨");
  value("latest-value", "—");
  value("limit-values", "미정 / 미정");
  value("selected-state", "평가 불가");
  value("chart-source", "데이터 소스: 미연결");
  value("demo-toggle", "예시 데이터 보기");
  const chart = document.getElementById("telemetry-chart");
  chart.setAttribute("aria-label", "TM 계측 추세 데이터 없음");
  chart.replaceChildren(svgNode("path", { class: "chart-grid", d: "M40 35H770 M40 95H770 M40 155H770 M40 215H770 M40 35V215 M220 35V215 M400 35V215 M580 35V215 M770 35V215" }), svgNode("text", { x: 400, y: 134, "text-anchor": "middle", class: "chart-placeholder" }, "표시할 TM 데이터가 없습니다"));
  const rows = document.getElementById("alarm-rows");
  const tr = document.createElement("tr");
  const td = document.createElement("td");
  td.colSpan = 4;
  td.className = "table-empty";
  td.textContent = "TM 정의 및 실시간 데이터가 연결되면 경보 이력이 표시됩니다.";
  tr.append(td);
  rows.replaceChildren(tr);
  if (rosSummaryUnavailable) {
    value("ros-data-state", "조회 실패");
    value("ros-last-tm", "확인 불가");
  }
  renderRosCoreStatus();
}

function setupRosExample() {
  const toggle = document.getElementById("demo-toggle");
  toggle.addEventListener("click", () => {
    if (exampleActive) { resetExample(); return; }
    exampleActive = true;
    document.getElementById("sensor-list").hidden = false;
    document.getElementById("sensor-empty").hidden = true;
    document.getElementById("sensor-filter").disabled = false;
    document.querySelectorAll("[data-range]").forEach((button) => { button.disabled = false; });
    value("sensor-count", `${exampleSensors.length}개 · 예시`);
    value("ros-data-state", "예시 데이터");
    value("chart-source", "데이터 소스: 합성 예시 · 실제 TM 아님");
    value("demo-toggle", "예시 데이터 닫기");
    renderExample();
  });
  document.getElementById("sensor-filter").addEventListener("input", renderExample);
  document.querySelectorAll("[data-range]").forEach((button) => button.addEventListener("click", () => {
    exampleRange = Number(button.dataset.range);
    document.querySelectorAll("[data-range]").forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
    renderExample();
  }));
}

function setupUtcClock() {
  const update = () => value("utc-clock", `UTC ${new Date().toISOString().slice(11, 19)}`);
  update();
  setInterval(update, 1000);
}

async function loginPage() {
  const form = document.getElementById("login-form");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const error = document.getElementById("login-error");
    error.hidden = true;
    const button = form.querySelector("button");
    button.disabled = true;
    try {
      await api("/api/login", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: document.getElementById("username").value.trim(),
          ["password"]: document.getElementById("password").value,
          totp: document.getElementById("totp").value.trim()
        })
      });
      document.getElementById("password").value = "";
      window.location.assign("/ops");
    } catch (_) {
      document.getElementById("password").value = "";
      error.textContent = "계정, 비밀번호 또는 일회용 코드를 확인하세요.";
      error.hidden = false;
    } finally { button.disabled = false; }
  });
}

async function refreshRosSummary() {
  try {
    const overview = await api("/api/ros/overview");
    value("ros-mode", overview.mode);
    value("ros-tm", overview.tm_icd);
    value("ros-tc", overview.tc_send_available ? "사용 가능" : "차단");
    latestRosOverview = overview;
    rosSummaryUnavailable = false;
    renderRosCoreStatus();
    document.getElementById("api-error").hidden = true;
  } catch (_) {
    latestRosOverview = null;
    rosSummaryUnavailable = true;
    if (!exampleActive) {
      value("ros-data-state", "조회 실패");
      value("ros-last-tm", "확인 불가");
    }
    showError("ROS 시험 기록을 읽지 못했습니다.");
  }
}

async function workspace(page) {
  setupUtcClock();
  if (page === "ops") setupRosExample();
  let session;
  try { session = await api("/api/session"); }
  catch (_) { return; }
  value("user-badge", `${session.username} · ${session.role}`);
  document.getElementById("logout").addEventListener("click", async () => {
    try {
      await api("/api/logout", { method: "POST",
        headers: { "X-CSRF-Token": session.csrf } });
      window.location.assign("/login");
    } catch (_) { showError("로그아웃에 실패했습니다."); }
  });
  if (page === "ops") {
    await refreshRosSummary();
    setInterval(() => {
      if (document.visibilityState === "visible") refreshRosSummary();
    }, 5000);
  } else if (page === "orbit") {
    await initOrbitViewer();
  } else {
    try {
      const [catalog, passes] = await Promise.all([
        api("/api/mps/catalog"), api("/api/mps/passes")
      ]);
      value("mps-version", catalog.version);
      value("mps-commands", catalog.commands.length);
      value("mps-passes", passes.passes.length);
      value("mps-catalog-status", catalog.status);
    } catch (_) { showError("서브시스템 상태를 읽지 못했습니다."); }
  }
}

const page = document.body.dataset.page;
if (page === "login") loginPage();
if (page === "ops" || page === "mps" || page === "orbit") workspace(page);
