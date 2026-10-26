"use strict";

// A body-centred, interactive 3D view of the bounded CAD triangle mesh.
// Orbit-camera movement changes only the viewpoint; TM orientation is expressed
// by the Earth/Sun direction vectors supplied in the spacecraft body frame.
window.rosAttitudeViewer = (() => {
  const MAX_BYTES = 4 * 1024 * 1024;
  const MAX_VERTICES = 40000;
  const MAX_TRIANGLES = 25000;
  const MAX_MATERIALS = 64;
  const DEFAULT_YAW = -2.6;
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
    // Body +X is visually up. Start on the -Y/-Z side so nadir Earth (+Z in
    // the test attitude) forms a left-side limb behind the spacecraft.
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
      this.preview = false;
      this.refineTimer = null;
      this.earthTexture = null;
      this.earthLayer = null;
      this.loadEarthTexture();
      this.installControls();
      this.draw();
    }

    loadEarthTexture() {
      if (typeof Image === "undefined" || typeof document === "undefined") return;
      const image = new Image();
      image.onload = () => {
        try {
          const canvas = document.createElement("canvas");
          canvas.width = 1600;
          canvas.height = 800;
          const ctx = canvas.getContext("2d", {willReadFrequently: true});
          ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
          this.earthTexture = ctx.getImageData(0, 0, canvas.width, canvas.height);
        } catch (_) {
          this.earthTexture = null;
        }
        this.requestDraw();
      };
      image.onerror = () => this.requestDraw();
      image.src = "/assets/earth-blue-marble-october.jpg";
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
        canvas.addEventListener(name, () => {
          this.pointer = null;
          this.requestDraw();
        });
      canvas.addEventListener("wheel", (event) => {
        event.preventDefault();
        this.zoom = clamp(this.zoom * (event.deltaY < 0 ? 1.12 : 1 / 1.12), 0.6, 2.3);
        this.queueRefine();
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
        this.queueRefine();
        this.requestDraw();
      });
    }

    queueRefine() {
      if (typeof setTimeout !== "function") return;
      this.preview = true;
      clearTimeout(this.refineTimer);
      this.refineTimer = setTimeout(() => {
        this.preview = false;
        this.requestDraw();
      }, 180);
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
      this.drawSpaceScene(basis, pixels);
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
      if (this.mesh) {
        this.drawMesh(project, basis.eye);
        this.drawBodyAxes(axes, origin);
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
      // Keep the projected body-axis endpoints within the canvas. Their rays
      // still pass through the model origin in the actual body-frame direction.
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
      ctx.save();
      ctx.lineCap = "round";
      const segment = (from, to, color, width, alpha, dashes) => {
        ctx.setLineDash(dashes);
        ctx.globalAlpha = alpha;
        ctx.beginPath(); ctx.moveTo(from.x, from.y); ctx.lineTo(to.x, to.y);
        ctx.strokeStyle = "#06101a"; ctx.lineWidth = width + 3; ctx.stroke();
        ctx.strokeStyle = color; ctx.lineWidth = width; ctx.stroke();
      };
      let endOnAxis = -1;
      for (let axis = 0; axis < AXES.length; axis++) {
        const {positive, negative} = axes[axis];
        const color = AXIS_COLORS[axis];
        const span = Math.hypot(positive.x - origin.x, positive.y - origin.y);
        // All three axes pass through the satellite centre. Draw on top of the
        // CAD so the body never hides an axis completely; dashed negative arms
        // still distinguish the half pointing away from each positive arrow.
        if (Math.hypot(negative.x - origin.x, negative.y - origin.y) >= 12)
          segment(negative, origin, color, 1.5, 0.65, [5, 5]);
        if (span < 18) {
          // A line viewed end-on has zero projected length. Use the standard
          // dot/cross sight-line marker at the *same* body-frame origin.
          endOnAxis = axis;
          continue;
        }
        segment(origin, positive, color, 2.5, 0.95, []);
        const angle = Math.atan2(positive.y - origin.y, positive.x - origin.x);
        ctx.setLineDash([]);
        ctx.globalAlpha = 1;
        ctx.strokeStyle = "#06101a"; ctx.lineWidth = 5.5;
        ctx.beginPath(); ctx.moveTo(positive.x, positive.y);
        ctx.lineTo(positive.x - Math.cos(angle - 0.42) * 10,
          positive.y - Math.sin(angle - 0.42) * 10);
        ctx.moveTo(positive.x, positive.y);
        ctx.lineTo(positive.x - Math.cos(angle + 0.42) * 10,
          positive.y - Math.sin(angle + 0.42) * 10);
        ctx.stroke();
        ctx.strokeStyle = color; ctx.lineWidth = 2.5; ctx.stroke();
      }
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;
      if (endOnAxis >= 0) {
        const color = AXIS_COLORS[endOnAxis];
        const positive = axes[endOnAxis].positive;
        ctx.fillStyle = "#07131f";
        ctx.beginPath(); ctx.arc(origin.x, origin.y, 10, 0, 2 * Math.PI); ctx.fill();
        ctx.strokeStyle = color; ctx.lineWidth = 2.3; ctx.stroke();
        if (positive.depth >= origin.depth) {
          ctx.fillStyle = color;
          ctx.beginPath(); ctx.arc(origin.x, origin.y, 3, 0, 2 * Math.PI); ctx.fill();
        } else {
          ctx.strokeStyle = color; ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.moveTo(origin.x - 3, origin.y - 3);
          ctx.lineTo(origin.x + 3, origin.y + 3);
          ctx.moveTo(origin.x + 3, origin.y - 3);
          ctx.lineTo(origin.x - 3, origin.y + 3);
          ctx.stroke();
        }
      } else {
        ctx.setLineDash([]);
        ctx.fillStyle = "#07131f";
        ctx.beginPath(); ctx.arc(origin.x, origin.y, 4, 0, 2 * Math.PI); ctx.fill();
        ctx.strokeStyle = "#e7edf3"; ctx.lineWidth = 1.4; ctx.stroke();
      }
      ctx.restore();
    }

    drawAxisLabels(axes, origin) {
      const ctx = this.ctx;
      ctx.font = "bold 13px Consolas, monospace";
      ctx.textAlign = "center";
      for (let axis = 0; axis < AXES.length; axis++) {
        const point = axes[axis].positive;
        const dx = point.x - origin.x, dy = point.y - origin.y;
        const distance = Math.hypot(dx, dy);
        const endOn = distance < 18;
        const x = clamp(endOn ? origin.x + 33 :
          point.x + dx / distance * 18 - (axis === 0 ? 26 : 0),
          20, this.canvas.width - 20);
        const y = clamp(endOn ? origin.y - 14 :
          point.y + dy / distance * 18 + 4, 20, this.canvas.height - 12);
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

    drawSpaceScene(basis, pixels) {
      if (!this.directions) return;
      const earth = unit(this.directions.earthBody);
      const sun = unit(this.directions.sunBody);
      const angle = this.directions.earthAngularRadiusRad;
      // The radius is computed from the FDS satellite position. A test-only
      // direction supplied without that field uses the 500 km demo altitude.
      const radius = Number.isFinite(angle) && angle > 0 && angle < Math.PI / 2 ?
        angle : Math.asin(6378137 / (6378137 + 500000));
      this.drawSunDisc(basis, pixels, sun);
      this.drawEarthHorizon(basis, pixels, earth, sun, radius);
    }

    drawSunDisc(basis, pixels, sun) {
      // Project a distant direction onto the same orbit-camera view as the CAD.
      // A source outside the camera hemisphere stays out of sight.
      const depth = -dot(sun, basis.eye);
      if (depth <= 0.02) return;
      const focal = pixels * 4.3;
      const x = this.canvas.width / 2 + dot(sun, basis.right) * focal / depth;
      const y = this.canvas.height / 2 - dot(sun, basis.up) * focal / depth;
      if (x < -100 || x > this.canvas.width + 100 ||
          y < -100 || y > this.canvas.height + 100) return;
      const ctx = this.ctx;
      const halo = ctx.createRadialGradient(x, y, 1, x, y, 88);
      halo.addColorStop(0, "rgba(255,248,205,0.96)");
      halo.addColorStop(0.09, "rgba(255,216,133,0.68)");
      halo.addColorStop(0.38, "rgba(255,193,91,0.13)");
      halo.addColorStop(1, "rgba(255,193,91,0)");
      ctx.fillStyle = halo;
      ctx.beginPath(); ctx.arc(x, y, 88, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = "#fff8da";
      ctx.beginPath(); ctx.arc(x, y, Math.max(4, focal * 0.00465), 0, Math.PI * 2); ctx.fill();
    }

    drawEarthHorizon(basis, pixels, earth, sun, angularRadius) {
      if (typeof document === "undefined") return;
      const centreAngle = Math.acos(clamp(-dot(earth, basis.eye), -1, 1));
      const cornerAngle = Math.atan(Math.hypot(this.canvas.width, this.canvas.height) /
        (2 * pixels * 4.3));
      if (centreAngle > angularRadius + cornerAngle + 0.04) return;
      // Dragging uses a coarse sky buffer while the CAD stays at full canvas
      // resolution. A full sky render follows when the operator stops moving.
      const moving = this.pointer || this.preview;
      const scale = moving ?
        Math.min(1, 300 / this.canvas.width, 180 / this.canvas.height) :
        Math.min(1, 700 / this.canvas.width, 420 / this.canvas.height);
      const width = Math.max(1, Math.round(this.canvas.width * scale));
      const height = Math.max(1, Math.round(this.canvas.height * scale));
      if (!this.earthLayer) {
        const canvas = document.createElement("canvas");
        this.earthLayer = {canvas, ctx: canvas.getContext("2d")};
      }
      const layer = this.earthLayer;
      if (!layer.ctx || !layer.ctx.createImageData || !layer.ctx.putImageData) return;
      if (layer.canvas.width !== width || layer.canvas.height !== height) {
        layer.canvas.width = width;
        layer.canvas.height = height;
      }
      const output = layer.ctx.createImageData(width, height);
      const target = output.data;
      const source = this.earthTexture?.data;
      const sourceWidth = this.earthTexture?.width || 0;
      const sourceHeight = this.earthTexture?.height || 0;
      const columns = this.directions.bodyToItrf;
      const mapped = Array.isArray(columns) && columns.length === 3 &&
        columns.every((axis) => Array.isArray(axis) && axis.length === 3 &&
          axis.every(Number.isFinite));
      const transform = mapped ? columns : [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
      const distance = 1 / Math.sin(angularRadius);
      const cx = earth[0] * distance, cy = earth[1] * distance,
        cz = earth[2] * distance;
      const horizon = distance * distance - 1;
      const focal = pixels * 4.3 * scale;
      const halfWidth = width / 2, halfHeight = height / 2;
      let hasEarth = false;
      for (let py = 0; py < height; py++) {
        const vertical = (halfHeight - py - 0.5) / focal;
        for (let px = 0; px < width; px++) {
          const horizontal = (px + 0.5 - halfWidth) / focal;
          const inverse = 1 / Math.sqrt(1 + horizontal * horizontal +
            vertical * vertical);
          const rx = (-basis.eye[0] + basis.right[0] * horizontal +
            basis.up[0] * vertical) * inverse;
          const ry = (-basis.eye[1] + basis.right[1] * horizontal +
            basis.up[1] * vertical) * inverse;
          const rz = (-basis.eye[2] + basis.right[2] * horizontal +
            basis.up[2] * vertical) * inverse;
          const approach = rx * cx + ry * cy + rz * cz;
          if (approach <= 0) continue;
          const discriminant = approach * approach - horizon;
          const index = (py * width + px) * 4;
          if (discriminant < 0) {
            // A narrow atmosphere just outside the physical horizon.
            if (discriminant > -0.022) {
              const strength = 1 + discriminant / 0.022;
              target[index] = 58;
              target[index + 1] = 145;
              target[index + 2] = 221;
              target[index + 3] = Math.round(55 * strength * strength);
            }
            continue;
          }
          hasEarth = true;
          const distanceToSurface = approach - Math.sqrt(discriminant);
          const nx = rx * distanceToSurface - cx;
          const ny = ry * distanceToSurface - cy;
          const nz = rz * distanceToSurface - cz;
          let red = 30, green = 88, blue = 139;
          if (source) {
            const ex = nx * transform[0][0] + ny * transform[1][0] + nz * transform[2][0];
            const ey = nx * transform[0][1] + ny * transform[1][1] + nz * transform[2][1];
            const ez = nx * transform[0][2] + ny * transform[1][2] + nz * transform[2][2];
            const longitude = Math.atan2(ey, ex);
            const latitude = Math.asin(clamp(ez, -1, 1));
            const tx = Math.min(sourceWidth - 1,
              Math.floor((longitude / (2 * Math.PI) + 0.5) * sourceWidth));
            const ty = Math.min(sourceHeight - 1,
              Math.floor((0.5 - latitude / Math.PI) * sourceHeight));
            const sourceIndex = (ty * sourceWidth + tx) * 4;
            red = source[sourceIndex];
            green = source[sourceIndex + 1];
            blue = source[sourceIndex + 2];
          }
          const sunlight = Math.max(0, nx * sun[0] + ny * sun[1] + nz * sun[2]);
          const illumination = 0.24 + 0.76 * Math.sqrt(sunlight);
          const rim = Math.pow(1 - clamp(Math.sqrt(discriminant), 0, 1), 6);
          target[index] = Math.min(255, red * illumination + 36 * rim);
          target[index + 1] = Math.min(255, green * illumination + 96 * rim);
          target[index + 2] = Math.min(255, blue * illumination + 150 * rim);
          target[index + 3] = 255;
        }
      }
      if (!hasEarth) return;
      layer.ctx.putImageData(output, 0, 0);
      this.ctx.drawImage(layer.canvas, 0, 0, this.canvas.width, this.canvas.height);
    }
  }

  return {create: (canvas, onStatus) => new Viewer(canvas, onStatus), meshFromJson};
})();
