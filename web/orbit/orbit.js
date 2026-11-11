"use strict";

// The browser interpolates FDS Earth-fixed samples. It never propagates a TLE.
const ORBIT_EARTH_RADIUS_M = 6378137;
const ORBIT_TARGETS = Object.freeze({
  synthetic: { name: "가상 PADO-SAT", mode: "PADO-SAT SIMULATION", note: "가상 TM · 운용 금지" },
  iss: { name: "ISS 외삽 시험", mode: "ISS FICTIONAL EXTRAPOLATION", note: "실제 ISS 위치 아님" },
  operational: { name: "실제 운용 위성", mode: "PADO-SAT ORBIT PREVIEW", note: "FDS 입력 대기" }
});
const orbitState = {
  data: null, samples: [], stations: [], time: Date.now(), lon: 127, lat: 24, zoom: 1,
  target: "synthetic", requestId: 0, footprintElevationDeg: 0,
  image: new Image(), gl: null, program: null, texture: null,
  lastUi: 0, nightStamp: -1, nightCanvas: document.createElement("canvas")
};

const orbitEl = (id) => document.getElementById(id);
const rad = (degrees) => degrees * Math.PI / 180;
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
const formattedUtc = (time) => new Date(time).toISOString().replace("T", " ").slice(0, 19) + " UTC";

function orbitSunApprox(time) {
  const days = time / 86400000 + 2440587.5 - 2451545;
  const meanLongitude = rad((280.460 + 0.9856474 * days) % 360);
  const anomaly = rad((357.528 + 0.9856003 * days) % 360);
  const longitude = meanLongitude + rad(1.915) * Math.sin(anomaly) + rad(0.020) * Math.sin(2 * anomaly);
  const obliquity = rad(23.439 - 0.0000004 * days);
  const ra = Math.atan2(Math.cos(obliquity) * Math.sin(longitude), Math.cos(longitude));
  const dec = Math.asin(Math.sin(obliquity) * Math.sin(longitude));
  const gst = rad((280.46061837 + 360.98564736629 * days) % 360);
  const subsolar = ra - gst;
  return [Math.cos(dec) * Math.cos(subsolar), Math.cos(dec) * Math.sin(subsolar), Math.sin(dec)];
}

function parseOrbitData(input) {
  if (!input || input.schema !== "fds.orbit-view.v1" || !Array.isArray(input.samples) ||
      input.samples.length < 2 || input.samples.length > 30000 ||
      !Array.isArray(input.stations) || input.stations.length !== 2 ||
      !Number.isInteger(input.norad_id) || !Number.isFinite(input.orbital_period_s) ||
      input.orbital_period_s < 3000 || input.orbital_period_s > 86400) {
    throw new Error("FDS 궤도 보기 파일 형식이 올바르지 않습니다.");
  }
  const start = Date.parse(input.start_utc), end = Date.parse(input.end_utc);
  const inputEpoch = Date.parse(input.input_epoch_utc);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start || end - start > 72 * 3600000) {
    throw new Error("FDS 예측 기간이 올바르지 않습니다.");
  }
  if (!Number.isFinite(inputEpoch) || inputEpoch > start || start - inputEpoch >= 72 * 3600000) {
    throw new Error("FDS 입력 시각이 올바르지 않습니다.");
  }
  let previous = -Infinity;
  const samples = input.samples.map((row) => {
    if (!Array.isArray(row) || row.length !== 10) throw new Error("FDS 위치 표본이 손상됐습니다.");
    const t = Date.parse(row[0]);
    if (!Number.isFinite(t) || t <= previous || row.slice(1).some((number) => !Number.isFinite(number))) {
      throw new Error("FDS 위치 표본의 시각 또는 좌표가 손상됐습니다.");
    }
    previous = t;
    const distance = Math.hypot(row[1], row[2], row[3]);
    if (distance < 6000000 || distance > 100000000 || Math.abs(row[4]) > 180 ||
        Math.abs(row[5]) > 90 || row[6] < -500000 || row[6] > 100000000 ||
        Math.abs(Math.hypot(row[7], row[8], row[9]) - 1) > 0.02) {
      throw new Error("FDS 위치 표본이 허용 범위를 벗어났습니다.");
    }
    return { t, xyz: row.slice(1, 4), lon: row[4], lat: row[5], alt: row[6], sun: row.slice(7, 10) };
  });
  if (Math.abs(samples[0].t - start) > 1000 || Math.abs(samples.at(-1).t - end) > 1000) {
    throw new Error("FDS 표본 범위가 예측 기간과 일치하지 않습니다.");
  }
  const ids = new Set();
  const stations = input.stations.map((station) => {
    if (!station || !["yonsei", "kari"].includes(station.id) || ids.has(station.id) ||
        !Number.isFinite(station.latitude_deg) || Math.abs(station.latitude_deg) > 90 ||
        !Number.isFinite(station.longitude_deg) || Math.abs(station.longitude_deg) > 180 ||
        !Number.isFinite(station.altitude_m) || !Number.isFinite(station.minimum_elevation_deg) ||
        station.minimum_elevation_deg < 0 || station.minimum_elevation_deg > 90 ||
        !Array.isArray(station.passes) || station.passes.length > 200) {
      throw new Error("FDS 안테나 설정이 올바르지 않습니다.");
    }
    ids.add(station.id);
    const passes = station.passes.map((entry) => {
      const aos = Date.parse(entry.aos_utc), los = Date.parse(entry.los_utc);
      if (!Number.isFinite(aos) || !Number.isFinite(los) || aos < start - 1000 ||
          los > end + 1000 || aos >= los || !Number.isFinite(entry.max_elevation_deg)) {
        throw new Error("FDS 컨택 시각이 올바르지 않습니다.");
      }
      return { aos, los, peak: Date.parse(entry.peak_utc), max: entry.max_elevation_deg };
    });
    return { ...station, passes };
  });
  return { info: input, samples, stations, start, end, inputEpoch };
}

