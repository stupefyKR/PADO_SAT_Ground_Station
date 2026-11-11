"use strict";

// Display the profile produced by FDS. No orbit or Doppler propagation runs here.
let mpsDopplerRevision = 0;
let mpsDopplerSelection = null;
let mpsDopplerProfile = null;
let mpsDopplerPending = false;
let mpsDopplerFetchedAt = 0;

function mpsDopplerNodes() {
  const nodes = Object.fromEntries(["state", "chart", "download", "meta", "note", "hover", "retry"]
    .map(name => [name, document.getElementById(`mps-doppler-${name}`)]));
  return Object.values(nodes).every(Boolean) ? nodes : null;
}

function mpsDopplerUtc(value) {
  return new Date(value).toISOString().replace("T", " ").replace("Z", " UTC");
}

function mpsDopplerError(error) {
  if (error?.message === "profile_changed")
    return "FDS 도플러 프로파일이 변경됐습니다. 다시 불러온 뒤 다운로드하세요.";
  if (error?.message === "schedule_changed")
    return "FDS 일정이 갱신됐습니다. 새 PASS 일정을 불러온 뒤 다시 선택하세요.";
  if (error?.message === "pass_not_found")
    return "선택한 PASS의 도플러 프로파일을 찾을 수 없습니다.";
  return "FDS 도플러 프로파일을 읽을 수 없습니다. 계산·게시가 끝난 뒤 다시 불러오세요.";
}

function mpsDopplerEmpty(nodes, message) {
  const context = nodes.chart.getContext("2d");
  context.clearRect(0, 0, nodes.chart.width, nodes.chart.height);
  context.fillStyle = "#091b2a";
  context.fillRect(0, 0, nodes.chart.width, nodes.chart.height);
  context.fillStyle = "#a8c5ce"; context.font = "14px system-ui";
  context.fillText(message, 28, nodes.chart.height / 2);
}

function mpsClearDoppler(message = "PASS를 선택하세요") {
  mpsDopplerRevision += 1;
  mpsDopplerSelection = null; mpsDopplerProfile = null; mpsDopplerPending = false;
  mpsDopplerFetchedAt = 0;
  const nodes = mpsDopplerNodes();
  if (!nodes) return;
  nodes.state.textContent = message; nodes.download.disabled = true;
  nodes.download.onclick = null; nodes.retry.hidden = true; nodes.retry.onclick = null;
  nodes.meta.textContent = "FDS가 계산한 PASS별 도플러 프로파일을 표시합니다.";
  nodes.note.textContent = ""; nodes.hover.textContent = "";
  nodes.chart.onpointermove = null; nodes.chart.onpointerleave = null;
  mpsDopplerEmpty(nodes, message);
}

function mpsValidateDoppler(profile, pass, digest) {
  const aos = Date.parse(profile?.aos_utc), los = Date.parse(profile?.los_utc);
  if (!profile || profile.schema !== "fds.doppler-profile.v1" ||
      profile.schedule_sha256 !== digest || profile.pass_id !== pass.pass_id ||
      profile.station_id !== pass.station_id || profile.band !== pass.band ||
      !["TLE", "GNSS"].includes(profile.source) ||
      !Number.isSafeInteger(profile.norad_id) || profile.norad_id < 1 ||
      !Number.isFinite(Date.parse(profile.input_epoch_utc)) ||
      !/^[0-9a-f]{64}$/.test(profile.profile_sha256) ||
      !Number.isFinite(aos) || !Number.isFinite(los) || aos >= los ||
      Math.abs(aos - pass.aos_utc_s * 1000) > 1500 ||
      Math.abs(los - pass.los_utc_s * 1000) > 1500 ||
      !Number.isFinite(profile.carrier_hz) || profile.carrier_hz <= 0 || profile.carrier_hz > 1e13 ||
      typeof profile.frequency_example !== "boolean" ||
      !Number.isFinite(profile.sample_step_s) || profile.sample_step_s <= 0 ||
      !Array.isArray(profile.samples) || profile.samples.length < 2 || profile.samples.length > 30000)
    throw new Error("invalid_doppler_profile");
  let previous = -Infinity;
  for (const row of profile.samples) {
    if (!Array.isArray(row) || row.length !== 4 || typeof row[0] !== "string" ||
        !row[0].endsWith("Z") || !Number.isFinite(Date.parse(row[0])) ||
        Date.parse(row[0]) <= previous || Date.parse(row[0]) < aos || Date.parse(row[0]) > los ||
        !row.slice(1).every(value => typeof value === "number" && Number.isFinite(value)) ||
        Math.abs(row[1]) > 1e8 || Math.abs(row[2]) > 1e12 || row[3] <= 0)
      throw new Error("invalid_doppler_profile");
    previous = Date.parse(row[0]);
  }
  if (Date.parse(profile.samples[0][0]) !== aos || Date.parse(profile.samples.at(-1)[0]) !== los)
    throw new Error("invalid_doppler_profile");
  return profile;
}

