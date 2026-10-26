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
  const AXIS_COLORS = ["#ee8e84", "#a6d78d", "#8fbef0"];
  const STARFIELD = (() => {
    // Stable on every frame, including while the camera is being dragged.
    let seed = 0x506144;
    const next = () => {
      seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
      return seed / 0x100000000;
    };
    return Array.from({length: 90}, () => ({
      x: next(), y: next(), radius: 0.45 + next() * 1.15,
      alpha: 0.18 + next() * 0.48,
    }));
  })();
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
      ctx.fillStyle = "#07131f";
      ctx.fillRect(0, 0, width, height);
      this.drawStars();
      const basis = cameraBasis(this.yaw, this.pitch);
      const pixels = Math.min(width, height) * 0.33 * this.zoom;
      const project = (position) => {
        const depth = dot(position, basis.eye);
        const perspective = 4.3 / (4.3 - depth);
        return {x: width / 2 + dot(position, basis.right) * pixels * perspective,
          y: height / 2 - dot(position, basis.up) * pixels * perspective, depth};
      };
      const origin = project([0, 0, 0]);
      const axes = this.mesh ? AXES.map((direction) => ({
        positive: this.fitDirection(project, direction, 1.5, 30, 30),
        negative: this.fitDirection(project, direction.map((value) => -value),
          1.3, 24, 24),
      })) : [];
      // Bodies show directions from the satellite, not physical distance or size.
      const bodies = this.mesh && this.directions ? [
        {kind: "earth", point: this.fitDirection(project, this.directions.earthBody,
          1.9, 74, 38)},
        {kind: "sun", point: this.fitDirection(project, this.directions.sunBody,
          1.9, 72, 36)},
      ].sort((a, b) => a.point.depth - b.point.depth) : [];
      if (this.mesh) {
        this.drawBodyAxes(axes, origin);
        for (const body of bodies) this.drawDirectionGuide(origin, body.point,
          body.kind === "earth" ? "#68a9d9" : "#e7b966", body.kind === "earth" ? 24 : 21);
        for (const body of bodies) if (body.point.depth < 0) this.drawCelestial(body);
        this.drawMesh(project, basis.eye);
        for (const body of bodies) if (body.point.depth >= 0) this.drawCelestial(body);
        this.drawAxisLabels(axes, origin);
      } else {
        ctx.fillStyle = "#a9bdc7";
        ctx.font = "16px Malgun Gothic, sans-serif";
        ctx.textAlign = "center";
        ctx.fillText("CAD 형상을 불러오는 중이거나 표시할 수 없습니다", width / 2, height / 2);
      }
    }

    drawStars() {
      const ctx = this.ctx;
      ctx.fillStyle = "#d9e9f2";
      for (const star of STARFIELD) {
        ctx.globalAlpha = star.alpha;
        ctx.beginPath();
        ctx.arc(star.x * this.canvas.width, star.y * this.canvas.height,
          star.radius, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
    }

    fitDirection(project, direction, distance, marginX, marginY) {
      // Reduce only illustrative display distance to keep direction objects in view.
      // The projected ray remains aligned with the body-frame direction.
      const vector = unit(direction);
      const at = (amount) => project(vector.map((value) => value * amount));
      const inside = (point) => point.x >= marginX &&
        point.x <= this.canvas.width - marginX &&
        point.y >= marginY && point.y <= this.canvas.height - marginY;
      if (inside(at(distance))) return at(distance);
      let low = 0, high = distance;
      for (let attempt = 0; attempt < 16; attempt++) {
        const mid = (low + high) / 2;
        if (inside(at(mid))) low = mid;
        else high = mid;
      }
      return at(low);
    }

    drawBodyAxes(axes, origin) {
      const ctx = this.ctx;
      for (let axis = 0; axis < AXES.length; axis++) {
        const {positive, negative} = axes[axis];
        ctx.strokeStyle = AXIS_COLORS[axis];
        ctx.globalAlpha = 0.28;
        ctx.lineWidth = 1.4;
        ctx.beginPath(); ctx.moveTo(negative.x, negative.y);
        ctx.lineTo(origin.x, origin.y); ctx.stroke();
        ctx.globalAlpha = 0.9;
        ctx.lineWidth = 2.5;
        ctx.beginPath(); ctx.moveTo(origin.x, origin.y);
        ctx.lineTo(positive.x, positive.y); ctx.stroke();
        const angle = Math.atan2(positive.y - origin.y, positive.x - origin.x);
        ctx.beginPath(); ctx.moveTo(positive.x, positive.y);
        ctx.lineTo(positive.x - Math.cos(angle - 0.42) * 10,
          positive.y - Math.sin(angle - 0.42) * 10);
        ctx.moveTo(positive.x, positive.y);
        ctx.lineTo(positive.x - Math.cos(angle + 0.42) * 10,
          positive.y - Math.sin(angle + 0.42) * 10);
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }

    drawAxisLabels(axes, origin) {
      const ctx = this.ctx;
      ctx.font = "bold 13px Consolas, monospace";
      ctx.textAlign = "center";
      for (let axis = 0; axis < AXES.length; axis++) {
        const point = axes[axis].positive;
        const dx = point.x - origin.x, dy = point.y - origin.y;
        const distance = Math.hypot(dx, dy) || 1;
        const x = clamp(point.x + dx / distance * 18 - (axis === 0 ? 26 : 0),
          20, this.canvas.width - 20);
        const y = clamp(point.y + dy / distance * 18 + 4, 20, this.canvas.height - 12);
        ctx.fillStyle = "#07131fe0";
        ctx.fillRect(x - 16, y - 15, 32, 21);
        ctx.fillStyle = AXIS_COLORS[axis];
        ctx.fillText(["+X", "+Y", "+Z"][axis], x, y);
      }
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

    drawDirectionGuide(from, to, color, radius) {
      const ctx = this.ctx;
      ctx.strokeStyle = color;
      ctx.globalAlpha = 0.36;
      ctx.lineWidth = 1.3;
      const dx = to.x - from.x, dy = to.y - from.y;
      const span = Math.hypot(dx, dy) || 1;
      ctx.beginPath(); ctx.moveTo(from.x, from.y);
      ctx.lineTo(to.x - dx / span * radius, to.y - dy / span * radius);
      ctx.stroke();
      ctx.globalAlpha = 1;
    }

    drawCelestial({kind, point}) {
      const ctx = this.ctx;
      const {x, y} = point;
      const radius = kind === "earth" ? 23 : 20;
      const glow = ctx.createRadialGradient(x, y, radius * 0.3,
        x, y, radius * 1.75);
      if (kind === "earth") {
        glow.addColorStop(0, "#59a7d060");
        glow.addColorStop(1, "#59a7d000");
      } else {
        glow.addColorStop(0, "#ffd07780");
        glow.addColorStop(1, "#f5a52900");
      }
      ctx.fillStyle = glow;
      ctx.beginPath(); ctx.arc(x, y, radius * 1.75, 0, Math.PI * 2); ctx.fill();
      if (kind === "earth") this.drawEarth(x, y, radius);
      else this.drawSun(x, y, radius);
      const label = kind === "earth" ? "지구" : "태양";
      const color = kind === "earth" ? "#95ccef" : "#f5d082";
      const labelX = clamp(x + radius + 15, 30, this.canvas.width - 30);
      const labelY = clamp(y + 4, 20, this.canvas.height - 8);
      ctx.fillStyle = "#07131fe0";
      ctx.fillRect(labelX - 23, labelY - 16, 46, 22);
      ctx.fillStyle = color;
      ctx.font = "bold 13px Malgun Gothic, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(label, labelX, labelY);
    }

    drawEarth(x, y, radius) {
      const ctx = this.ctx;
      const sea = ctx.createRadialGradient(x - 8, y - 9, 1,
        x + 5, y + 5, radius * 1.3);
      sea.addColorStop(0, "#7dc7ea");
      sea.addColorStop(0.55, "#286eaa");
      sea.addColorStop(1, "#0b294c");
      ctx.save();
      ctx.beginPath(); ctx.arc(x, y, radius, 0, Math.PI * 2); ctx.clip();
      ctx.fillStyle = sea;
      ctx.fillRect(x - radius, y - radius, radius * 2, radius * 2);
      ctx.fillStyle = "#81ba8b";
      ctx.beginPath();
      ctx.moveTo(x - 17, y - 8); ctx.lineTo(x - 9, y - 13);
      ctx.lineTo(x - 3, y - 9); ctx.lineTo(x - 6, y - 3);
      ctx.lineTo(x - 2, y + 2); ctx.lineTo(x - 8, y + 7);
      ctx.lineTo(x - 12, y + 3); ctx.lineTo(x - 18, y + 2); ctx.closePath(); ctx.fill();
      ctx.beginPath();
      ctx.moveTo(x + 6, y - 8); ctx.lineTo(x + 16, y - 5);
      ctx.lineTo(x + 18, y + 2); ctx.lineTo(x + 12, y + 5);
      ctx.lineTo(x + 10, y + 12); ctx.lineTo(x + 4, y + 8);
      ctx.lineTo(x + 5, y + 1); ctx.closePath(); ctx.fill();
      ctx.strokeStyle = "#deeff7";
      ctx.globalAlpha = 0.58;
      ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.arc(x - 3, y + 2, radius * 0.71, 3.65, 5.25); ctx.stroke();
      ctx.beginPath(); ctx.arc(x + 5, y - 4, radius * 0.62, 0.35, 1.8); ctx.stroke();
      ctx.restore();
      ctx.strokeStyle = "#9fd9ee";
      ctx.globalAlpha = 0.72;
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.arc(x, y, radius, 0, Math.PI * 2); ctx.stroke();
      ctx.globalAlpha = 1;
    }

    drawSun(x, y, radius) {
      const ctx = this.ctx;
      ctx.strokeStyle = "#f5bd63";
      ctx.globalAlpha = 0.75;
      ctx.lineWidth = 1.8;
      for (let ray = 0; ray < 12; ray++) {
        const angle = ray * Math.PI / 6;
        ctx.beginPath();
        ctx.moveTo(x + Math.cos(angle) * (radius + 5),
          y + Math.sin(angle) * (radius + 5));
        ctx.lineTo(x + Math.cos(angle) * (radius + 15),
          y + Math.sin(angle) * (radius + 15));
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
      const surface = ctx.createRadialGradient(x - 7, y - 8, 1,
        x + 5, y + 5, radius * 1.35);
      surface.addColorStop(0, "#fff8ca");
      surface.addColorStop(0.58, "#ffcc67");
      surface.addColorStop(1, "#d8802a");
      ctx.fillStyle = surface;
      ctx.beginPath(); ctx.arc(x, y, radius, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = "#ffe5a0";
      ctx.lineWidth = 1;
      ctx.stroke();
    }
  }

  return {create: (canvas, onStatus) => new Viewer(canvas, onStatus), meshFromJson};
})();