function setOrbitData(input, label) {
  const parsed = parseOrbitData(input);
  if ((orbitState.target === "synthetic" && input.norad_id !== 99999) ||
      (orbitState.target === "iss" && input.norad_id !== 25544) ||
      (orbitState.target === "operational" && [99999, 25544].includes(input.norad_id))) {
    throw new Error("선택한 위성과 FDS 결과의 식별자가 일치하지 않습니다.");
  }
  orbitState.data = parsed;
  orbitState.samples = parsed.samples;
  orbitState.stations = parsed.stations;
  orbitState.time = Date.now();
  const target = ORBIT_TARGETS[orbitState.target];
  orbitEl("orbit-mode-label").textContent = target.mode;
  orbitEl("orbit-sidebar-mode").textContent = target.name;
  orbitEl("orbit-sidebar-note").textContent = target.note;
  orbitEl("orbit-source-note").textContent =
    `${orbitState.target === "synthetic" ? "가상 PADO-SAT (500 km SSO · LTAN 12h)" : orbitState.target === "iss" ? "2026년 고정 ISS 참조 TLE를 장기 외삽·재적합한 가상 위치 (실제 ISS 아님)" : `${target.name} · NORAD ${input.norad_id}`} · ${input.source} / ${input.model} · 계산 입력 epoch ${formattedUtc(parsed.inputEpoch)} · ${orbitState.target === "operational" ? "안테나 설정을 확인하세요. " : "안테나 좌표는 시험용 근삿값입니다. "}현재 산출물은 운용 판단에 사용하지 마세요.`;
  orbitEl("orbit-data-state").textContent = `${label} · ${isCurrentData() ? "현재 위치 표시 가능" : "현재 유효한 입력·예측 범위 없음"}`;
  orbitEl("orbit-target-status").textContent = isCurrentData() ?
    orbitState.target === "iss" ? "가상 외삽 위치 표시 중" : "현재 위치 표시 중" : "FDS 결과 갱신 필요";
  orbitEl("orbit-model").textContent = `${target.name} · ${input.source} / ${input.model}`;
  orbitEl("orbit-period").textContent = `${(input.orbital_period_s / 60).toFixed(1)} 분`;
  orbitEl("orbit-coverage").textContent = `${Math.round((parsed.end - parsed.start) / 3600000)} 시간`;
  orbitEl("api-error").hidden = true;
  orbitRender(true);
}

function clearOrbitData(message) {
  orbitState.data = null;
  orbitState.samples = [];
  orbitState.stations = [];
  const target = ORBIT_TARGETS[orbitState.target];
  orbitEl("orbit-mode-label").textContent = target.mode;
  orbitEl("orbit-sidebar-mode").textContent = target.name;
  orbitEl("orbit-sidebar-note").textContent = target.note;
  orbitEl("orbit-source-note").textContent = `${target.name} · ${message}`;
  orbitEl("orbit-target-status").textContent = message;
  orbitEl("orbit-data-state").textContent = message;
  orbitEl("orbit-model").textContent = "—";
  orbitEl("orbit-period").textContent = "—";
  orbitEl("orbit-coverage").textContent = "—";
  orbitRender(true);
}

function isCurrentData() {
  const data = orbitState.data, now = orbitState.time;
  return !!data && now >= data.start && now <= data.end &&
    now >= data.inputEpoch && now - data.inputEpoch < 72 * 3600000;
}

function sampleAt(time) {
  const rows = orbitState.samples;
  if (!rows.length) return null;
  const t = clamp(time, rows[0].t, rows.at(-1).t);
  let lo = 0, hi = rows.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (rows[mid].t <= t) lo = mid; else hi = mid;
  }
  const a = rows[lo], b = rows[hi], f = (t - a.t) / (b.t - a.t);
  const interpolate = (x, y) => x + (y - x) * f;
  const xyz = a.xyz.map((value, index) => interpolate(value, b.xyz[index]));
  const sun = a.sun.map((value, index) => interpolate(value, b.sun[index]));
  let lonDelta = b.lon - a.lon;
  if (lonDelta > 180) lonDelta -= 360;
  if (lonDelta < -180) lonDelta += 360;
  let lon = a.lon + lonDelta * f;
  if (lon > 180) lon -= 360;
  if (lon < -180) lon += 360;
  return { t, xyz, sun, lon, lat: interpolate(a.lat, b.lat), alt: interpolate(a.alt, b.alt) };
}

function activePass(station, time) {
  return station.passes.find((pass) => pass.aos <= time && time < pass.los) || null;
}

function stationEcef(station) {
  const lat = rad(station.latitude_deg), lon = rad(station.longitude_deg);
  const a = ORBIT_EARTH_RADIUS_M, e2 = 0.00669437999014;
  const n = a / Math.sqrt(1 - e2 * Math.sin(lat) ** 2);
  return [(n + station.altitude_m) * Math.cos(lat) * Math.cos(lon),
    (n + station.altitude_m) * Math.cos(lat) * Math.sin(lon),
    (n * (1 - e2) + station.altitude_m) * Math.sin(lat)];
}