function mpsDrawDoppler(profile, hoverIndex = null) {
  const nodes = mpsDopplerNodes();
  if (!nodes) return;
  const canvas = nodes.chart, context = canvas.getContext("2d");
  const width = canvas.width, height = canvas.height;
  const left = 84, right = width - 28, top = 32, bottom = height - 66;
  const start = Date.parse(profile.aos_utc), end = Date.parse(profile.los_utc);
  const maximum = profile.samples.reduce((value, row) => Math.max(value, Math.abs(row[2])), 1);
  const magnitude = 10 ** Math.floor(Math.log10(maximum));
  const multiple = [1, 2, 5, 10].find(value => value * magnitude >= maximum * 1.08) ?? 20;
  const limit = multiple * magnitude;
  const divisor = limit >= 1000 ? 1000 : 1;
  const unit = divisor === 1000 ? "kHz" : "Hz";
  const x = value => left + (Date.parse(value) - start) / (end - start) * (right - left);
  const y = value => bottom - (value + limit) / (2 * limit) * (bottom - top);
  context.clearRect(0, 0, width, height);
  context.fillStyle = "#091b2a"; context.fillRect(0, 0, width, height);
  context.font = "12px Consolas,monospace";
  for (let index = 0; index <= 4; index++) {
    const value = limit - index * limit / 2, position = y(value);
    context.strokeStyle = value === 0 ? "#91b8c7" : "#294759";
    context.lineWidth = value === 0 ? 1.5 : 1;
    context.setLineDash(value === 0 ? [5, 4] : []);
    context.beginPath(); context.moveTo(left, position); context.lineTo(right, position); context.stroke();
    context.setLineDash([]); context.fillStyle = "#b5d3dc";
    const label = (value / divisor).toFixed(divisor === 1000 ? 2 : 1);
    context.fillText(label, Math.max(4, left - label.length * 7.3 - 12), position + 4);
  }
  context.fillStyle = "#b5d3dc"; context.fillText(unit, 20, 20);
  for (let index = 0; index <= 4; index++) {
    const time = start + (end - start) * index / 4;
    const position = left + (right - left) * index / 4;
    context.strokeStyle = "#294759"; context.lineWidth = 1;
    context.beginPath(); context.moveTo(position, top); context.lineTo(position, bottom); context.stroke();
    const stamp = new Date(time).toISOString();
    const labelX = Math.max(4, Math.min(width - 82, position - 37));
    context.fillStyle = "#b5d3dc"; context.fillText(stamp.slice(11, 19), labelX, bottom + 22);
    context.fillStyle = "#809fac"; context.fillText(stamp.slice(0, 10), labelX - 6, bottom + 39);
  }
  context.fillStyle = "#a8c5ce"; context.fillText("UTC", right - 24, height - 9);
  context.strokeStyle = "#7bd6e1"; context.lineWidth = 2.5; context.beginPath();
  profile.samples.forEach((row, index) => {
    if (index === 0) context.moveTo(x(row[0]), y(row[2]));
    else context.lineTo(x(row[0]), y(row[2]));
  });
  context.stroke();
  for (const [label, row, offset] of [["AOS", profile.samples[0], 0], ["LOS", profile.samples.at(-1), -24]]) {
    context.fillStyle = "#ffde82"; context.beginPath(); context.arc(x(row[0]), y(row[2]), 4, 0, Math.PI * 2); context.fill();
    context.fillText(label, x(row[0]) + offset, top - 12);
  }
  if (hoverIndex !== null) {
    const row = profile.samples[hoverIndex];
    context.strokeStyle = "#ffde82"; context.lineWidth = 1; context.setLineDash([3, 3]);
    context.beginPath(); context.moveTo(x(row[0]), top); context.lineTo(x(row[0]), bottom); context.stroke();
    context.setLineDash([]); context.fillStyle = "#ffde82";
    context.beginPath(); context.arc(x(row[0]), y(row[2]), 4, 0, Math.PI * 2); context.fill();
  }
}

