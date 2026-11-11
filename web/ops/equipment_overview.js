"use strict";

// The fixed engineering view shares the actual attitude CAD and its BODY frame.
// It has no camera controls and never derives sensor values from the CAD model.
window.rosEquipmentOverview = (() => {
  const WIDTH = 1200, HEIGHT = 860;
  const MAX_JSON = 256 * 1024, MAX_MESH = 4 * 1024 * 1024;
  const svgNS = "http://www.w3.org/2000/svg";
  const STATES = new Set(["CURRENT", "ALARM", "STALE", "PARTIAL", "MISSING"]);
  const LABEL = {CURRENT: "계측 수신", ALARM: "한계 초과", STALE: "측정 오래됨",
    PARTIAL: "일부 자료", MISSING: "자료 없음"};
  const COLOR = {CURRENT: "#87bdce", ALARM: "#ffc29a", STALE: "#a4a8ac",
    PARTIAL: "#bac4ca", MISSING: "#8295a0"};
  const finite = value => typeof value === "number" && Number.isFinite(value);
  const text = (value, max = 96) => typeof value === "string" && value.length <= max;
  const timestamp = value => typeof value === "string" && value.endsWith("Z") &&
    Number.isFinite(Date.parse(value));
  const dot = (a, b) => a.reduce((sum, value, axis) => sum + value * b[axis], 0);
  const cross = (a, b) => [a[1] * b[2] - a[2] * b[1],
    a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  const unit = value => { const length = Math.hypot(...value); return value.map(x => x / length); };

  function cameraBasis(camera = {}) {
    const yaw = finite(camera.yaw_rad) && Math.abs(camera.yaw_rad) <= Math.PI * 2 ? camera.yaw_rad : -0.98;
    const pitch = finite(camera.pitch_rad) && Math.abs(camera.pitch_rad) <= 1.3 ? camera.pitch_rad : 0.48;
    const eye = [Math.cos(pitch) * Math.cos(yaw), Math.cos(pitch) * Math.sin(yaw), Math.sin(pitch)];
    const right = unit(cross([0, 0, 1], eye));
    return {eye, right, up: cross(eye, right)};
  }

  function project(position, basis, pixels, width = WIDTH, height = HEIGHT) {
    const depth = dot(position, basis.eye), perspective = 4.3 / (4.3 - depth);
    return {x: width / 2 + dot(position, basis.right) * pixels * perspective,
      y: height / 2 - dot(position, basis.up) * pixels * perspective, depth};
  }

  function projectAnchor(bodyMm, mesh, basis, pixels, width = WIDTH, height = HEIGHT) {
    if (!Array.isArray(bodyMm) || bodyMm.length !== 3 || !bodyMm.every(finite) ||
        !mesh || !Array.isArray(mesh.centre) || !finite(mesh.scale) || mesh.scale <= 0)
      throw Error("invalid_equipment_anchor");
    return project(bodyMm.map((value, axis) => (value - mesh.centre[axis]) * mesh.scale),
      basis, pixels, width, height);
  }

  function readingState(raw, now = Date.now()) {
    const result = {...raw};
    const hasValue = typeof raw.value === "boolean" || finite(raw.value) || text(raw.value, 80);
    const validTimes = timestamp(raw.sampled_utc) && timestamp(raw.received_utc) &&
      Date.parse(raw.sampled_utc) <= Date.parse(raw.received_utc);
    const maxAge = finite(raw.max_age_s) && raw.max_age_s > 0 && raw.max_age_s <= 86400 ? raw.max_age_s : 120;
    if (!hasValue || !validTimes || raw.freshness === "MISSING") {
      result.value = null; result.freshness = "MISSING"; result.limit_state = "INDETERMINATE";
      return result;
    }
    const age = now - Date.parse(raw.sampled_utc);
    if (raw.freshness !== "CURRENT" || age < -30000 || age > maxAge * 1000 ||
        Date.parse(raw.received_utc) > now + 30000) {
      result.freshness = "STALE"; result.limit_state = "INDETERMINATE";
      return result;
    }
    result.freshness = "CURRENT";
    const limits = typeof raw.value === "number" && finite(raw.lower) && finite(raw.upper) && raw.lower <= raw.upper;
    if (!limits) result.limit_state = "UNCONFIGURED";
    else {
      const measured = raw.value < raw.lower || raw.value > raw.upper ? "OUT_OF_RANGE" : "IN_RANGE";
      result.limit_state = raw.limit_state === measured ? measured : "INDETERMINATE";
    }
    return result;
  }

  function equipmentState(readings) {
    if (readings.some(row => row.freshness === "CURRENT" && row.limit_state === "OUT_OF_RANGE")) return "ALARM";
    const current = readings.filter(row => row.freshness === "CURRENT").length;
    if (current && current === readings.length) return "CURRENT";
    if (current) return "PARTIAL";
    return readings.some(row => row.freshness === "STALE") ? "STALE" : "MISSING";
  }

  function normalizePayload(raw, now = Date.now()) {
    if (!raw || raw.schema !== "pado.equipment-status.v1" || !timestamp(raw.generated_utc) ||
        !raw.layout || raw.layout.units !== "mm" || raw.layout.frame !== "BODY" ||
        !Array.isArray(raw.equipment) || raw.equipment.length > 32)
      throw Error("invalid_equipment_status");
    const ids = new Set();
    const equipment = raw.equipment.map(component => {
      if (!component || !text(component.id, 64) || !/^[A-Za-z0-9_.-]+$/.test(component.id) ||
          ids.has(component.id) || !text(component.label) || !text(component.group, 48) ||
          !Array.isArray(component.anchor_body_mm) || component.anchor_body_mm.length !== 3 ||
          !component.anchor_body_mm.every(value => finite(value) && Math.abs(value) < 100000) ||
          !component.callout || !["left", "right"].includes(component.callout.side) ||
          !Number.isInteger(component.callout.order) || component.callout.order < 0 ||
          !STATES.has(component.status) || !Array.isArray(component.readings) || component.readings.length > 12)
        throw Error("invalid_equipment_status");
      ids.add(component.id);
      const readingIds = new Set();
      const readings = component.readings.map(row => {
        if (!row || !text(row.id, 64) || readingIds.has(row.id) || !text(row.label) || !text(row.unit, 24) ||
            !(row.value === null || typeof row.value === "boolean" || finite(row.value) || text(row.value, 80)) ||
            !["CURRENT", "STALE", "MISSING"].includes(row.freshness)) throw Error("invalid_equipment_reading");
        readingIds.add(row.id);
        return readingState(row, now);
      });
      return {...component, readings, status: equipmentState(readings)};
    });
    return {...raw, equipment};
  }

  function valueText(reading) {
    if (reading.value === null) return "—";
    if (typeof reading.value === "boolean") return reading.value ? "ON" : "OFF";
    if (typeof reading.value === "string") return reading.value;
    const places = Number.isInteger(reading.decimals) && reading.decimals >= 0 && reading.decimals <= 6 ? reading.decimals : 2;
    return reading.value.toFixed(places);
  }

  function readingLabel(reading) {
    if (reading.freshness === "MISSING") return "자료 없음";
    if (reading.freshness === "STALE") return "오래됨 · 판정 불가";
    return {IN_RANGE: "범위 내", OUT_OF_RANGE: "한계 초과", UNCONFIGURED: "범위 미설정",
      INDETERMINATE: "판정 불가"}[reading.limit_state] || "판정 불가";
  }

  function measurementUtc(value, compact = false) {
    if (!timestamp(value)) return "측정 UTC —";
    const iso = new Date(value).toISOString();
    return compact ? `${iso.slice(11, 19)} UTC` : iso.replace("T", " ").replace("Z", " UTC");
  }

  function fitText(value, width, fontSize) {
    const budget = width / fontSize;
    let result = "", used = 0;
    for (const character of value) {
      const units = character.codePointAt(0) > 255 ? 1 : .62;
      if (used + units > budget - 1) return result + "…";
      result += character; used += units;
    }
    return result;
  }

  const html = (name, className, content) => {
    const node = document.createElement(name);
    if (className) node.className = className;
    if (content !== undefined) node.textContent = content;
    return node;
  };
  const svg = (name, attributes = {}, content) => {
    const node = document.createElementNS(svgNS, name);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
    if (content !== undefined) node.textContent = content;
    return node;
  };

  let sharedAssets = null;
  async function boundedJson(path, maximum) {
    const response = await fetch(path, {credentials: "same-origin", cache: "no-store"});
    if (!response.ok || Number(response.headers.get("content-length") || 0) > maximum) throw Error("cad_asset_unavailable");
    const bytes = await response.arrayBuffer();
    if (!bytes.byteLength || bytes.byteLength > maximum) throw Error("cad_asset_size");
    return JSON.parse(new TextDecoder("utf-8", {fatal: true}).decode(bytes));
  }

  function texture(path) {
    return new Promise(resolve => {
      if (typeof Image === "undefined") { resolve(null); return; }
      const image = new Image();
      const timer = setTimeout(() => finish(null), 10000);
      function finish(value) { clearTimeout(timer); image.onload = null; image.onerror = null; resolve(value); }
      image.onload = () => {
        const width = image.naturalWidth || image.width, height = image.naturalHeight || image.height;
        if (!Number.isInteger(width) || !Number.isInteger(height) || width < 1 || height < 1 ||
            width > 4096 || height > 4096 || width * height > 16 * 1024 * 1024) { finish(null); return; }
        let source = image;
        try {
          const layer = document.createElement("canvas"); layer.width = width; layer.height = height;
          const ctx = layer.getContext("2d", {willReadFrequently: true}); ctx.drawImage(image, 0, 0);
          const pixels = ctx.getImageData(0, 0, width, height);
          for (let i = 0; i < pixels.data.length; i += 4)
            if (pixels.data[i] > 252 && pixels.data[i + 1] > 252 && pixels.data[i + 2] > 252) pixels.data[i + 3] = 0;
          ctx.putImageData(pixels, 0, 0); source = layer;
        } catch (_) { /* The mesh remains available with its CAD material colours. */ }
        finish({image: source, width, height});
      };
      image.onerror = () => finish(null); image.decoding = "async"; image.src = path;
    });
  }

  async function assets() {
    if (!sharedAssets) sharedAssets = (async () => {
      if (!window.rosAttitudeViewer) throw Error("cad_parser_unavailable");
      const mesh = window.rosAttitudeViewer.meshFromJson(await boundedJson("/assets/pado-sat-deployed-mesh.json", MAX_MESH));
      let appearance = null;
      try {
        const compiled = window.rosAttitudeViewer.appearanceFromJson(
          await boundedJson("/assets/pado-sat-appearance.json", 64 * 1024), mesh);
        const images = new Map(); let pixels = 0;
        for (const [id, path] of Object.entries(compiled.textures)) {
          const image = await texture(path);
          if (image) { pixels += image.width * image.height; if (pixels > 32 * 1024 * 1024) throw Error("texture_budget"); }
          images.set(id, image);
        }
        const triangles = compiled.triangles.filter(item => images.get(item.texture))
          .map(item => ({...item, asset: images.get(item.texture)}));
        if (triangles.length) appearance = {triangles};
      } catch (_) { /* Texture availability never replaces the real CAD with a guessed shape. */ }
      return {mesh, appearance};
    })().catch(error => { sharedAssets = null; throw error; });
    return sharedAssets;
  }

  function calloutLayout(equipment) {
    return ["left", "right"].flatMap(side => {
      const items = equipment.filter(item => item.callout.side === side)
        .sort((a, b) => a.callout.order - b.callout.order || a.id.localeCompare(b.id));
      const space = (HEIGHT - 70) / Math.max(items.length, 1);
      return items.map((item, index) => {
        const height = Math.min(98, space - 8), dense = height < 80;
        const headerBaseline = height < 32 ? Math.min(14, height - 3) : dense ? 17 : 22;
        const readingBaseline = dense ? 31 : 42, lineHeight = dense ? 13 : 17;
        return {item, x: side === "left" ? 14 : 906, y: 35 + space * index + 4,
          width: 280, height, side, headerBaseline, headerSize: height < 32 ? 11 : dense ? 14 : 18,
          statusSize: height < 32 ? 10 : dense ? 11 : 14, readingBaseline, lineHeight,
          readingSize: dense ? 11 : 15,
          readingCount: Math.min(3, Math.max(0, Math.floor((height - 8 - readingBaseline) / lineHeight) + 1))};
      });
    });
  }

  function create({api}) {
    const byId = id => document.getElementById(id);
    const nodes = {root: byId("equipment-overview"), matrix: byId("equipment-matrix"),
      canvas: byId("equipment-cad-canvas"), leaders: byId("equipment-callouts"),
      state: byId("equipment-status"), summary: byId("equipment-summary"),
      time: byId("equipment-generated"), source: byId("equipment-layout-source"), cad: byId("equipment-cad-state")};
    if (Object.values(nodes).some(node => !node) || typeof nodes.canvas.getContext !== "function") return null;
    const ctx = nodes.canvas.getContext("2d");
    if (!ctx) return null;
    let payload = null, mesh = null, appearance = null, renderer = null;
    let requestId = 0, pending = false, stopped = false, selected = null, reachable = false;
    let cadPending = false, cadRevision = 0;
    nodes.canvas.width = WIDTH; nodes.canvas.height = HEIGHT;

    function renderMatrix() {
      nodes.matrix.replaceChildren();
      if (!payload) { nodes.matrix.append(html("p", "equipment-waiting", "장비 계측 자료를 기다리는 중입니다.")); return; }
      const table = html("table", "equipment-readings-table");
      const head = html("thead"), heading = html("tr");
      ["장비 / 상태", "수신 계측 · 측정 UTC"].forEach(label => heading.append(html("th", "", label)));
      head.append(heading); table.append(head);
      const body = html("tbody"); let previousGroup = null;
      const groups = ["EPS", "AOCS", "ADCS", "CDHS", "CS", "CREST", "SIREN"];
      const rank = group => groups.includes(group) ? groups.indexOf(group) : groups.length;
      const ordered = [...payload.equipment].sort((a, b) => rank(a.group) - rank(b.group) ||
        a.group.localeCompare(b.group));
      for (const component of ordered) {
        if (component.group !== previousGroup) {
          const group = html("tr", "equipment-group"), cell = html("th", "", component.group);
          cell.colSpan = 2; group.append(cell); body.append(group); previousGroup = component.group;
        }
        const row = html("tr", `equipment-row equipment-state-${component.status.toLowerCase()}`);
        row.dataset.equipment = component.id;
        if (component.id === selected) row.classList.add("equipment-selected");
        const name = html("td", "equipment-name");
        const button = html("button", "equipment-select", component.label);
        button.type = "button"; button.setAttribute("aria-pressed", String(component.id === selected));
        button.addEventListener("click", () => { selected = selected === component.id ? null : component.id; renderMatrix(); drawCallouts(); });
        name.append(button, html("small", "equipment-component-state", reachable ? LABEL[component.status] : "조회 불가"));
        const values = html("td", "equipment-values");
        if (!component.readings.length) values.append(html("span", "equipment-no-data", "TM 연결 없음 · 계측 매핑 대기"));
        for (const reading of component.readings) {
          const style = reading.freshness === "CURRENT" ? reading.limit_state.toLowerCase() : reading.freshness.toLowerCase();
          const item = html("div", `equipment-reading reading-${style}`);
          item.append(html("span", "equipment-reading-label", reading.label));
          item.append(html("strong", "equipment-reading-value", `${valueText(reading)}${reading.value === null || !reading.unit ? "" : " " + reading.unit}`));
          const state = html("small", "equipment-reading-status", reachable ? readingLabel(reading) : "조회 불가");
          if (reading.freshness === "CURRENT" && finite(reading.lower) && finite(reading.upper))
            state.title = `${reading.lower} ~ ${reading.upper} ${reading.unit}`;
          const time = html("time", "equipment-reading-time", measurementUtc(reading.sampled_utc));
          if (timestamp(reading.sampled_utc)) time.dateTime = reading.sampled_utc;
          time.title = `측정 ${reading.sampled_utc || "—"} · 수신 ${reading.received_utc || "—"}`;
          item.append(state, time); values.append(item);
        }
        row.append(name, values); body.append(row);
      }
      table.append(body); nodes.matrix.append(table);
    }

    function basisAndPixels() {
      const camera = payload?.layout?.camera || {};
      const zoom = finite(camera.zoom) ? Math.max(.65, Math.min(1.25, camera.zoom)) : 1;
      return {basis: cameraBasis(camera), pixels: Math.min(WIDTH * .16, HEIGHT * .24) * zoom};
    }

    function drawCallouts() {
      nodes.leaders.replaceChildren();
      if (!payload) return;
      const {basis, pixels} = basisAndPixels();
      for (const box of calloutLayout(payload.equipment)) {
        const item = box.item, colour = COLOR[item.status], highlighted = selected === item.id;
        const group = svg("g", {class: `equipment-callout equipment-state-${item.status.toLowerCase()}${highlighted ? " equipment-selected" : ""}`,
          "data-equipment": item.id});
        if (mesh) {
          const anchor = projectAnchor(item.anchor_body_mm, mesh, basis, pixels);
          const endX = box.side === "left" ? box.x + box.width : box.x;
          const elbowX = box.side === "left" ? endX + 24 : endX - 24;
          const middle = box.y + box.height / 2;
          group.append(svg("polyline", {points: `${anchor.x},${anchor.y} ${elbowX},${middle} ${endX},${middle}`,
            fill: "none", stroke: colour, "stroke-width": highlighted ? 3 : 1.5, opacity: highlighted ? 1 : .7}));
          group.append(svg("circle", {cx: anchor.x, cy: anchor.y, r: highlighted ? 5 : 3.2, fill: colour,
            stroke: "#071521", "stroke-width": 1}));
        }
        group.append(svg("rect", {x: box.x, y: box.y, width: box.width, height: box.height,
          fill: highlighted ? "#183749" : "#0e2331", stroke: highlighted ? "#e8f4f7" : "#355363", "stroke-width": highlighted ? 2 : 1}));
        const label = fitText(item.label, box.width - 28 - box.statusSize * 7, box.headerSize);
        group.append(svg("text", {x: box.x + 10, y: box.y + box.headerBaseline, fill: "#e3eef1", "font-size": box.headerSize,
          "font-weight": "700"}, label));
        group.append(svg("text", {x: box.x + box.width - 8, y: box.y + box.headerBaseline, fill: colour,
          "font-size": box.statusSize, "text-anchor": "end"}, reachable ? LABEL[item.status] : "조회 불가"));
        const visible = item.readings.slice(0, box.readingCount);
        visible.forEach((reading, index) => {
          const limitColour = reading.freshness !== "CURRENT" ? "#97a6ad" :
            reading.limit_state === "OUT_OF_RANGE" ? "#ffc29a" : reading.limit_state === "IN_RANGE" ? "#b0d4b5" : "#bdced5";
          const value = `${reading.label}  ${valueText(reading)}${reading.value === null ? "" : " " + reading.unit}`;
          group.append(svg("text", {x: box.x + 10, y: box.y + box.readingBaseline + index * box.lineHeight,
            fill: limitColour, "font-size": box.readingSize}, fitText(value, box.width - 20, box.readingSize)));
        });
        if (!item.readings.length && box.readingCount) group.append(svg("text", {x: box.x + 10,
          y: box.y + box.readingBaseline, fill: "#91a4af", "font-size": box.readingSize}, "TM 연결 없음"));
        const measured = visible.find(reading => timestamp(reading.sampled_utc));
        if (box.height >= 92 && measured) group.append(svg("text", {x: box.x + 10,
          y: box.y + box.height - 7, fill: "#8da9b6", "font-size": 12}, measurementUtc(measured.sampled_utc, true)));
        group.append(svg("title", {}, `${item.label} · ${item.location_note || "부품 중심 기준"}\n` +
          item.readings.map(reading => `${reading.label}: ${valueText(reading)} ${reading.unit} · ${measurementUtc(reading.sampled_utc)} · ${readingLabel(reading)}`).join("\n")));
        group.setAttribute("role", "button"); group.setAttribute("tabindex", "0");
        group.setAttribute("aria-label", `${item.label} · ${LABEL[item.status]}`);
        const select = () => {
          selected = selected === item.id ? null : item.id; renderMatrix(); drawCallouts();
          if (selected) nodes.matrix.querySelector?.(`[data-equipment="${selected}"]`)?.scrollIntoView?.({block: "nearest", behavior: "smooth"});
        };
        group.addEventListener("click", select);
        group.addEventListener("keydown", event => { if (["Enter", " "].includes(event.key)) { event.preventDefault(); select(); } });
        nodes.leaders.append(group);
      }
    }

    function drawScene() {
      ctx.fillStyle = "#071521"; ctx.fillRect(0, 0, WIDTH, HEIGHT);
      ctx.strokeStyle = "#163141"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(320, HEIGHT / 2); ctx.lineTo(880, HEIGHT / 2);
      ctx.moveTo(WIDTH / 2, 130); ctx.lineTo(WIDTH / 2, HEIGHT - 130); ctx.stroke();
      if (!mesh) {
        ctx.fillStyle = "#91a9b5"; ctx.font = "17px Malgun Gothic, sans-serif"; ctx.textAlign = "center";
        ctx.fillText("고정 CAD 형상 확인 중", WIDTH / 2, HEIGHT / 2);
      } else {
        const {basis, pixels} = basisAndPixels();
        let layer = null;
        try { layer = renderer?.render(basis, pixels, WIDTH, HEIGHT, appearance); } catch (_) { layer = null; }
        if (layer) ctx.drawImage(layer, 0, 0);
        else {
          const positions = mesh.vertices.map(vertex => project(vertex, basis, pixels));
          const faces = mesh.triangles.map((face, index) => ({face, index,
            depth: face.slice(0, 3).reduce((sum, id) => sum + positions[id].depth, 0)}))
            .sort((a, b) => a.depth - b.depth);
          for (const {face: [a, b, c, material], index} of faces) {
            const points = [positions[a], positions[b], positions[c]];
            const shade = .55 + .38 * Math.abs(dot(mesh.normals[index] || [0, 0, 0], basis.eye));
            const color = mesh.colors[material].map(value => Math.min(245, Math.round(value * shade + 20)));
            ctx.fillStyle = `rgb(${color.join(",")})`; ctx.beginPath();
            ctx.moveTo(points[0].x, points[0].y); ctx.lineTo(points[1].x, points[1].y);
            ctx.lineTo(points[2].x, points[2].y); ctx.closePath(); ctx.fill();
          }
        }
      }
      drawCallouts();
    }

    function show(data) {
      payload = data;
      const counts = Object.fromEntries([...STATES].map(state => [state, payload.equipment.filter(item => item.status === state).length]));
      nodes.state.textContent = reachable ? "수신 계측 기준" : "조회 불가 · 현재 판정 불가";
      nodes.summary.textContent = `계측 수신 ${counts.CURRENT} · 한계 초과 ${counts.ALARM} · 일부 자료 ${counts.PARTIAL} · 오래됨 ${counts.STALE} · 자료 없음 ${counts.MISSING}`;
      nodes.time.textContent = `조회 UTC ${measurementUtc(payload.generated_utc)}`;
      const source = payload.layout.source || {};
      nodes.source.textContent = `스택 ${source.revision || "—"} / ${source.plan || "—"} · 부품 중심 기준 · 스택 임시 배치`;
      nodes.source.title = `${source.file || "참고 자료"} · SHA-256 ${source.sha256 || "—"}`;
      renderMatrix(); drawScene();
    }

    function unavailable() {
      reachable = false;
      if (payload) {
        show({...payload, equipment: payload.equipment.map(item => ({...item, status: "MISSING",
          readings: item.readings.map(reading => ({...reading, value: null, freshness: "MISSING", limit_state: "INDETERMINATE"}))}))});
      } else {
        nodes.state.textContent = "장비 계측 연결 대기"; nodes.summary.textContent = "현재 상태 판정 불가";
        renderMatrix(); drawScene();
      }
    }

    async function refresh() {
      // A stalled request must not keep an expired measurement looking current.
      if (payload && reachable && !stopped) {
        const aged = normalizePayload(payload);
        if (aged.equipment.some((item, index) => item.readings.some((reading, row) =>
            reading.freshness !== payload.equipment[index].readings[row].freshness))) show(aged);
      }
      if (pending || stopped) return;
      if (!mesh) loadCad();
      pending = true; const revision = ++requestId;
      try {
        const data = await api("/api/ros/equipment-status");
        if (stopped || revision !== requestId) return;
        if (JSON.stringify(data).length > MAX_JSON) throw Error("equipment_status_too_large");
        reachable = true; show(normalizePayload(data));
      } catch (_) { if (!stopped && revision === requestId) unavailable(); }
      finally { if (revision === requestId) pending = false; }
    }

    function loadCad() {
      if (cadPending || stopped) return;
      cadPending = true; const revision = ++cadRevision;
      assets().then(value => {
        if (stopped || revision !== cadRevision) return;
        mesh = value.mesh; appearance = value.appearance;
        try { renderer?.dispose?.(); renderer = window.rosAttitudeGl?.create(mesh) || null; } catch (_) { renderer = null; }
        nodes.cad.textContent = renderer && appearance ? "PADO-SAT CAD · 제공 이미지 · 고정 시점" : "PADO-SAT CAD · 재질색 · 고정 시점";
        drawScene();
      }).catch(() => {
        if (!stopped && revision === cadRevision) { nodes.cad.textContent = "CAD 자료를 읽을 수 없습니다"; drawScene(); }
      }).finally(() => { if (revision === cadRevision) cadPending = false; });
    }
    loadCad();
    drawScene();
    return {refresh, start() { stopped = false; return refresh(); }, stop() {
      stopped = true; ++requestId; pending = false; ++cadRevision; cadPending = false; unavailable();
    }};
  }

  return Object.freeze({create, cameraBasis, project, projectAnchor, readingState,
    equipmentState, normalizePayload, calloutLayout});
})();