function stationObservation(station, satellite) {
  const site = stationEcef(station), delta = satellite.map((value, index) => value - site[index]);
  const lat = rad(station.latitude_deg), lon = rad(station.longitude_deg);
  const east = [-Math.sin(lon), Math.cos(lon), 0];
  const north = [-Math.sin(lat) * Math.cos(lon), -Math.sin(lat) * Math.sin(lon), Math.cos(lat)];
  const up = [Math.cos(lat) * Math.cos(lon), Math.cos(lat) * Math.sin(lon), Math.sin(lat)];
  return {
    az: (Math.atan2(dot(delta, east), dot(delta, north)) * 180 / Math.PI + 360) % 360,
    el: Math.asin(clamp(dot(delta, up) / Math.hypot(...delta), -1, 1)) * 180 / Math.PI
  };
}

function orbitBasis() {
  const lat = rad(orbitState.lat), lon = rad(orbitState.lon);
  return {
    forward: [Math.cos(lat) * Math.cos(lon), Math.cos(lat) * Math.sin(lon), Math.sin(lat)],
    right: [-Math.sin(lon), Math.cos(lon), 0],
    up: [-Math.sin(lat) * Math.cos(lon), -Math.sin(lat) * Math.sin(lon), Math.cos(lat)]
  };
}

function shader(gl, type, source) {
  const result = gl.createShader(type);
  gl.shaderSource(result, source); gl.compileShader(result);
  if (!gl.getShaderParameter(result, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(result));
  return result;
}

function setupGlobe() {
  const canvas = orbitEl("orbit-earth");
  const gl = canvas.getContext("webgl", { alpha: true, antialias: true, preserveDrawingBuffer: false });
  if (!gl) return false;
  const vertex = shader(gl, gl.VERTEX_SHADER,
    "attribute vec2 aPosition; void main(){gl_Position=vec4(aPosition,0.0,1.0);}");
  const fragment = shader(gl, gl.FRAGMENT_SHADER, `
    precision highp float;
    uniform vec2 uResolution; uniform float uRadius;
    uniform vec3 uForward, uRight, uUp, uSun;
    uniform sampler2D uEarth;
    const float PI = 3.141592653589793;
    void main(){
      vec2 p=(gl_FragCoord.xy-uResolution*0.5)/uRadius;
      float r2=dot(p,p); if(r2>1.0) discard;
      vec3 normal=normalize(uRight*p.x+uUp*p.y+uForward*sqrt(1.0-r2));
      float longitude=atan(normal.y,normal.x);
      float latitude=asin(clamp(normal.z,-1.0,1.0));
      vec3 land=texture2D(uEarth,vec2(fract(0.5+longitude/(2.0*PI)),0.5-latitude/PI)).rgb;
      float solar=dot(normal,normalize(uSun));
      float daylight=smoothstep(-0.13,0.12,solar);
      vec3 night=land*vec3(0.08,0.15,0.24);
      vec3 day=land*(0.35+0.70*max(solar,0.0));
      vec3 color=mix(night,day,daylight);
      color+=vec3(0.025,0.075,0.11)*pow(1.0-sqrt(1.0-r2),4.0);
      gl_FragColor=vec4(color,1.0);
    }
  `);
  const program = gl.createProgram();
  gl.attachShader(program, vertex); gl.attachShader(program, fragment); gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
  const buffer = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1, 1,-1, -1,1, 1,1]), gl.STATIC_DRAW);
  gl.useProgram(program);
  const position = gl.getAttribLocation(program, "aPosition");
  gl.enableVertexAttribArray(position); gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
  const texture = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, texture);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE,
    new Uint8Array([32, 66, 88, 255]));
  orbitState.gl = gl; orbitState.program = program; orbitState.texture = texture;
  return true;
}

function sizeCanvases() {
  const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
  for (const [id, wrapId] of [["orbit-earth", "orbit-canvas-wrap"],
    ["orbit-overlay", "orbit-canvas-wrap"], ["orbit-flat", "orbit-flat-wrap"]]) {
    const canvas = orbitEl(id);
    const wrap = orbitEl(wrapId);
    const width = Math.round(wrap.clientWidth * pixelRatio);
    const height = Math.round((id === "orbit-flat" ? canvas.clientHeight : wrap.clientHeight) * pixelRatio);
    if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
  }
}

function earthRadius(canvas) { return Math.min(canvas.width * 0.36, canvas.height * 0.46) * orbitState.zoom; }

function drawGlobe(sun) {
  const gl = orbitState.gl, canvas = orbitEl("orbit-earth");
  if (!gl) return;
  const basis = orbitBasis();
  gl.viewport(0, 0, canvas.width, canvas.height);
  gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
  gl.useProgram(orbitState.program); gl.bindTexture(gl.TEXTURE_2D, orbitState.texture);
  const uniform = (name) => gl.getUniformLocation(orbitState.program, name);
  gl.uniform2f(uniform("uResolution"), canvas.width, canvas.height);
  gl.uniform1f(uniform("uRadius"), earthRadius(canvas));
  gl.uniform3fv(uniform("uForward"), basis.forward);
  gl.uniform3fv(uniform("uRight"), basis.right);
  gl.uniform3fv(uniform("uUp"), basis.up);
  gl.uniform3fv(uniform("uSun"), sun);
  gl.uniform1i(uniform("uEarth"), 0);
  gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
}