async function mpsDownloadDoppler(endpoint, revision, profile) {
  const nodes = mpsDopplerNodes();
  if (!nodes || revision !== mpsDopplerRevision) return;
  nodes.download.disabled = true;
  try {
    const response = await fetch(endpoint + "&format=csv", {credentials: "same-origin", cache: "no-store"});
    if (response.status === 401) {
      window.location.assign("/login");
      throw new Error("session_expired");
    }
    if (!response.ok) {
      const result = await response.json().catch(() => ({}));
      throw new Error(result.error || "doppler_unavailable");
    }
    if (!response.headers.get("Content-Type")?.startsWith("text/csv"))
      throw new Error("invalid_doppler_profile");
    if (response.headers.get("X-Pado-Profile-SHA256") !== profile.profile_sha256)
      throw new Error("profile_changed");
    const blob = await response.blob();
    if (revision !== mpsDopplerRevision) return;
    const url = URL.createObjectURL(blob), link = document.createElement("a");
    link.href = url; link.download = `doppler-${profile.pass_id}-${profile.schedule_sha256.slice(0, 12)}.csv`;
    try { link.click(); }
    finally { setTimeout(() => URL.revokeObjectURL(url), 0); }
  } catch (error) {
    if (revision !== mpsDopplerRevision) return;
    mpsDopplerProfile = null;
    nodes.state.textContent = "다운로드 확인 불가";
    nodes.note.textContent = mpsDopplerError(error); nodes.retry.hidden = false;
    nodes.chart.onpointermove = null; nodes.chart.onpointerleave = null;
    nodes.hover.textContent = ""; mpsDopplerEmpty(nodes, "FDS 프로파일 갱신 필요");
  } finally {
    if (revision === mpsDopplerRevision) nodes.download.disabled = mpsDopplerProfile === null;
  }
}

