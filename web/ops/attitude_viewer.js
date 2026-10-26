"use strict";

// A body-centred, interactive 3D view of the bounded CAD triangle mesh.
// Orbit-camera movement changes only the viewpoint; TM orientation is expressed
// by the Earth/Sun direction vectors supplied in the spacecraft body frame.
window.rosAttitudeViewer = (() => {
  const MAX_BYTES = 4 * 1024 * 1024;
  const MAX_VERTICES = 40000;
  const MAX_TRIANGLES = 25000;
  const MAX_MATERIALS = 64;
  const DEFAULT_YAW = Math.PI / 4;
  const DEFAULT_PITCH = 0.62;
  const AXES = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const cross = (a, b) => [a[1] * b[2] - a[2] * b[1],
    a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  const length = (v) => Math.hypot(v[0], v[1], v[2]);
  const unit = (v) => { const n = length(v); return n > 0 ? v.map((x) => x / n) : null; };
  const clamp = (v, low, high) => Math.max(low, Math.min(high, v));

  function meshFromJson(raw) {
    if (!raw || raw.schema !== "pado.cad-mesh.v1" ||
        (raw.vertices_frame !== undefined && raw.vertices_frame !== "BODY") ||
        !Array.isArray(raw.vertices) || !raw.vertices.length ||
        raw.vertices.length > MAX_VERTICES ||
        !Array.isArray(raw.triangles) || !raw.triangles.length ||
        raw.triangles.length > MAX_TRIANGLES ||
        !Array.isArray(raw.materials) || !raw.materials.length ||
        raw.materials.length > MAX_MATERIALS) throw Error("CAD 메시 형식 또는 크기가 유효하지 않습니다.");
    const finite = (x) => typeof x === "number" && Number.isFinite(x) && Math.abs(x) < 1e7;
    // The shipped asset has already converted STEP coordinates to BODY. The
    // cad_to_body matrix is provenance metadata and must not be applied again.
    const vertices = raw.vertices.map((point) => {
      if (!Array.isArray(point) || point.length !== 3 || !point.every(finite))
        throw Error("CAD 정점이 유효하지 않습니다.");
      return point;
    });
    const colors = raw.materials.map((material) => {
      if (!material || typeof material.color !== "string" ||
          !/^#[0-9a-fA-F]{6}$/.test(material.color)) throw Error("CAD 재질 색이 유효하지 않습니다.");
      return [1, 3, 5].map((offset) => parseInt(material.color.slice(offset, offset + 2), 16));
    });
    const triangles = raw.triangles.map((face) => {
      if (!Array.isArray(face) || face.length !== 4 ||
          !face.every((value) => Number.isInteger(value)) ||
          face.slice(0, 3).some((index) => index < 0 || index >= vertices.length) ||
          face[3] < 0 || face[3] >= colors.length)
        throw Error("CAD 면 인덱스가 유효하지 않습니다.");
      return face;
    });
    const low = [Infinity, Infinity, Infinity], high = [-Infinity, -Infinity, -Infinity];
    for (const point of vertices) for (let axis = 0; axis < 3; axis++) {
      low[axis] = Math.min(low[axis], point[axis]);
      high[axis] = Math.max(high[axis], point[axis]);
    }
    const span = Math.max(...high.map((value, axis) => value - low[axis]));
    if (!(span > 0)) throw Error("CAD 형상의 크기가 유효하지 않습니다.");
    const centre = high.map((value, axis) => (value + low[axis]) / 2);
    const scale = 2.5 / span;
    const adjusted = vertices.map((point) => point.map((value, axis) =>
      (value - centre[axis]) * scale));
    const normals = triangles.map(([a, b, c]) => unit(cross(
      adjusted[b].map((value, axis) => value - adjusted[a][axis]),
      adjusted[c].map((value, axis) => value - adjusted[a][axis]))));
    return {vertices: adjusted, triangles, colors, normals};
  }

  function cameraBasis(yaw, pitch) {
    // Body +X is visually up; at the initial isometric view +Y goes left and
    // +Z goes right, matching the user-supplied axis illustration.
    const eye = [Math.sin(pitch), Math.cos(pitch) * Math.cos(yaw),
      Math.cos(pitch) * Math.sin(yaw)];
    const right = unit(cross([1, 0, 0], eye));
    const up = cross(eye, right);
    return {eye, right, up};
  }

  function faceColor(rgb, shade) {
    const channel = (value) => Math.round(clamp(value * shade + 20, 24, 245));
    return `rgb(${channel(rgb[0])},${channel(rgb[1])},${channel(rgb[2])})`;
  }

  class Viewer {
    constructor(canvas, onStatus = () => {}) {
      this.canvas = canvas;
      this.ctx = canvas.getContext("2d", {alpha: false});
      this.onStatus = onStatus;
      this.mesh = null;
      this.directions = null;
      this.yaw = DEFAULT_YAW;
      this.pitch = DEFAULT_PITCH;
      this.zoom = 1;
      this.pending = false;
      this.pointer = null;
      this.installControls();
      this.draw();
    }

    async load(url) {
      this.onStatus("CAD 형상 불러오는 중");
      try {
        const response = await fetch(url, {credentials: "same-origin", cache: "no-store"});
        if (!response.ok) throw Error(`CAD 파일 응답 ${response.status}`);
        const declared = Number(response.headers.get("content-length") || 0);
        if (declared > MAX_BYTES) throw Error("CAD 파일이 허용 크기를 넘습니다.");
        const bytes = await response.arrayBuffer();
        if (bytes.byteLength < 32 || bytes.byteLength > MAX_BYTES)
          throw Error("CAD 파일 크기가 유효하지 않습니다.");
        this.mesh = meshFromJson(JSON.parse(new TextDecoder("utf-8", {fatal: true}).decode(bytes)));
        this.onStatus("PADO-SAT CAD · 축 정렬 추정");
      } catch (error) {
        this.mesh = null;
        this.onStatus(`CAD 형상 표시 불가 · ${error.message}`);
      }
      this.requestDraw();
      return !!this.mesh;
    }

    setDirections(directions) {
      const valid = (vector) => Array.isArray(vector) && vector.length === 3 &&
        vector.every((entry) => typeof entry === "number" && Number.isFinite(entry)) &&
        length(vector) > 1e-9;
      this.directions = directions && valid(directions.earthBody) &&
        valid(directions.sunBody) ? directions : null;
      this.requestDraw();
    }

    reset() {
      this.yaw = DEFAULT_YAW;
      this.pitch = DEFAULT_PITCH;
      this.zoom = 1;
      this.requestDraw();
    }

    installControls() {
      const canvas = this.canvas;
      canvas.addEventListener("pointerdown", (event) => {
        this.pointer = {id: event.pointerId, x: event.clientX, y: event.clientY};
        canvas.setPointerCapture(event.pointerId);
        canvas.focus();
      });
      canvas.addEventListener("pointermove", (event) => {
        if (!this.pointer || this.pointer.id !== event.pointerId) return;
        this.yaw += (event.clientX - this.pointer.x) * 0.008;
        this.pitch = clamp(this.pitch + (event.clientY - this.pointer.y) * 0.008, -1.3, 1.3);
        this.pointer.x = event.clientX;
        this.pointer.y = event.clientY;
        this.requestDraw();
      });
      for (const name of ["pointerup", "pointercancel", "lostpointercapture"])
        canvas.addEventListener(name, () => { this.pointer = null; });
      canvas.addEventListener("wheel", (event) => {
        event.preventDefault();
        this.zoom = clamp(this.zoom * (event.deltaY < 0 ? 1.12 : 1 / 1.12), 0.6, 2.3);
        this.requestDraw();
      }, {passive: false});
      canvas.addEventListener("keydown", (event) => {
        const turn = 0.12;
        if (event.key === "ArrowLeft") this.yaw -= turn;
        else if (event.key === "ArrowRight") this.yaw += turn;
        else if (event.key === "ArrowUp") this.pitch = clamp(this.pitch - turn, -1.3, 1.3);
        else if (event.key === "ArrowDown") this.pitch = clamp(this.pitch + turn, -1.3, 1.3);
        else if (event.key === "+" || event.key === "=") this.zoom = clamp(this.zoom * 1.12, 0.6, 2.3);
        else if (event.key === "-") this.zoom = clamp(this.zoom / 1.12, 0.6, 2.3);
        else if (event.key === "Home") this.reset();
        else return;
        event.preventDefault();
        this.requestDraw();
      });
    }

    requestDraw() {
      if (this.pending) return;
      this.pending = true;
      requestAnimationFrame(() => { this.pending = false; this.draw(); });
    }

    draw() {
      const canvas = this.canvas, ctx = this.ctx;
      const width = canvas.width, height = canvas.height;
      ctx.fillStyle = "#081722";
      ctx.fillRect(0, 0, width, height);
      ctx.strokeStyle = "#254052";
      ctx.lineWidth = 1;
      for (let x = 40; x < width; x += 52) {
        ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, height); ctx.stroke();
      }
      const basis = cameraBasis(this.yaw, this.pitch);
      const pixels = Math.min(width, height) * 0.33 * this.zoom;
      const project = (position) => {
        const depth = dot(position, basis.eye);
        const perspective = 4.3 / (4.3 - depth);
        return {x: width / 2 + dot(position, basis.right) * pixels * perspective,
          y: height / 2 - dot(position, basis.up) * pixels * perspective, depth};
      };
      const origin = project([0, 0, 0]);
      // Direction arrows are display vectors only, never distance/size scale.
      if (this.mesh && this.directions) {
        this.drawDirectionLine(origin, project(unit(this.directions.earthBody).map((v) => v * 1.55)), "#70b8e9");
        this.drawDirectionLine(origin, project(unit(this.directions.sunBody).map((v) => v * 1.55)), "#f0c568");
      }
      if (this.mesh) this.drawDirectionLine(origin, project([0, 0, 1.25]), "#83d9cf", 1.5);
      if (this.mesh) this.drawMesh(project, basis.eye);
      else {
        ctx.fillStyle = "#a9bdc7";
        ctx.font = "16px Malgun Gothic, sans-serif";
        ctx.textAlign = "center";
        ctx.fillText("CAD 형상을 불러오는 중이거나 표시할 수 없습니다", width / 2, height / 2);
      }
      if (this.mesh) this.drawMarker(project([0, 0, 1.25]), "+Z 본체축", "#83d9cf");
      if (this.mesh && this.directions) {
        this.drawMarker(project(unit(this.directions.earthBody).map((v) => v * 1.55)),
          "지구 방향", "#70b8e9", "earth");
        this.drawMarker(project(unit(this.directions.sunBody).map((v) => v * 1.55)),
          "태양 방향", "#f0c568", "sun");
      }
      if (this.mesh) this.drawAxisGizmo(basis);
    }

    drawMesh(project, eye) {
      const {vertices, triangles, colors, normals} = this.mesh;
      const projected = vertices.map(project);
      const faces = triangles.map(([a, b, c, material], index) => ({a, b, c, material, index,
        depth: (projected[a].depth + projected[b].depth + projected[c].depth) / 3}));
      faces.sort((a, b) => a.depth - b.depth);
      const ctx = this.ctx;
      for (const face of faces) {
        const p = projected[face.a], q = projected[face.b], r = projected[face.c];
        const normal = normals[face.index];
        const shade = normal ? 0.55 + 0.38 * Math.abs(dot(normal, eye)) : 0.55;
        ctx.fillStyle = faceColor(colors[face.material], shade);
        ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(q.x, q.y);
        ctx.lineTo(r.x, r.y); ctx.closePath(); ctx.fill();
      }
    }

    drawDirectionLine(from, to, color, width = 2) {
      const ctx = this.ctx;
      ctx.strokeStyle = color;
      ctx.globalAlpha = 0.72;
      ctx.lineWidth = width;
      ctx.beginPath(); ctx.moveTo(from.x, from.y); ctx.lineTo(to.x, to.y); ctx.stroke();
      if (width >= 2) {
        const angle = Math.atan2(to.y - from.y, to.x - from.x);
        const tipX = to.x - Math.cos(angle) * 17;
        const tipY = to.y - Math.sin(angle) * 17;
        ctx.beginPath();
        ctx.moveTo(tipX, tipY);
        ctx.lineTo(tipX - Math.cos(angle - 0.48) * 9,
          tipY - Math.sin(angle - 0.48) * 9);
        ctx.moveTo(tipX, tipY);
        ctx.lineTo(tipX - Math.cos(angle + 0.48) * 9,
          tipY - Math.sin(angle + 0.48) * 9);
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }

    drawMarker(point, label, color, kind = "axis") {
      const ctx = this.ctx;
      const radius = kind === "axis" ? 5 : 13;
      if (kind === "sun") {
        ctx.strokeStyle = "#f0c568";
        ctx.lineWidth = 2;
        for (let ray = 0; ray < 8; ray++) {
          const angle = ray * Math.PI / 4;
          ctx.beginPath();
          ctx.moveTo(point.x + Math.cos(angle) * 16, point.y + Math.sin(angle) * 16);
          ctx.lineTo(point.x + Math.cos(angle) * 22, point.y + Math.sin(angle) * 22);
          ctx.stroke();
        }
      }
      ctx.fillStyle = color;
      ctx.beginPath(); ctx.arc(point.x, point.y, radius, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = "#e7f3f5"; ctx.lineWidth = 1.5; ctx.stroke();
      if (kind === "earth") {
        ctx.fillStyle = "#90cba4";
        ctx.beginPath();
        ctx.arc(point.x - 3, point.y + 1, 4, 0, Math.PI * 2);
        ctx.fill();
        ctx.beginPath();
        ctx.arc(point.x + 5, point.y - 4, 2.5, 0, Math.PI * 2);
        ctx.fill();
      }
      if (kind === "sun") {
        ctx.fillStyle = "#fff2be";
        ctx.beginPath(); ctx.arc(point.x - 4, point.y - 4, 4, 0, Math.PI * 2); ctx.fill();
      }
      ctx.font = "bold 13px Malgun Gothic, sans-serif";
      ctx.textAlign = "left";
      const x = clamp(point.x + 14, 8, this.canvas.width - 98);
      const y = clamp(point.y + 5, 18, this.canvas.height - 8);
      ctx.fillStyle = "#071521d9";
      ctx.fillRect(x - 5, y - 16, 96, 22);
      ctx.fillStyle = color; ctx.fillText(label, x, y);
    }

    drawAxisGizmo(basis) {
      const ctx = this.ctx;
      const ox = 69, oy = this.canvas.height - 64;
      const colors = ["#f2a99a", "#a7db99", "#8dc1f5"];
      ctx.font = "bold 12px Consolas, monospace";
      ctx.textAlign = "left";
      for (let axis = 0; axis < 3; axis++) {
        const dx = dot(AXES[axis], basis.right) * 31;
        const dy = -dot(AXES[axis], basis.up) * 31;
        ctx.strokeStyle = colors[axis]; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.moveTo(ox, oy); ctx.lineTo(ox + dx, oy + dy); ctx.stroke();
        ctx.fillStyle = colors[axis];
        ctx.fillText(["+X", "+Y", "+Z"][axis], ox + dx + 4, oy + dy + 3);
      }
      ctx.fillStyle = "#d7e9ec";
      ctx.beginPath(); ctx.arc(ox, oy, 3, 0, Math.PI * 2); ctx.fill();
    }
  }

  return {create: (canvas, onStatus) => new Viewer(canvas, onStatus), meshFromJson};
})();