function refreshNightCanvas(sun, time) {
  const stamp = Math.floor(time / 60000);
  if (orbitState.nightStamp === stamp) return;
  orbitState.nightStamp = stamp;
  const canvas = orbitState.nightCanvas;
  canvas.width = 180; canvas.height = 90;
  const context = canvas.getContext("2d"), pixels = context.createImageData(180, 90);
  for (let y = 0; y < 90; y++) for (let x = 0; x < 180; x++) {
    const lat = Math.PI / 2 - (y + .5) / 90 * Math.PI;
    const lon = (x + .5) / 180 * 2 * Math.PI - Math.PI;
    const normal = [Math.cos(lat) * Math.cos(lon), Math.cos(lat) * Math.sin(lon), Math.sin(lat)];
    const light = clamp((dot(normal, sun) + .1) / .2, 0, 1);
    const index = (y * 180 + x) * 4;
    pixels.data[index] = 2; pixels.data[index + 1] = 10; pixels.data[index + 2] = 27;
    pixels.data[index + 3] = Math.round((1 - light) * 215);
  }
  context.putImageData(pixels, 0, 0);
}

function drawFlat(sun, time) {
  const canvas = orbitEl("orbit-flat"), context = canvas.getContext("2d");
  const w = canvas.width, h = canvas.height;
  context.clearRect(0, 0, w, h);
  const rect = flatRect(canvas);
  if (orbitState.image.complete && orbitState.image.naturalWidth) {
    context.drawImage(orbitState.image, rect.x, rect.y, rect.w, rect.h);
  }
  refreshNightCanvas(sun, time);
  context.drawImage(orbitState.nightCanvas, rect.x, rect.y, rect.w, rect.h);
  context.strokeStyle = "#c6e4e03a"; context.lineWidth = 1;
  for (let longitude = 30; longitude < 360; longitude += 30) {
    const x = rect.x + longitude / 360 * rect.w;
    context.beginPath(); context.moveTo(x, rect.y); context.lineTo(x, rect.y + rect.h); context.stroke();
  }
  for (let latitude = 30; latitude < 180; latitude += 30) {
    const y = rect.y + latitude / 180 * rect.h;
    context.beginPath(); context.moveTo(rect.x, y); context.lineTo(rect.x + rect.w, y); context.stroke();
  }
}

function project3d(xyz, canvas) {
  const basis = orbitBasis(), scaled = xyz.map((value) => value / ORBIT_EARTH_RADIUS_M);
  const x = dot(scaled, basis.right), y = dot(scaled, basis.up), depth = dot(scaled, basis.forward);
  const radius = earthRadius(canvas), surface = Math.sqrt(Math.max(0, 1 - x * x - y * y));
  return { x: canvas.width / 2 + x * radius, y: canvas.height / 2 - y * radius,
    visible: x * x + y * y >= 1 || depth >= surface - .003 };
}

function flatRect(canvas) {
  const width = Math.min(canvas.width, canvas.height * 2);
  const height = width / 2;
  return { x: (canvas.width - width) / 2, y: (canvas.height - height) / 2, w: width, h: height };
}

function projectFlat(sample, canvas) {
  const rect = flatRect(canvas);
  return { x: rect.x + (sample.lon + 180) / 360 * rect.w,
    y: rect.y + (90 - sample.lat) / 180 * rect.h, visible: true };
}

function routePoints() {
  if (!isCurrentData()) return [];
  const end = Math.min(orbitState.data.end,
    orbitState.time + 3 * orbitState.data.info.orbital_period_s * 1000);
  const points = [sampleAt(orbitState.time)];
  for (const sample of orbitState.samples) {
    if (sample.t > orbitState.time && sample.t < end) points.push(sample);
  }
  points.push(sampleAt(end));
  return points;
}

function drawSegment(context, start, end, color, width, flat, lonDelta) {
  if (!start.visible || !end.visible || (flat && lonDelta > 180)) return;
  context.strokeStyle = color; context.lineWidth = width; context.beginPath();
  context.moveTo(start.x, start.y); context.lineTo(end.x, end.y); context.stroke();
}

function drawMarker(context, point, color, label, radius = 5, labelOffset = [6, -3]) {
  if (!point.visible) return;
  context.fillStyle = color; context.strokeStyle = "#061621"; context.lineWidth = 2;
  context.beginPath(); context.arc(point.x, point.y, radius, 0, 2 * Math.PI);
  context.fill(); context.stroke();
  context.fillStyle = "#eff9fb"; context.font = "bold 11px Arial";
  context.fillText(label, point.x + radius + labelOffset[0], point.y + labelOffset[1]);
}