async function mpsLoadDoppler(pass, schedule) {
  const nodes = mpsDopplerNodes();
  if (!nodes) return;
  const digest = schedule?.schedule_sha256;
  if (!pass || schedule?.schedule_state !== "validated" || !/^[0-9a-f]{64}$/.test(digest) ||
      !["yonsei", "kari"].includes(pass.station_id) || !/^(yonsei|kari)-[0-9]+$/.test(pass.pass_id) ||
      !Number.isFinite(pass.aos_utc_s) || !Number.isFinite(pass.los_utc_s) || pass.aos_utc_s >= pass.los_utc_s) {
    mpsClearDoppler(); return;
  }
  const key = [digest, pass.station_id, pass.pass_id, pass.aos_utc_s, pass.los_utc_s].join(":");
  if (mpsDopplerSelection?.key === key && (mpsDopplerPending ||
      (mpsDopplerProfile && Date.now() - mpsDopplerFetchedAt < 60000))) return;
  mpsClearDoppler("FDS 프로파일 확인 중");
  const revision = mpsDopplerRevision;
  const requestedAt = Date.now();
  mpsDopplerSelection = {key, pass: {...pass}, schedule: {
    schedule_state: schedule.schedule_state, schedule_sha256: digest}};
  mpsDopplerPending = true;
  const endpoint = `/api/fds/doppler?pass_id=${encodeURIComponent(pass.pass_id)}&schedule_sha256=${digest}`;
  nodes.retry.onclick = () => mpsLoadDoppler(pass, schedule);
  try {
    const result = await api(endpoint);
    if (revision !== mpsDopplerRevision) return;
    const profile = mpsValidateDoppler(result, pass, digest);
    mpsDopplerProfile = profile;
    mpsDopplerFetchedAt = requestedAt;
    nodes.state.textContent = `${pass.station_id === "yonsei" ? "연세대" : "항우연"} · ${pass.band} · FDS 확인`;
    nodes.meta.textContent = `NORAD ${profile.norad_id} · ${profile.source} · 기준 ${(profile.carrier_hz / 1e6).toFixed(6)} MHz · ${profile.frequency_example ? "예시 주파수" : "설정 주파수"} · ${profile.sample_step_s}초 간격 · ${profile.samples.length}개 표본`;
    const shifts = profile.samples.map(row => row[2]);
    nodes.note.textContent = `AOS ${mpsDopplerUtc(profile.aos_utc)} → LOS ${mpsDopplerUtc(profile.los_utc)} · 접근 시 양수, 멀어질 때 음수 · 범위 ${Math.min(...shifts).toFixed(1)} ~ ${Math.max(...shifts).toFixed(1)} Hz`;
    nodes.hover.textContent = "그래프 위에 마우스를 올리면 표본 시각과 정확한 값을 확인할 수 있습니다.";
    nodes.download.disabled = false;
    nodes.download.onclick = () => mpsDownloadDoppler(endpoint, revision, profile);
    nodes.chart.onpointermove = event => {
      if (revision !== mpsDopplerRevision) return;
      const bounds = nodes.chart.getBoundingClientRect();
      if (!bounds.width) return;
      const pixel = (event.clientX - bounds.left) * nodes.chart.width / bounds.width;
      const fraction = Math.max(0, Math.min(1, (pixel - 84) / (nodes.chart.width - 112)));
      const time = Date.parse(profile.aos_utc) + fraction * (Date.parse(profile.los_utc) - Date.parse(profile.aos_utc));
      let nearest = 0;
      profile.samples.forEach((row, index) => {
        if (Math.abs(Date.parse(row[0]) - time) < Math.abs(Date.parse(profile.samples[nearest][0]) - time)) nearest = index;
      });
      const row = profile.samples[nearest];
      nodes.hover.textContent = `${mpsDopplerUtc(row[0])} · 도플러 ${row[2].toFixed(3)} Hz · 수신 ${row[3].toFixed(3)} Hz · 거리 변화율 ${row[1].toFixed(3)} m/s`;
      mpsDrawDoppler(profile, nearest);
    };
    nodes.chart.onpointerleave = () => {
      if (revision !== mpsDopplerRevision) return;
      nodes.hover.textContent = "그래프 위에 마우스를 올리면 표본 시각과 정확한 값을 확인할 수 있습니다.";
      mpsDrawDoppler(profile);
    };
    mpsDrawDoppler(profile);
  } catch (error) {
    if (revision !== mpsDopplerRevision) return;
    mpsDopplerProfile = null;
    nodes.state.textContent = "프로파일 확인 불가";
    nodes.note.textContent = mpsDopplerError(error); nodes.retry.hidden = false;
    mpsDopplerEmpty(nodes, "FDS 프로파일 대기");
  } finally {
    if (revision === mpsDopplerRevision) mpsDopplerPending = false;
  }
}

function mpsRefreshDoppler() {
  if (mpsDopplerSelection)
    return mpsLoadDoppler(mpsDopplerSelection.pass, mpsDopplerSelection.schedule);
}
