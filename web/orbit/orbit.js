"use strict";

// The browser interpolates FDS Earth-fixed samples. It never propagates a TLE.
const ORBIT_EARTH_RADIUS_M = 6378137;
const orbitState = {
  data: null, samples: [], stations: [], time: Date.now(), live: false,
  playing: false, speed: 300, view: "3d", lon: 127, lat: 24, zoom: 1,
  image: new Image(), gl: null, program: null, texture: null,
  lastFrame: 0, lastUi: 0, nightStamp: -1, nightCanvas: document.createElement("canvas")
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
  orbitState.data = parsed;
  orbitState.samples = parsed.samples;
  orbitState.stations = parsed.stations;
  const now = Date.now();
  orbitState.live = now >= parsed.start && now <= parsed.end &&
    now - parsed.inputEpoch < 72 * 3600000;
  orbitState.time = orbitState.live ? now : parsed.start;
  orbitState.playing = false;
  orbitEl("orbit-play").textContent = "재생";
  orbitEl("orbit-timeline").disabled = false;
  orbitEl("orbit-timeline").max = String(Math.round((parsed.end - parsed.start) / 1000));
  orbitEl("orbit-source-note").textContent =
    `NORAD ${input.norad_id} · ${input.source} / ${input.model} · 입력 ${formattedUtc(parsed.inputEpoch)} · 예비 계산 결과입니다. 실제 관제 판단에는 사용하지 마세요.`;
  orbitEl("orbit-data-state").textContent = `${label} · ${orbitState.live ? "현재 위치 표시 가능" : "현재 유효한 입력·예측 범위 없음 (재생만 가능)"}`;
  orbitEl("orbit-model").textContent = `${input.source} / ${input.model}`;
  orbitEl("orbit-period").textContent = `${(input.orbital_period_s / 60).toFixed(1)} 분`;
  orbitEl("orbit-coverage").textContent = `${Math.round((parsed.end - parsed.start) / 3600000)} 시간`;
  orbitEl("orbit-window").textContent = `+3주기 ${(3 * input.orbital_period_s / 3600).toFixed(1)}h`;
  orbitEl("api-error").hidden = true;
  orbitRender(true);
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

function stationElevation(station, satellite) {
  const site = stationEcef(station), delta = satellite.map((value, index) => value - site[index]);
  const up = [Math.cos(rad(station.latitude_deg)) * Math.cos(rad(station.longitude_deg)),
    Math.cos(rad(station.latitude_deg)) * Math.sin(rad(station.longitude_deg)),
    Math.sin(rad(station.latitude_deg))];
  return Math.asin(clamp(dot(delta, up) / Math.hypot(...delta), -1, 1)) * 180 / Math.PI;
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
  const wrap = orbitEl("orbit-canvas-wrap"), pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
  for (const id of ["orbit-earth", "orbit-flat", "orbit-overlay"]) {
    const canvas = orbitEl(id);
    const width = Math.round(wrap.clientWidth * pixelRatio), height = Math.round(wrap.clientHeight * pixelRatio);
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
  if (!orbitState.data) return [];
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

function drawOverlay() {
  const canvas = orbitEl("orbit-overlay"), context = canvas.getContext("2d"), flat = orbitState.view === "2d";
  context.clearRect(0, 0, canvas.width, canvas.height);
  if (!orbitState.data) return;
  const points = routePoints(), current = points[0], projected = points.map((point) =>
    flat ? projectFlat(point, canvas) : project3d(point.xyz, canvas));
  const scale = Math.min(window.devicePixelRatio || 1, 2);
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
  drawMarker(context, projected[0], "#f4f7fb", `NORAD ${orbitState.data.info.norad_id}`, 6 * scale);
  if (flat) {
    context.fillStyle = "#eff9fb"; context.font = `${11 * scale}px Arial`;
    context.fillText("N", 8 * scale, 15 * scale);
  }
}

function updateOrbitUi() {
  const has = !!orbitState.data, current = has ? sampleAt(orbitState.time) : null;
  orbitEl("orbit-display-time").textContent = formattedUtc(orbitState.time);
  orbitEl("orbit-time-mode").textContent = orbitState.live ? "UTC NOW" : has ? "PLAYBACK / FDS" : "EARTH / UTC";
  if (!has) return;
  orbitEl("orbit-timeline").value = String(Math.round((orbitState.time - orbitState.data.start) / 1000));
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
    detail.textContent = `표시 고각 ≈${stationElevation(station, current.xyz).toFixed(1)}° / 최소 ${station.minimum_elevation_deg.toFixed(1)}°`;
    const note = document.createElement("small");
    note.textContent = contact ? `LOS ${formattedUtc(contact.los)}` : "FDS PASS 구간 밖";
    section.append(title, detail, note); stationContainer.append(section);
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
  const sample = orbitState.data ? sampleAt(orbitState.time) : null;
  const sun = sample ? sample.sun : orbitSunApprox(orbitState.time);
  if (orbitState.view === "3d") drawGlobe(sun); else drawFlat(sun, orbitState.time);
  drawOverlay();
  if (forceUi || performance.now() - orbitState.lastUi > 500) {
    updateOrbitUi(); orbitState.lastUi = performance.now();
  }
}

function orbitAnimation(now) {
  const delta = orbitState.lastFrame ? Math.min(now - orbitState.lastFrame, 1000) : 0;
  orbitState.lastFrame = now;
  if (orbitState.live && orbitState.data) {
    orbitState.time = Date.now();
    if (orbitState.time > orbitState.data.end) orbitState.live = false;
  } else if (orbitState.playing && orbitState.data) {
    orbitState.time = Math.min(orbitState.data.end, orbitState.time + delta * orbitState.speed);
    if (orbitState.time >= orbitState.data.end) {
      orbitState.playing = false; orbitEl("orbit-play").textContent = "재생";
    }
  } else if (!orbitState.data) orbitState.time = Date.now();
  if (now - (orbitAnimation.lastPaint || 0) >= 50) {
    orbitRender(); orbitAnimation.lastPaint = now;
  }
  requestAnimationFrame(orbitAnimation);
}

async function readOrbitServer() {
  orbitEl("orbit-data-state").textContent = "서버 FDS 산출물 확인 중…";
  try {
    const response = await fetch("/api/fds/orbit", { credentials: "same-origin", cache: "no-store" });
    if (response.status === 401) { window.location.assign("/login"); return; }
    if (!response.ok) throw new Error("서버에 게시된 FDS 결과가 없습니다.");
    if (Number(response.headers.get("content-length") || 0) > 4 * 1024 * 1024) {
      throw new Error("FDS 결과 파일이 너무 큽니다.");
    }
    setOrbitData(await response.json(), "서버 FDS 결과");
  } catch (error) {
    orbitEl("orbit-data-state").textContent = error.message + " 결과 파일을 선택해 확인할 수도 있습니다.";
  }
}

function setOrbitView(mode) {
  orbitState.view = mode;
  orbitEl("orbit-3d").setAttribute("aria-pressed", String(mode === "3d"));
  orbitEl("orbit-2d").setAttribute("aria-pressed", String(mode === "2d"));
  orbitEl("orbit-earth").hidden = mode !== "3d";
  orbitEl("orbit-flat").hidden = mode !== "2d";
  orbitEl("orbit-center").disabled = mode !== "3d";
  orbitEl("orbit-zoom-in").disabled = mode !== "3d";
  orbitEl("orbit-zoom-out").disabled = mode !== "3d";
  orbitRender(true);
}

function orbitControls() {
  orbitEl("orbit-3d").addEventListener("click", () => setOrbitView("3d"));
  orbitEl("orbit-2d").addEventListener("click", () => setOrbitView("2d"));
  orbitEl("orbit-center").addEventListener("click", () => {
    const sample = sampleAt(orbitState.time);
    if (sample) { orbitState.lon = sample.lon; orbitState.lat = sample.lat; orbitRender(true); }
  });
  orbitEl("orbit-zoom-in").addEventListener("click", () => { orbitState.zoom = clamp(orbitState.zoom * 1.2, .7, 1.8); orbitRender(); });
  orbitEl("orbit-zoom-out").addEventListener("click", () => { orbitState.zoom = clamp(orbitState.zoom / 1.2, .7, 1.8); orbitRender(); });
  orbitEl("orbit-now").addEventListener("click", () => {
    if (!orbitState.data || Date.now() < orbitState.data.start || Date.now() > orbitState.data.end ||
        Date.now() - orbitState.data.inputEpoch >= 72 * 3600000) {
      orbitEl("orbit-data-state").textContent = "현재 시각과 72시간 이내 입력을 포함하는 FDS 결과가 없습니다.";
      return;
    }
    orbitState.live = true; orbitState.playing = false;
    orbitEl("orbit-play").textContent = "재생"; orbitRender(true);
  });
  orbitEl("orbit-play").addEventListener("click", () => {
    if (!orbitState.data) return;
    orbitState.live = false;
    if (orbitState.time >= orbitState.data.end) orbitState.time = orbitState.data.start;
    orbitState.playing = !orbitState.playing;
    orbitEl("orbit-play").textContent = orbitState.playing ? "일시정지" : "재생";
  });
  orbitEl("orbit-speed").addEventListener("change", (event) => { orbitState.speed = Number(event.target.value); });
  orbitEl("orbit-timeline").addEventListener("input", (event) => {
    if (!orbitState.data) return;
    orbitState.live = false; orbitState.playing = false;
    orbitEl("orbit-play").textContent = "재생";
    orbitState.time = orbitState.data.start + Number(event.target.value) * 1000;
    orbitRender(true);
  });
  orbitEl("orbit-reload").addEventListener("click", readOrbitServer);
  orbitEl("orbit-file").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    try {
      if (file.size > 4 * 1024 * 1024) throw new Error("4 MB 이하의 FDS 결과만 읽을 수 있습니다.");
      setOrbitData(JSON.parse(await file.text()), "로컬 파일");
    } catch (error) { orbitEl("orbit-data-state").textContent = error.message; }
    event.target.value = "";
  });
  const wrap = orbitEl("orbit-canvas-wrap");
  let dragging = null;
  wrap.addEventListener("pointerdown", (event) => {
    if (orbitState.view !== "3d" || event.target.tagName !== "CANVAS") return;
    dragging = { x: event.clientX, y: event.clientY };
    wrap.setPointerCapture(event.pointerId);
  });
  wrap.addEventListener("pointermove", (event) => {
    if (!dragging || orbitState.view !== "3d") return;
    orbitState.lon -= (event.clientX - dragging.x) * .27;
    orbitState.lat = clamp(orbitState.lat + (event.clientY - dragging.y) * .27, -85, 85);
    dragging = { x: event.clientX, y: event.clientY };
    orbitRender();
  });
  wrap.addEventListener("pointerup", () => { dragging = null; });
  wrap.addEventListener("pointercancel", () => { dragging = null; });
  wrap.addEventListener("wheel", (event) => {
    if (orbitState.view !== "3d") return;
    event.preventDefault(); orbitState.zoom = clamp(orbitState.zoom * (event.deltaY < 0 ? 1.1 : .9), .7, 1.8);
    orbitRender();
  }, { passive: false });
  new ResizeObserver(() => orbitRender()).observe(wrap);
}

async function initOrbitViewer() {
  orbitControls();
  try {
    if (!setupGlobe()) {
      orbitEl("orbit-data-state").textContent = "이 브라우저에서는 3D 그래픽을 사용할 수 없어 2D 지도로 표시합니다.";
      setOrbitView("2d"); orbitEl("orbit-3d").disabled = true;
    }
  } catch (_) {
    setOrbitView("2d"); orbitEl("orbit-3d").disabled = true;
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
}