function visibilityFootprint(xyz, minimumElevationDeg = 0, segments = 256) {
  if (!Array.isArray(xyz) || xyz.length !== 3 || !xyz.every(Number.isFinite) ||
      !Number.isFinite(minimumElevationDeg) || minimumElevationDeg < 0 || minimumElevationDeg >= 90 ||
      !Number.isInteger(segments) || segments < 16 || segments > 2048) return null;
  const distance = Math.hypot(...xyz);
  if (!Number.isFinite(distance) || distance <= ORBIT_EARTH_RADIUS_M) return null;
  const elevation = rad(minimumElevationDeg);
  const angle = Math.acos(clamp(ORBIT_EARTH_RADIUS_M / distance * Math.cos(elevation), -1, 1)) - elevation;
  if (!Number.isFinite(angle) || angle <= 0) return null;
  const center = xyz.map(value => value / distance);
  const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
    a[0] * b[1] - a[1] * b[0]];
  const reference = Math.abs(center[2]) < .9 ? [0, 0, 1] : [1, 0, 0];
  const rawRight = cross(reference, center), length = Math.hypot(...rawRight);
  const right = rawRight.map(value => value / length), up = cross(center, right);
  const points = [];
  for (let index = 0; index <= segments; index++) {
    const phase = index % segments / segments * 2 * Math.PI;
    const unit = center.map((value, axis) => Math.cos(angle) * value + Math.sin(angle) *
      (Math.cos(phase) * right[axis] + Math.sin(phase) * up[axis]));
    points.push({xyz: unit.map(value => value * ORBIT_EARTH_RADIUS_M),
      lon: Math.atan2(unit[1], unit[0]) * 180 / Math.PI,
      lat: Math.asin(clamp(unit[2], -1, 1)) * 180 / Math.PI});
  }
  return {points, angularRadiusRad: angle, groundRadiusM: angle * ORBIT_EARTH_RADIUS_M};
}

function footprintVisibleSegment(a, b, forward) {
  const first = dot(a.xyz, forward), second = dot(b.xyz, forward);
  if (first < 0 && second < 0) return null;
  if (first >= 0 && second >= 0) return [a, b];
  const fraction = first / (first - second);
  const xyz = a.xyz.map((value, index) => value + fraction * (b.xyz[index] - value));
  const magnitude = Math.hypot(...xyz);
  if (!magnitude) return null;
  const edge = {xyz: xyz.map(value => value / magnitude * ORBIT_EARTH_RADIUS_M)};
  return first >= 0 ? [a, edge] : [edge, b];
}

function footprintFlatSegments(a, b) {
  // Unwrap the short arc before splitting it at the +/-180 degree map edges.
  let longitude = b.lon;
  if (longitude - a.lon > 180) longitude -= 360;
  if (longitude - a.lon < -180) longitude += 360;
  if (longitude >= -180 && longitude <= 180) return [[a, {...b, lon: longitude}]];
  const edge = longitude > 180 ? 180 : -180;
  const fraction = (edge - a.lon) / (longitude - a.lon);
  const latitude = a.lat + fraction * (b.lat - a.lat);
  return [[a, {lon: edge, lat: latitude}], [{lon: -edge, lat: latitude}, b]];
}

function drawVisibilityFootprint(context, current, canvas, flat, scale) {
  const footprint = visibilityFootprint(current?.xyz, orbitState.footprintElevationDeg);
  if (!footprint) return;
  const forward = flat ? null : orbitBasis().forward;
  context.strokeStyle = "#ffffff"; context.lineWidth = 1.6 * scale; context.beginPath();
  for (let index = 1; index < footprint.points.length; index++) {
    const a = footprint.points[index - 1], b = footprint.points[index];
    const segments = flat ? footprintFlatSegments(a, b) : [footprintVisibleSegment(a, b, forward)].filter(Boolean);
    for (const segment of segments) {
      const [start, end] = segment.map(point => flat ? projectFlat(point, canvas) : project3d(point.xyz, canvas));
      context.moveTo(start.x, start.y); context.lineTo(end.x, end.y);
    }
  }
  context.stroke();
}

function drawOverlay(canvas, flat) {
  const context = canvas.getContext("2d");
  if (!flat) context.clearRect(0, 0, canvas.width, canvas.height);
  if (!isCurrentData()) return;
  const points = routePoints(), current = points[0], projected = points.map((point) =>
    flat ? projectFlat(point, canvas) : project3d(point.xyz, canvas));
  const scale = Math.min(window.devicePixelRatio || 1, 2);
  drawVisibilityFootprint(context, current, canvas, flat, scale);
  for (let index = 1; index < points.length; index++) {
    const a = projected[index - 1], b = projected[index];
    const lonDelta = Math.abs(points[index].lon - points[index - 1].lon);
    drawSegment(context, a, b, "#74cbe0d9", 2 * scale, flat, lonDelta);
    const midpoint = (points[index].t + points[index - 1].t) / 2;
    for (const station of orbitState.stations) {
      if (activePass(station, midpoint)) {
        drawSegment(context, a, b, station.id === "yonsei" ? "#e5ae58" : "#8adca8",
          4 * scale, flat, lonDelta);
      }
    }
  }
  for (const station of orbitState.stations) {
    const site = stationEcef(station), point = flat
      ? projectFlat({ lon: station.longitude_deg, lat: station.latitude_deg }, canvas)
      : project3d(site, canvas);
    const color = station.id === "yonsei" ? "#e5ae58" : "#8adca8";
    if (activePass(station, orbitState.time) && point.visible && projected[0].visible && !flat) {
      drawSegment(context, point, projected[0], color + "c9", 1.5 * scale, false, 0);
    }
    drawMarker(context, point, color, station.id === "yonsei" ? "YONSEI / UHF" : "KARI / S",
      4 * scale, station.id === "yonsei" ? [7 * scale, -10 * scale] : [7 * scale, 14 * scale]);
  }
  drawMarker(context, projected[0], "#f4f7fb",
    orbitState.target === "synthetic" ? "PADO-SAT SIM / NOW" :
      orbitState.target === "iss" ? "ISS TEST / NOW" : `NORAD ${orbitState.data.info.norad_id}`, 6 * scale);
  if (flat) {
    context.fillStyle = "#eff9fb"; context.font = `${11 * scale}px Arial`;
    context.fillText("N", 8 * scale, 15 * scale);
  }
}

