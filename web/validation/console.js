"use strict";
// Shared ROS/MPS interface. The server, not this visibility guard, authorizes
// every validation API. Operations never mounts the simulator/control store.
(async function () {
  const request = async (path, options = {}) => {
    const response = await fetch(path, {credentials: "same-origin", cache: "no-store", ...options});
    const result = await response.json();
    if (!response.ok) throw Error(result.error || `HTTP ${response.status}`);
    return result;
  };
  const utc = ms => ms ? new Date(ms).toISOString().replace(".000Z", "Z") : "—";
  const node = (tag, text) => { const n = document.createElement(tag); n.textContent = text; return n; };
  const env = await request("/api/environment").catch(() => null);
  if (!env) return;
  const badge = node("span", env.label); badge.className = "tag neutral";
  (document.querySelector(".top-right") || document.querySelector("h1"))?.append(badge);
  if (document.body.dataset.page === "orbit") {
    const select = document.querySelector("#orbit-target");
    if (select && env.environment !== "legacy") {
      for (const option of [...select.options])
        if (option.value !== (env.environment === "validation" ? "synthetic" : "operational")) option.remove();
      select.dispatchEvent(new Event("change"));
    }
  }
  if (env.environment !== "validation" || !["ros", "mps", "ops"].includes(document.body.dataset.page)) return;
  const session = await request("/api/session").catch(() => null);
  if (!session) return;
  const panel = document.createElement("section"); panel.className = "panel validation-panel";
  panel.innerHTML = `<div class="panel-head"><div><span class="eyebrow">GROUND STATION INTEGRATION VALIDATION</span><h2>TC · TM 통합 검증</h2></div><button type="button" id="validation-open">Validation Packets</button></div>
    <div class="validation-content"><p>검증용 가상 위성과 연속 연결합니다. 정상 TM 7,000개 · 손상 2,500개 · 센서 급변 500개를 섞어 전송합니다. 실제 FSW의 장치 동작은 모사하지 않습니다.</p>
    <form id="validation-form" hidden><div class="validation-form-grid"><label>시퀀스 시작 UTC<input id="validation-time" type="datetime-local" step="1" required></label><label>AOS 몇 초 후<input id="validation-offset" type="number" min="0" value="10" step="1"></label><button type="button" id="validation-pass">선택한 PASS 기준 적용</button><label>데이터 seed<input id="validation-seed" type="number" min="0" max="4294967295" step="1" value="724" required></label><button type="submit">검증 시퀀스 예약</button></div><p id="validation-when"></p><p id="validation-coverage"></p></form>
    <div class="validation-actions"><label>검증 실행<select id="validation-run"><option value="">실행 없음</option></select></label><button type="button" id="validation-cancel" disabled>남은 검증 중단</button><button type="button" id="validation-export" disabled>결과 JSON 저장</button></div>
    <p id="validation-message" role="status"></p><div id="validation-summary" class="validation-summary"></div>
    <details><summary>TC 송신 원문 · 응답</summary><div class="table-scroll"><table><thead><tr><th>순서 / 명령</th><th>시험 값</th><th>결과</th><th>실제 송신 UTC / RAW</th></tr></thead><tbody id="validation-tc"></tbody></table></div></details>
    <details><summary>TM 수신 원문 · 파싱 결과</summary><div id="validation-packets"></div><button type="button" id="validation-more">이전 패킷 50개</button></details>
    <details open><summary>센서 이벤트 · 측정 UTC</summary><div class="table-scroll"><table><thead><tr><th>측정 UTC</th><th>센서</th><th>이벤트</th><th>측정값 / 허용 범위</th></tr></thead><tbody id="validation-events"></tbody></table></div></details></div>`;
  const host = document.getElementById("ros-view-tc") || document.querySelector(".content");
  host?.prepend(panel);
  let overviewEventRows = null;
  const overview = document.getElementById('ros-view-status');
  if (overview) {
    const events = document.createElement('section'); events.className = 'panel validation-panel';
    events.innerHTML = '<div class="panel-head"><h2>통합 검증 센서 이벤트</h2><span class="tag neutral">최근 실행 · 측정 시각 기준</span></div><div class="table-scroll"><table><thead><tr><th>측정 UTC</th><th>센서</th><th>이벤트</th><th>측정값 / 허용 범위</th></tr></thead><tbody></tbody></table></div>';
    overviewEventRows = events.querySelector('tbody'); overview.append(events);
  }
  const $ = id => panel.querySelector(`#validation-${id}`);
  let current = null, selected = "", before = 2147483647, busy = false, packetSignature = "", tcSignature = "";
  const message = text => { $("message").textContent = text; };
  const post = (path, data) => request(`/api/ros/validation/${path}`, {method: "POST",
    headers: {"Content-Type": "application/json", "X-CSRF-Token": session.csrf}, body: JSON.stringify(data)});
  const preview = () => { $("when").textContent = `시퀀스 전체 시작: ${$("time").value || "—"} UTC`; };
  $("open").disabled = !["planner", "operator", "lead"].includes(session.role);
  $("open").onclick = async () => {
    $("form").hidden = !$("form").hidden;
    if (!$("time").value) $("time").value = new Date(Date.now() + 15000).toISOString().slice(0, 19);
    preview();
    try { const c = await request("/api/ros/validation/coverage");
      $("coverage").textContent = `정의된 TC ${c.defined_command_count}종 / ${c.cases.length}개 시험 값 + TM 요청 1개. 인자 ICD 미정 ${c.undefined_commands.length}종: ${c.undefined_commands.map(x => x.name).join(", ")}`;
    } catch (e) { message(e.message); }
  };
  $("time").oninput = preview;
  $("pass").onclick = () => {
    const pass = typeof mpsSelectedPass !== "undefined" ? mpsSelectedPass : null;
    if (!pass) { message("MPS에서 PASS를 먼저 선택하세요. 직접 UTC를 지정할 수도 있습니다."); return; }
    $("time").value = new Date((pass.aos_utc_s + Number($("offset").value)) * 1000).toISOString().slice(0, 19); preview();
  };
  $("form").onsubmit = async event => {
    event.preventDefault();
    try {
      const beacon = await request('/api/ros/test-beacon').catch(() => null);
      if (!beacon || Date.now() - Date.parse(beacon.received_utc) > 30000)
        throw Error('가상 위성의 정상 HK 비콘 수신을 기다린 뒤 예약하세요. 초기 FDS 계산에는 시간이 걸립니다.');
      const run = await post("runs", {target_utc_s: Date.parse($("time").value + "Z") / 1000, seed: Number($("seed").value)});
      selected = String(run.id); $("form").hidden = true; message("검증 시퀀스를 예약했습니다."); await refresh();
    } catch (e) { message(e.message); }
  };
  $("run").onchange = () => { selected = $("run").value; packetSignature = ""; refresh(); };
  $("cancel").onclick = async () => { try { await post("cancel", {id: Number(selected)}); await refresh(); } catch (e) { message(e.message); } };
  $("export").onclick = () => {
    const link = document.createElement("a"), url = URL.createObjectURL(new Blob([JSON.stringify(current, null, 2)], {type: "application/json"}));
    link.href = url; link.download = `pado-validation-${current.id}.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  async function packets(reset = false) {
    if (!selected) return;
    if (reset) { before = 2147483647; $("packets").replaceChildren(); }
    const data = await request(`/api/ros/validation/packets/${selected}/${before}`);
    for (const p of data.items) {
      const detail = node("details", ""), summary = node("summary", `#${p.case_id} ${p.name} · ${p.category}${p.mutation ? " / " + p.mutation : ""}`);
      detail.append(summary, node("pre", `RAW\n${p.raw_hex}\n\n${p.error ? "거부: " + p.error : JSON.stringify(p.parsed, null, 2)}`));
      $("packets").append(detail); before = p.case_id;
    }
    $("more").disabled = data.items.length < 50;
  }
  $("more").onclick = () => packets().catch(e => message(e.message));
  async function refresh() {
    if (busy || document.hidden) return; busy = true;
    try {
      const list = await request("/api/ros/validation/runs");
      if (!selected && list.items.length) selected = String(list.items[0].id);
      $("run").replaceChildren(...list.items.map(r => { const o = node("option", `#${r.id} · ${utc(r.target * 1000)} · ${r.status}`); o.value = r.id; return o; }));
      $("run").value = selected;
      if (!selected) return;
      current = await request(`/api/ros/validation/runs/${selected}`);
      const tm = current.tm, tc = current.tc_summary;
      $("summary").replaceChildren(...[
        `TC ${tc.state} · 송신 ${tc.sent}/${tc.expected} · ACK ${tc.acked}`,
        `TM ${tm.state} · 수신 ${tm.received}/${tm.expected || 10000} · 누락 ${tm.missing ?? 10000}`,
        `정상 파싱 ${tm.valid_parsed || 0} · 손상 거부 ${tm.malformed_rejected || 0} · 센서 이벤트 ${tm.sensor_events || 0}`,
        `오수락 ${tm.error_counts?.false_accept || 0} · 오거부 ${tm.error_counts?.false_reject || 0} · 값 불일치 ${tm.error_counts?.wrong_fields || 0} · 이벤트 누락 ${tm.error_counts?.missing_spike_events || 0} · 예상 외 패킷 ${tm.unexpected_packets || 0}`
      ].map(text => node("strong", text)));
      $("cancel").disabled = current.status === "CANCELLED" || tm.state === "PASS" || !["planner", "operator", "lead"].includes(session.role);
      $("export").disabled = false;
      const nextTcSignature = JSON.stringify(current.tc);
      if (nextTcSignature !== tcSignature) { tcSignature = nextTcSignature; $("tc").replaceChildren(...current.tc.map(t => {
        const row = document.createElement("tr");
        row.append(node("td", `${t.step} · ${t.name}`), node("td", `${t.variant}\n${JSON.stringify(t.parameters)}`), node("td", `${t.status}${t.error ? " / " + t.error : ""}`));
        const raw = node("td", utc(t.wire_sent_ms)); if (t.raw_hex) { const d = node("details", ""); d.append(node("summary", t.wire_sent_ms ? "DGS 송신 RAW" : `구성 RAW · ${t.wire_status}`), node("pre", t.raw_hex)); raw.append(d); } row.append(raw); return row;
      })); }
      const ev = await request(`/api/ros/validation/events/${selected}`);
      const eventRows = () => ev.items.map(e => { const r = document.createElement("tr");
        r.append(...[utc(e.sampled_ms), e.sensor, e.kind, `${e.value} / ${e.lower_value} … ${e.upper_value}`].map(s => node("td", s))); return r; });
      $("events").replaceChildren(...eventRows()); overviewEventRows?.replaceChildren(...eventRows());
      const signature = `${selected}:${tm.received}`;
      if (signature !== packetSignature) { packetSignature = signature; await packets(true); }
    } catch (e) { message(`검증 상태: ${e.message}`); } finally { busy = false; }
  }
  await refresh(); setInterval(refresh, 4000);
})();
