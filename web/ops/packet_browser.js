"use strict";

// Exact received application PDUs are read from the ground receiver's archive.
(() => {
  const text = (value) => value === null || value === undefined ? "—" :
    typeof value === "object" ? JSON.stringify(value) : String(value);
  const validId = (value) => Number.isSafeInteger(value) && value > 0;
  const label = (item) => item.label || `TM${String(item.id).padStart(4, "0")}`;

  function hexDump(raw) {
    if (typeof raw !== "string" || raw.length > 400 || raw.length % 2 ||
        !/^[0-9a-f]*$/i.test(raw)) throw Error("원문 HEX 형식이 올바르지 않습니다.");
    const bytes = raw.match(/../g) || [];
    const rows = [];
    for (let offset = 0; offset < bytes.length; offset += 16) {
      const part = bytes.slice(offset, offset + 16);
      const ascii = part.map((byte) => {
        const number = parseInt(byte, 16);
        return number >= 32 && number <= 126 ? String.fromCharCode(number) : ".";
      }).join("");
      rows.push(`${offset.toString(16).padStart(4, "0")}  ${part.join(" ").padEnd(47)}  ${ascii}`);
    }
    return rows.join("\n");
  }

  function create({api}) {
    const $ = (id) => document.getElementById(id);
    const pageSize = 50;
    let beforeId = null;
    let nextBeforeId = null;
    let selectedId = null;
    let selectedHkIncomplete = false;
    let items = [];
    let pending = false;
    let refreshAgain = false;
    let detailGeneration = 0;
    let appliedName = "";

    function state(value, error = false) {
      $("packet-browser-state").textContent = value;
      $("packet-browser-state").classList.toggle("error", error);
    }
    function cell(row, value) {
      const element = document.createElement("td");
      element.textContent = text(value);
      row.append(element);
      return element;
    }
    function empty(target, columns, value) {
      const row = document.createElement("tr");
      const element = cell(row, value);
      element.colSpan = columns;
      element.className = "table-empty";
      target.replaceChildren(row);
    }
    function keyValues(target, values) {
      target.replaceChildren();
      for (const [key, value] of Object.entries(values || {})) {
        const row = document.createElement("tr");
        cell(row, key); cell(row, value); target.append(row);
      }
      if (!target.children.length) empty(target, 2, "필드가 없습니다.");
    }
    function detailTab(name) {
      for (const item of ["parsed", "raw"]) {
        const active = name === item;
        $("packet-tab-" + item).setAttribute("aria-selected", String(active));
        $("packet-detail-" + item).hidden = !active;
      }
    }
    function drawList() {
      const rows = $("packet-record-rows");
      rows.replaceChildren();
      if (!items.length) empty(rows, 5, "조건에 맞는 수신 패킷이 없습니다.");
      for (const item of items) {
        const row = document.createElement("tr");
        row.className = item.id === selectedId ? "packet-selected" : "";
        const button = document.createElement("button");
        button.type = "button";
        button.className = "packet-select";
        button.textContent = label(item);
        button.setAttribute("aria-pressed", String(item.id === selectedId));
        button.addEventListener("click", () => select(item.id));
        const control = document.createElement("td"); control.append(button); row.append(control);
        cell(row, item.name); cell(row, item.sampled_utc);
        cell(row, item.sequence); cell(row, item.size_bytes);
        rows.append(row);
      }
      $("packet-older").disabled = !validId(nextBeforeId) || pending;
    }
    async function select(id) {
      if (!validId(id)) { state("TM 번호를 확인하세요. 예: TM0010", true); return; }
      selectedId = id;
      selectedHkIncomplete = false;
      const generation = ++detailGeneration;
      drawList();
      $("packet-detail-title").textContent = `${label({id})} · 조회 중`;
      $("packet-detail-kind").textContent = "";
      $("packet-detail-meta").replaceChildren();
      empty($("packet-header-rows"), 2, "조회 중");
      empty($("packet-field-rows"), 2, "조회 중");
      $("packet-raw-hex").textContent = "조회 중";
      $("packet-full-hk-detail").hidden = true;
      state(`${label({id})} 원문·파싱 값 조회 중`);
      try {
        const detail = await api(`/api/ros/received-tm/${id}`);
        if (generation !== detailGeneration) return;
        if (detail.id !== id || !detail.fields || typeof detail.fields !== "object")
          throw Error("패킷 상세 응답 형식이 올바르지 않습니다.");
        $("packet-detail-title").textContent = `${label(detail)} · ${detail.name}`;
        $("packet-detail-kind").textContent = `${detail.size_bytes} bytes · 순번 ${detail.sequence}`;
        const meta = $("packet-detail-meta"); meta.replaceChildren();
        for (const [key, value] of [["측정 UTC", detail.sampled_utc],
          ["수신 UTC", detail.received_utc], ["패킷 ID", detail.packet_id]]) {
          const term = document.createElement("dt"); term.textContent = key;
          const description = document.createElement("dd"); description.textContent = text(value);
          meta.append(term, description);
        }
        keyValues($("packet-header-rows"), detail.header);
        keyValues($("packet-field-rows"), detail.fields);
        $("packet-raw-hex").textContent = hexDump(detail.raw_hex);
        $("packet-full-hk-detail").hidden = detail.full_hk === undefined || detail.full_hk === null;
        $("packet-full-hk-json").textContent = detail.full_hk ?
          JSON.stringify(detail.full_hk, null, 2) : "";
        selectedHkIncomplete = Boolean(detail.full_hk &&
          String(detail.full_hk.state).toLowerCase() !== "complete");
        state(`${label(detail)} · 저장된 수신 원문과 파싱 값을 표시합니다.`);
      } catch (error) {
        if (generation === detailGeneration) state(`패킷 조회 실패: ${error.message}`, true);
      }
    }
    async function refresh({latest = false} = {}) {
      if (latest) beforeId = null;
      if (pending) { refreshAgain = refreshAgain || latest; return; }
      pending = true;
      $("packet-older").disabled = true;
      try {
        const query = new URLSearchParams({limit: String(pageSize)});
        if (validId(beforeId)) query.set("before_id", String(beforeId));
        if (appliedName) query.set("name", appliedName);
        const result = await api("/api/ros/received-tm?" + query.toString());
        if (!Array.isArray(result.items) || result.items.length > pageSize ||
            !result.items.every((item) => validId(item.id))) throw Error("목록 응답 형식이 올바르지 않습니다.");
        items = result.items;
        nextBeforeId = result.next_before_id;
        $("packet-total").textContent = `보관 ${result.total ?? "—"}개 · 표시 ${items.length}개`;
        drawList();
        if (selectedId === null && items.length) await select(items[0].id);
        else if (selectedId !== null && selectedHkIncomplete) await select(selectedId);
        else if (!items.length) state("수신한 패킷이 없거나 검색 조건에 맞는 항목이 없습니다.");
        else if (latest) state("최신 수신 목록으로 갱신했습니다. 패킷을 선택하세요.");
      } catch (error) {
        state(`수신 목록 조회 실패: ${error.message}`, true);
      } finally {
        pending = false;
        $("packet-older").disabled = !validId(nextBeforeId);
        if (refreshAgain) { refreshAgain = false; refresh({latest: true}); }
      }
    }
    $("packet-filter-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const value = $("packet-name-filter").value.trim();
      if (value && !/^[A-Z][A-Z0-9_]{0,47}$/.test(value)) {
        state("종류는 GNSS_PVT처럼 정확한 패킷 이름으로 입력하세요.", true); return;
      }
      appliedName = value; selectedId = null; ++detailGeneration; refresh({latest: true});
    });
    $("packet-open-id").addEventListener("click", () => {
      const value = $("packet-id-filter").value.trim();
      if (!/^(?:TM)?\d{1,15}$/i.test(value)) { state("수신 번호를 확인하세요. 예: TM0010", true); return; }
      select(Number(value.replace(/^TM/i, "")));
    });
    $("packet-refresh").addEventListener("click", () => refresh({latest: true}));
    $("packet-older").addEventListener("click", () => {
      if (validId(nextBeforeId)) { beforeId = nextBeforeId; refresh(); }
    });
    $("packet-tab-parsed").addEventListener("click", () => detailTab("parsed"));
    $("packet-tab-raw").addEventListener("click", () => detailTab("raw"));
    return {refresh, select};
  }

  window.rosPacketBrowser = {create, hexDump};
})();