function drawPolar(canvas, station, pass, now) {
  const scale = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.round(canvas.clientWidth * scale);
  canvas.height = Math.round(canvas.clientHeight * scale);
  const context = canvas.getContext("2d"), w = canvas.width, h = canvas.height;
  const cx = w / 2, cy = h / 2, radius = Math.min(w * .37, h * .39);
  const color = station.id === "yonsei" ? "#e5ae58" : "#8adca8";
  context.clearRect(0, 0, w, h);
  context.strokeStyle = "#386072"; context.lineWidth = scale;
  for (const elevation of [0, 30, 60]) {
    context.beginPath(); context.arc(cx, cy, radius * (90 - elevation) / 90, 0, Math.PI * 2); context.stroke();
  }
  context.beginPath(); context.moveTo(cx - radius, cy); context.lineTo(cx + radius, cy);
  context.moveTo(cx, cy - radius); context.lineTo(cx, cy + radius); context.stroke();
  context.fillStyle = "#b8d5df"; context.font = `${10 * scale}px Consolas,monospace`;
  context.textAlign = "center"; context.textBaseline = "middle";
  for (const [label, x, y] of [["N", cx, cy - radius - 11 * scale],
    ["E", cx + radius + 11 * scale, cy], ["S", cx, cy + radius + 11 * scale],
    ["W", cx - radius - 11 * scale, cy]]) context.fillText(label, x, y);
  context.fillStyle = "#a0bfcb"; context.textAlign = "left";
  for (const elevation of [0, 30, 60]) {
    const distance = radius * (90 - elevation) / 90;
    context.fillText(`${elevation}°`, cx + distance / Math.SQRT2 + 3 * scale,
      cy - distance / Math.SQRT2 - 4 * scale);
  }
  if (!pass) {
    context.fillStyle = "#a9bdc6"; context.textAlign = "center";
    context.fillText("예측 기간 내 PASS 없음", cx, cy + radius / 2);
    return;
  }
  const hasPeak = Number.isFinite(pass.peak) && pass.aos <= pass.peak && pass.peak <= pass.los &&
    Number.isFinite(pass.max) && pass.max >= 0 && pass.max <= 90;
  const point = (time, elevation = null) => {
    const observation = stationObservation(station, sampleAt(time).xyz);
    const distance = radius * (90 - clamp(elevation ?? observation.el, 0, 90)) / 90;
    return { x: cx + Math.sin(rad(observation.az)) * distance,
      y: cy - Math.cos(rad(observation.az)) * distance };
  };
  const times = [pass.aos];
  for (let t = pass.aos + 30000; t < pass.los; t += 30000) times.push(t);
  times.push(pass.los);
  if (hasPeak && !times.includes(pass.peak)) times.push(pass.peak);
  times.sort((a, b) => a - b);
  const points = times.map(time => point(time, hasPeak && time === pass.peak ? pass.max : null));
  context.strokeStyle = color; context.lineWidth = 2.5 * scale;
  context.beginPath(); context.moveTo(points[0].x, points[0].y);
  for (const p of points.slice(1)) context.lineTo(p.x, p.y);
  context.stroke();
  const events = [["AOS", points[0], -9], ["LOS", points.at(-1), 11]];
  if (hasPeak) events.push(["MAX", point(pass.peak, pass.max), -11]);
  const labelBoxes = [];
  for (const [label, p, dy] of events) {
    context.fillStyle = color; context.beginPath(); context.arc(p.x, p.y, 3.5 * scale, 0, Math.PI * 2); context.fill();
    context.fillStyle = "#e9f5f6"; context.textAlign = "left";
    const width = label.length * 6 * scale, height = 12 * scale;
    const candidates = [[p.x + 6 * scale, p.y + dy * scale],
      [p.x + 6 * scale, p.y - dy * scale],
      [p.x - width - 6 * scale, p.y + dy * scale],
      [p.x - width - 6 * scale, p.y - dy * scale],
      ...[-28, 28, -44, 44].map(offset => [p.x - width / 2, p.y + offset * scale])];
    const positions = candidates.map(([x, y]) => {
      x = clamp(x, 4 * scale, w - width - 4 * scale);
      y = clamp(y, 8 * scale, h - 8 * scale);
      return {x, y, left: x - scale, right: x + width + scale,
        top: y - height / 2 - scale, bottom: y + height / 2 + scale};
    });
    const chosen = positions.find(box => labelBoxes.every(previous =>
      box.right <= previous.left || box.left >= previous.right ||
      box.bottom <= previous.top || box.top >= previous.bottom)) || positions[0];
    labelBoxes.push(chosen);
    context.strokeStyle = "#8eacb9"; context.lineWidth = .7 * scale;
    context.beginPath(); context.moveTo(p.x, p.y);
    context.lineTo(clamp(p.x, chosen.left, chosen.right), chosen.y); context.stroke();
    context.fillText(label, chosen.x, chosen.y);
  }
  if (pass.aos <= now && now < pass.los) {
    const current = point(now);
    context.fillStyle = "#ffffff"; context.strokeStyle = color; context.lineWidth = 2 * scale;
    context.beginPath(); context.arc(current.x, current.y, 6 * scale, 0, Math.PI * 2);
    context.fill(); context.stroke();
  }
}

function updateOrbitUi() {
  const has = isCurrentData(), current = has ? sampleAt(orbitState.time) : null;
  orbitEl("orbit-display-time").textContent = formattedUtc(orbitState.time);
  if (!has) {
    orbitEl("orbit-coordinates").textContent = "—";
    orbitEl("orbit-altitude").textContent = "—";
    orbitEl("orbit-stations").textContent = "현재 유효한 FDS 입력이 없습니다.";
    orbitEl("orbit-passes").textContent = "예측 데이터 없음";
    if (orbitState.data) {
      orbitEl("orbit-data-state").textContent = "현재 시각이 FDS 예측 범위 밖이거나 입력이 72시간 이상 경과했습니다.";
      orbitEl("orbit-target-status").textContent = "FDS 결과 갱신 필요";
    }
    return;
  }
  orbitEl("orbit-coordinates").textContent = `${Math.abs(current.lat).toFixed(3)}°${current.lat >= 0 ? "N" : "S"} · ${Math.abs(current.lon).toFixed(3)}°${current.lon >= 0 ? "E" : "W"}`;
  orbitEl("orbit-altitude").textContent = `${(current.alt / 1000).toFixed(1)} km`;
  const stationContainer = orbitEl("orbit-stations"); stationContainer.replaceChildren();
  for (const station of orbitState.stations) {
    const contact = activePass(station, orbitState.time);
    const section = document.createElement("div"); section.className = "orbit-station";
    const title = document.createElement("div"); title.className = "orbit-station-title";
    const name = document.createElement("strong");
    name.textContent = station.id === "yonsei" ? "연세대 · UHF" : "대전 항우연 · S-band";
    const badge = document.createElement("em"); badge.className = contact ? "contact" : "no-contact";
    badge.textContent = contact ? "CONTACT" : "NO CONTACT";
    title.append(name, badge);
    const detail = document.createElement("p");
    const observation = stationObservation(station, current.xyz);
    detail.textContent = `현재 방위 ${observation.az.toFixed(1)}° · 고각 ≈${observation.el.toFixed(1)}° / 최소 ${station.minimum_elevation_deg.toFixed(1)}°`;
    const note = document.createElement("small");
    const next = contact || station.passes.find((pass) => pass.aos > orbitState.time);
    note.textContent = contact ? `현재 PASS · LOS ${formattedUtc(contact.los)}` :
      next ? `다음 PASS · AOS ${formattedUtc(next.aos)}` : "예측 기간 내 PASS 없음";
    const polar = document.createElement("canvas"); polar.className = "orbit-polar";
    polar.setAttribute("aria-label", `${name.textContent} ${contact ? "현재" : "다음"} PASS 방위각·고각 polar plot`);
    const caption = document.createElement("div"); caption.className = "orbit-polar-caption";
    if (next) {
      const hasPeak = Number.isFinite(next.peak) && next.aos <= next.peak && next.peak <= next.los &&
        Number.isFinite(next.max) && next.max >= 0 && next.max <= 90;
      const labels = [`AOS · ${formattedUtc(next.aos)}`,
        hasPeak ? `MAX ${next.max.toFixed(1)}° · ${formattedUtc(next.peak)}` : "MAX · 정점 자료 없음",
        `LOS · ${formattedUtc(next.los)}`];
      for (const text of labels) {
        const row = document.createElement("span"); row.textContent = text; caption.append(row);
      }
    }
    section.append(title, detail, note, polar, caption); stationContainer.append(section);
    drawPolar(polar, station, next, orbitState.time);
  }
  const passes = orbitState.stations.flatMap((station) => station.passes
    .filter((pass) => pass.los > orbitState.time)
    .map((pass) => ({ station, pass })))
    .sort((a, b) => a.pass.aos - b.pass.aos).slice(0, 5);
  const container = orbitEl("orbit-passes"); container.replaceChildren();
  if (!passes.length) {
    const empty = document.createElement("p"); empty.className = "orbit-empty";
    empty.textContent = "현재 예측 기간에 남은 컨택이 없습니다."; container.append(empty);
  }
  for (const { station, pass } of passes) {
    const row = document.createElement("div"); row.className = "orbit-pass";
    const name = document.createElement("strong"); name.textContent = station.id === "yonsei" ? "연세대 UHF" : "대전 S-band";
    const elevation = document.createElement("span"); elevation.textContent = `최고 ${pass.max.toFixed(1)}°`;
    const time = document.createElement("small"); time.textContent = `${formattedUtc(pass.aos)} → ${formattedUtc(pass.los)}`;
    row.append(name, elevation, time); container.append(row);
  }
}

function orbitRender(forceUi = false) {
  sizeCanvases();
  const sample = isCurrentData() ? sampleAt(orbitState.time) : null;
  const sun = sample ? sample.sun : orbitSunApprox(orbitState.time);
  drawGlobe(sun);
  drawOverlay(orbitEl("orbit-overlay"), false);
  drawFlat(sun, orbitState.time);
  drawOverlay(orbitEl("orbit-flat"), true);
  if (forceUi || performance.now() - orbitState.lastUi > 500) {
    updateOrbitUi(); orbitState.lastUi = performance.now();
  }
}

function orbitAnimation(now) {
  orbitState.time = Date.now();
  if (now - (orbitAnimation.lastPaint || 0) >= 50) {
    orbitRender(); orbitAnimation.lastPaint = now;
  }
  requestAnimationFrame(orbitAnimation);
}

async function readOrbitServer() {
  const target = orbitState.target;
  const requestId = ++orbitState.requestId;
  if (!orbitState.data) orbitEl("orbit-data-state").textContent = `${ORBIT_TARGETS[target].name} FDS 산출물 확인 중…`;
  try {
    const response = await fetch(`/api/fds/orbit?target=${target}`, { credentials: "same-origin", cache: "no-store" });
    if (requestId !== orbitState.requestId) return;
    if (response.status === 401) { window.location.assign("/login"); return; }
    if (!response.ok) throw new Error(response.status === 503 ?
      `${ORBIT_TARGETS[target].name}의 유효한 FDS 결과가 아직 없습니다.` : "FDS 결과를 읽지 못했습니다.");
    if (Number(response.headers.get("content-length") || 0) > 4 * 1024 * 1024) {
      throw new Error("FDS 결과 파일이 너무 큽니다.");
    }
    const input = await response.json();
    if (requestId !== orbitState.requestId) return;
    if (!orbitState.data || input.generated_utc !== orbitState.data.info.generated_utc ||
        input.input_sha256 !== orbitState.data.info.input_sha256 ||
        input.start_utc !== orbitState.data.info.start_utc) {
      setOrbitData(input, "서버 FDS 결과");
    }
  } catch (error) {
    if (requestId === orbitState.requestId) clearOrbitData(error.message);
  }
}

function chooseOrbitTarget(target) {
  if (!Object.hasOwn(ORBIT_TARGETS, target) || target === orbitState.target) return;
  orbitState.target = target;
  ++orbitState.requestId;
  clearOrbitData(`${ORBIT_TARGETS[target].name} FDS 결과 확인 중…`);
  readOrbitServer();
}

function orbitControls() {
  orbitEl("orbit-target").addEventListener("change", (event) => chooseOrbitTarget(event.target.value));
  orbitEl("orbit-footprint-elevation")?.addEventListener("change", (event) => {
    const elevation = Number(event.target.value);
    if ([0, 5, 10, 20].includes(elevation)) {
      orbitState.footprintElevationDeg = elevation;
      orbitRender(true);
    }
  });
  orbitEl("orbit-center").addEventListener("click", () => {
    const sample = sampleAt(orbitState.time);
    if (sample && isCurrentData()) { orbitState.lon = sample.lon; orbitState.lat = sample.lat; orbitRender(true); }
  });
  orbitEl("orbit-zoom-in").addEventListener("click", () => { orbitState.zoom = clamp(orbitState.zoom * 1.2, .7, 1.8); orbitRender(); });
  orbitEl("orbit-zoom-out").addEventListener("click", () => { orbitState.zoom = clamp(orbitState.zoom / 1.2, .7, 1.8); orbitRender(); });
  orbitEl("orbit-reload").addEventListener("click", readOrbitServer);
  const wrap = orbitEl("orbit-canvas-wrap");
  let dragging = null;
  wrap.addEventListener("pointerdown", (event) => {
    if (event.target.tagName !== "CANVAS") return;
    dragging = { x: event.clientX, y: event.clientY };
    wrap.setPointerCapture(event.pointerId);
  });
  wrap.addEventListener("pointermove", (event) => {
    if (!dragging) return;
    orbitState.lon -= (event.clientX - dragging.x) * .27;
    orbitState.lat = clamp(orbitState.lat + (event.clientY - dragging.y) * .27, -85, 85);
    dragging = { x: event.clientX, y: event.clientY };
    orbitRender();
  });
  wrap.addEventListener("pointerup", () => { dragging = null; });
  wrap.addEventListener("pointercancel", () => { dragging = null; });
  wrap.addEventListener("wheel", (event) => {
    event.preventDefault(); orbitState.zoom = clamp(orbitState.zoom * (event.deltaY < 0 ? 1.1 : .9), .7, 1.8);
    orbitRender();
  }, { passive: false });
  new ResizeObserver(() => orbitRender()).observe(wrap);
  new ResizeObserver(() => orbitRender()).observe(orbitEl("orbit-flat-wrap"));
}

async function initOrbitViewer() {
  orbitControls();
  try {
    if (!setupGlobe()) {
      orbitEl("orbit-data-state").textContent = "이 브라우저에서는 3D 그래픽을 사용할 수 없습니다. 2D 지상궤적은 계속 표시합니다.";
    }
  } catch (_) {
    orbitEl("orbit-data-state").textContent = "3D 그래픽을 초기화하지 못했습니다. 2D 지상궤적은 계속 표시합니다.";
  }
  orbitState.image.onload = () => {
    if (orbitState.gl) {
      const gl = orbitState.gl;
      gl.bindTexture(gl.TEXTURE_2D, orbitState.texture);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, orbitState.image);
    }
    orbitRender(true);
  };
  orbitState.image.src = "/assets/earth-blue-marble-october.jpg";
  requestAnimationFrame(orbitAnimation);
  await readOrbitServer();
  setInterval(readOrbitServer, 60000);
}
