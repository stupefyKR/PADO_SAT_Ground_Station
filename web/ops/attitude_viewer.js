"use strict";

// A body-centred, interactive 3D view of the bounded CAD triangle mesh.
// Orbit-camera movement changes only the viewpoint; TM orientation is expressed
// by the Earth/Sun direction vectors supplied in the spacecraft body frame.
window.rosAttitudeViewer = (() => {
  const MAX_BYTES = 4 * 1024 * 1024;
  const MAX_VERTICES = 40000;
  const MAX_TRIANGLES = 25000;
  const MAX_MATERIALS = 64;
  const MAX_APPEARANCE_BYTES = 64 * 1024;
  const MAX_TEXTURE_TRIANGLES = 512;
  const MAX_TEXTURE_PIXELS = 16 * 1024 * 1024;
  const MAX_TOTAL_TEXTURE_PIXELS = 32 * 1024 * 1024;
  const DEFAULT_YAW = -1.0;
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
    return {vertices: adjusted, triangles, colors, normals, centre, scale};
  }

  function quadMapper(quad) {
    // Homography from the unit square to the supplied source-image quadrilateral.
    // This undoes the perspective already present in a CAD screenshot.
    const [[x0, y0], [x1, y1], [x2, y2], [x3, y3]] = quad;
    const dx1 = x1 - x2, dx2 = x3 - x2, dx3 = x0 - x1 + x2 - x3;
    const dy1 = y1 - y2, dy2 = y3 - y2, dy3 = y0 - y1 + y2 - y3;
    const determinant = dx1 * dy2 - dx2 * dy1;
    let g = 0, h = 0;
    if (Math.abs(dx3) + Math.abs(dy3) > 1e-10) {
      if (Math.abs(determinant) < 1e-10) throw Error("텍스처 사각형이 퇴화했습니다.");
      g = (dx3 * dy2 - dx2 * dy3) / determinant;
      h = (dx1 * dy3 - dx3 * dy1) / determinant;
    }
    const a = x1 - x0 + g * x1, b = x3 - x0 + h * x3;
    const d = y1 - y0 + g * y1, e = y3 - y0 + h * y3;
    const denominators = [1, 1 + g, 1 + h, 1 + g + h];
    if (denominators.some((value) => !Number.isFinite(value) || value <= 1e-6))
      throw Error("텍스처 사각형의 투영이 유효하지 않습니다.");
    return (s, t) => {
      const denominator = g * s + h * t + 1;
      return [(a * s + b * t + x0) / denominator,
        (d * s + e * t + y0) / denominator];
    };
  }

  function appearanceFromJson(raw, mesh) {
    const finite = (value) => typeof value === "number" && Number.isFinite(value);
    const point = (value, dimensions) => Array.isArray(value) && value.length === dimensions &&
      value.every((entry) => finite(entry) && Math.abs(entry) < 1e7);
    if (!mesh || !point(mesh.centre, 3) || !finite(mesh.scale) || mesh.scale <= 0 ||
        !raw || raw.schema !== "pado.cad-appearance.v1" || !raw.textures ||
        typeof raw.textures !== "object" || Array.isArray(raw.textures) ||
        !Array.isArray(raw.patches) || raw.patches.length < 1 || raw.patches.length > 12)
      throw Error("CAD 표면 이미지 형식이 유효하지 않습니다.");
    const textures = Object.entries(raw.textures);
    if (textures.length < 1 || textures.length > 12 || textures.some(([id, url]) =>
      !/^[a-z][a-z0-9-]{0,47}$/.test(id) || typeof url !== "string" ||
      !/^\/assets\/pado-sat-surface-[a-z0-9-]+\.png$/.test(url)))
      throw Error("허용된 로컬 CAD 표면 이미지만 사용할 수 있습니다.");
    const textureIds = new Set(textures.map(([id]) => id));
    const patchIds = new Set();
    const triangles = [];
    for (const patch of raw.patches) {
      if (!patch || typeof patch.id !== "string" || !/^[a-z][a-z0-9-]{0,47}$/.test(patch.id) ||
          patchIds.has(patch.id) || !textureIds.has(patch.texture) ||
          !Array.isArray(patch.corners) || patch.corners.length !== 4 ||
          !patch.corners.every((corner) => point(corner, 3)) ||
          !Array.isArray(patch.image_quad) || patch.image_quad.length !== 4 ||
          !patch.image_quad.every((corner) => point(corner, 2) &&
            corner.every((entry) => entry >= 0 && entry <= 1)) ||
          !point(patch.normal, 3) || !unit(patch.normal) ||
          !Array.isArray(patch.subdivisions) || patch.subdivisions.length !== 2 ||
          !patch.subdivisions.every((value) => Number.isInteger(value) && value >= 1 && value <= 16))
        throw Error("CAD 표면 패치가 유효하지 않습니다.");
      patchIds.add(patch.id);
      const [nu, nv] = patch.subdivisions;
      if (triangles.length + 2 * nu * nv > MAX_TEXTURE_TRIANGLES)
        throw Error("CAD 텍스처 삼각형 수가 허용 범위를 넘습니다.");
      const q = patch.image_quad;
      let orientation = 0;
      for (let index = 0; index < 4; index++) {
        const a = q[index], b = q[(index + 1) % 4], c = q[(index + 2) % 4];
        const turn = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]);
        if (Math.abs(turn) < 1e-8 || (orientation && Math.sign(turn) !== orientation))
          throw Error("텍스처 사각형은 볼록해야 합니다.");
        orientation = Math.sign(turn);
      }
      const normal = unit(patch.normal);
      const corners = patch.corners.map((corner) => corner.map((value, axis) =>
        (value - mesh.centre[axis]) * mesh.scale));
      if (corners.some((corner) => corner.some((value) => Math.abs(value) > 2)))
        throw Error("CAD 표면 패치가 형상 범위를 벗어났습니다.");
      const edgeA = corners[1].map((value, axis) => value - corners[0][axis]);
      const edgeB = corners[3].map((value, axis) => value - corners[0][axis]);
      if (length(cross(edgeA, edgeB)) < 1e-9 || corners.some((corner) =>
        Math.abs(dot(corner.map((value, axis) => value - corners[0][axis]), normal)) > 1e-5))
        throw Error("CAD 표면 패치는 지정된 법선의 평면이어야 합니다.");
      let cornerOrientation = 0;
      for (let index = 0; index < 4; index++) {
        const a = corners[index], b = corners[(index + 1) % 4], c = corners[(index + 2) % 4];
        const turn = dot(cross(b.map((value, axis) => value - a[axis]),
          c.map((value, axis) => value - b[axis])), normal);
        if (Math.abs(turn) < 1e-9 ||
            (cornerOrientation && Math.sign(turn) !== cornerOrientation))
          throw Error("CAD 표면 사각형은 볼록해야 합니다.");
        cornerOrientation = Math.sign(turn);
      }
      const mapImage = quadMapper(q);
      const grid = [];
      for (let v = 0; v <= nv; v++) for (let u = 0; u <= nu; u++) {
        const s = u / nu, t = v / nv;
        const weights = [(1 - s) * (1 - t), s * (1 - t), s * t, (1 - s) * t];
        grid.push({position: [0, 1, 2].map((axis) =>
          corners.reduce((sum, corner, index) => sum + corner[axis] * weights[index], 0)),
        uv: mapImage(s, t)});
      }
      for (let v = 0; v < nv; v++) for (let u = 0; u < nu; u++) {
        const a = grid[v * (nu + 1) + u], b = grid[v * (nu + 1) + u + 1];
        const c = grid[(v + 1) * (nu + 1) + u + 1], d = grid[(v + 1) * (nu + 1) + u];
        for (const vertices of [[a, b, c], [a, c, d]])
          triangles.push({texture: patch.texture, normal, vertices});
      }
    }
    return {textures: Object.fromEntries(textures), triangles};
  }

  function cameraBasis(yaw, pitch) {
    // Body +Z (along-track in the test attitude) is the camera's up reference.
    // The initial +X/-Y view puts nadir Earth (-X) behind the left of the CAD.
    const eye = [Math.cos(pitch) * Math.cos(yaw), Math.cos(pitch) * Math.sin(yaw),
      Math.sin(pitch)];
    const right = unit(cross([0, 0, 1], eye));
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
      this.appearance = null;
      this.glRenderer = null;
      this.textureCache = new Map();
      this.directions = null;
      this.stateProvided = false;
      this.orientationKnown = false;
      this.orientationHeld = false;
      this.positionKnown = false;
      this.sunStatus = "NO_DATA";
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
      this.appearance = null;
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
        this.configureGraphics();
        this.onStatus("PADO-SAT CAD · 축 정렬 추정");
      } catch (error) {
        this.mesh = null;
        this.configureGraphics();
        this.onStatus(`CAD 형상 표시 불가 · ${error.message}`);
      }
      this.requestDraw();
      return !!this.mesh;
    }

    configureGraphics() {
      try { this.glRenderer?.dispose(); } catch (_) { /* Already lost context. */ }
      this.glRenderer = null;
      if (this.mesh) {
        try { this.glRenderer = window.rosAttitudeGl?.create(this.mesh) || null; }
        catch (_) { this.glRenderer = null; }
      }
    }

    loadTexture(url) {
      if (this.textureCache.has(url)) return this.textureCache.get(url);
      const pending = new Promise((resolve) => {
        if (typeof Image === "undefined") { resolve(null); return; }
        const image = new Image();
        let timer = null;
        const finish = (value) => {
          if (timer !== null && typeof clearTimeout === "function") clearTimeout(timer);
          image.onload = null;
          image.onerror = null;
          resolve(value);
        };
        image.onload = () => {
          const width = image.naturalWidth || image.width;
          const height = image.naturalHeight || image.height;
          if (!Number.isInteger(width) || !Number.isInteger(height) ||
              width < 1 || height < 1 || width > 4096 || height > 4096 ||
              width * height > MAX_TEXTURE_PIXELS) { finish(null); return; }
          let source = image;
          // The supplied CAD captures have a pure white background. Remove
          // only those nearly-white pixels in the working canvas; PNG bytes
          // and every coloured surface pixel remain unchanged on disk.
          if (typeof document !== "undefined") {
            try {
              const canvas = document.createElement("canvas");
              canvas.width = width; canvas.height = height;
              const ctx = canvas.getContext("2d", {willReadFrequently: true});
              ctx.drawImage(image, 0, 0);
              const pixels = ctx.getImageData(0, 0, width, height);
              for (let index = 0; index < pixels.data.length; index += 4)
                if (pixels.data[index] > 252 && pixels.data[index + 1] > 252 &&
                    pixels.data[index + 2] > 252) pixels.data[index + 3] = 0;
              ctx.putImageData(pixels, 0, 0);
              source = canvas;
            } catch (_) { /* Colour-only CAD remains available if readback fails. */ }
          }
          finish({image: source, width, height});
        };
        image.onerror = () => finish(null);
        image.decoding = "async";
        if (typeof setTimeout === "function") timer = setTimeout(() => finish(null), 10000);
        image.src = url;
      });
      this.textureCache.set(url, pending);
      return pending;
    }

    async loadAppearance(url = "/assets/pado-sat-appearance.json") {
      const mesh = this.mesh;
      this.appearance = null;
      try {
        if (!mesh || url !== "/assets/pado-sat-appearance.json")
          throw Error("CAD 형상과 지정된 로컬 표면 설정이 필요합니다.");
        const response = await fetch(url, {credentials: "same-origin", cache: "no-store"});
        if (!response.ok) throw Error(`표면 설정 응답 ${response.status}`);
        if (Number(response.headers.get("content-length") || 0) > MAX_APPEARANCE_BYTES)
          throw Error("CAD 표면 설정이 허용 크기를 넘습니다.");
        const bytes = await response.arrayBuffer();
        if (bytes.byteLength < 16 || bytes.byteLength > MAX_APPEARANCE_BYTES)
          throw Error("CAD 표면 설정 크기가 유효하지 않습니다.");
        const compiled = appearanceFromJson(JSON.parse(
          new TextDecoder("utf-8", {fatal: true}).decode(bytes)), mesh);
        const images = new Map(), unique = new Set();
        let totalPixels = 0;
        // Load sequentially so the pixel budget is checked before decoding
        // every image in a malformed manifest at once.
        for (const [id, path] of Object.entries(compiled.textures)) {
          const asset = await this.loadTexture(path);
          images.set(id, asset);
          if (asset && !unique.has(asset)) {
            unique.add(asset);
            totalPixels += asset.width * asset.height;
            if (totalPixels > MAX_TOTAL_TEXTURE_PIXELS)
              throw Error("CAD 표면 이미지의 전체 크기가 너무 큽니다.");
          }
        }
        if (this.mesh !== mesh) return false;
        const triangles = compiled.triangles.filter((triangle) => images.get(triangle.texture))
          .map((triangle) => ({...triangle, asset: images.get(triangle.texture)}));
        if (!triangles.length) throw Error("표면 이미지를 불러오지 못했습니다.");
        this.appearance = {triangles};
        this.onStatus(this.glRenderer ? "PADO-SAT CAD · 제공 이미지 적용" :
          "PADO-SAT CAD · 표면 이미지 3D 표시 불가 · 재질색 표시");
      } catch (error) {
        this.appearance = null;
        this.onStatus(`PADO-SAT CAD · 재질색 표시 · ${error.message}`);
      }
      this.requestDraw();
      return !!this.appearance;
    }

    setDirections(directions) {
      const valid = (vector) => Array.isArray(vector) && vector.length === 3 &&
        vector.every((entry) => typeof entry === "number" && Number.isFinite(entry)) &&
        length(vector) > 1e-9;
      this.stateProvided = true;
      if (directions && typeof directions.orientationKnown === "boolean") {
        this.directions = {...directions,
          earthBody: valid(directions.earthBody) ? directions.earthBody : null,
          sunBody: valid(directions.sunBody) ? directions.sunBody : null};
        this.orientationKnown = directions.orientationKnown === true;
        this.orientationHeld = !this.orientationKnown && directions.orientationHeld === true &&
          directions.displayOrientationKnown === true;
        this.positionKnown = directions.positionKnown === true &&
          valid(directions.satelliteItrf) && !!this.directions.earthBody;
      } else {
        // Retain the explicit test-preview adapter, while null always clears
        // the last pose instead of keeping an older measurement on screen.
        this.directions = directions && valid(directions.earthBody) &&
          valid(directions.sunBody) ? directions : null;
        this.orientationKnown = !!this.directions;
        this.orientationHeld = false;
        this.positionKnown = !!this.directions;
      }
      this.requestDraw();
    }

    reset() {
      this.yaw = DEFAULT_YAW;
      this.pitch = DEFAULT_PITCH;
      this.zoom = 1;
      this.requestDraw();
    }

    viewSun() {
      if (!this.directions?.sunBody) return false;
      // Rotate only the camera. Offset the Sun from the satellite centre so
      // the foreground CAD does not cover its disc after focusing.
      const sun = unit(this.directions.sunBody), eye = sun.map(value => -value);
      const right = unit(cross(Math.abs(eye[2]) > .95 ? [0, 1, 0] : [0, 0, 1], eye));
      const up = cross(eye, right);
      const focal = Math.min(this.canvas.width, this.canvas.height) * .33 * 4.3;
      const dx = Math.min(.65, this.canvas.width * .25 / focal);
      const dy = this.canvas.height * .2 / focal;
      const shifted = unit(eye.map((value, index) => value + right[index] * dx + up[index] * dy));
      this.yaw = Math.atan2(shifted[1], shifted[0]);
      this.pitch = Math.asin(clamp(shifted[2], -1, 1));
      this.zoom = 1;
      this.requestDraw();
      return true;
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
        this.drawMesh(project, basis.eye, basis, pixels);
        this.drawBodyAxes(axes, origin);
        this.drawAxisLabels(axes, origin);
      } else {
        ctx.fillStyle = "#a9bdc7";
        ctx.font = "16px Malgun Gothic, sans-serif";
        ctx.textAlign = "center";
        ctx.fillText("CAD 형상을 불러오는 중이거나 표시할 수 없습니다", width / 2, height / 2);
      }
      if (this.stateProvided && !this.orientationKnown) {
        this.drawAttitudeNotice();
      } else if (this.stateProvided && !this.positionKnown) {
        ctx.fillStyle = "#d0dce2"; ctx.font = "13px Malgun Gothic, sans-serif";
        ctx.textAlign = "center";
        ctx.fillText("위치 정보 없음 · 자세 TM 표시", width / 2, height - 18);
      } else if (this.directions && !this.directions.sunBody) {
        ctx.fillStyle = "#9eb7c5"; ctx.font = "12px Malgun Gothic, sans-serif";
        ctx.textAlign = "center";
        ctx.fillText("태양 방향 자료 없음", width / 2, height - 18);
      }
      const sunLabels = {NO_DATA:"태양 방향 자료 없음",VISIBLE:"태양 표시 중",
        OUT_OF_VIEW:"태양 · 시야 밖",EARTH_OCCLUDED:"태양 · 지구에 가림"};
      ctx.font = "12px Malgun Gothic, sans-serif"; ctx.textAlign = "left";
      ctx.fillStyle = "#07131fe6"; ctx.fillRect(10, 10, 190, 25);
      ctx.fillStyle = "#f3d88e"; ctx.fillText(sunLabels[this.sunStatus], 18, 27);
    }

    drawAttitudeNotice() {
      const ctx = this.ctx;
      ctx.save(); ctx.setLineDash([]);
      ctx.fillStyle = "#07131fe6"; ctx.fillRect(0, this.canvas.height - 34, this.canvas.width, 34);
      ctx.fillStyle = "#dbe9ef"; ctx.font = "13px Malgun Gothic, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(this.orientationHeld ? "자세 정보 없음 · 마지막 수신 자세 유지" :
        "자세 정보 없음 · 기준 형상 표시", this.canvas.width / 2, this.canvas.height - 13);
      ctx.restore();
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

    drawMesh(project, eye, basis = null, pixels = null) {
      if (basis && this.glRenderer) {
        let layer = null;
        try {
          layer = this.glRenderer.render(basis, pixels, this.canvas.width,
            this.canvas.height, this.appearance);
        } catch (_) { /* The existing colour renderer is the safe fallback. */ }
        if (layer) {
          this.ctx.drawImage(layer, 0, 0);
          return;
        }
        try { this.glRenderer.dispose(); } catch (_) { /* Context may be lost. */ }
        this.glRenderer = null;
        if (this.appearance)
          this.onStatus("PADO-SAT CAD · 표면 이미지 3D 표시 불가 · 재질색 표시");
      }
      const {vertices, triangles, colors, normals} = this.mesh;
      const projected = vertices.map(project);
      const faces = triangles.map(([a, b, c, material], index) => ({a, b, c, material, index,
        depth: (projected[a].depth + projected[b].depth + projected[c].depth) / 3}));
      // Canvas fallback intentionally keeps flat CAD colours. Painter sorting
      // cannot reliably occlude screenshot decals on the detailed STEP mesh.
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
      this.sunStatus = "NO_DATA";
      if (!this.directions) return;
      const earth = this.directions.earthBody ? unit(this.directions.earthBody) : null;
      const sun = this.directions.sunBody ? unit(this.directions.sunBody) : null;
      const angle = this.directions.earthAngularRadiusRad;
      // The radius is computed from the FDS satellite position. A test-only
      // direction supplied without that field uses the 500 km demo altitude.
      const radius = Number.isFinite(angle) && angle > 0 && angle < Math.PI / 2 ?
        angle : Math.asin(6378137 / (6378137 + 500000));
      if (sun) {
        this.sunStatus = this.drawSunDisc(basis, pixels, sun) ? "VISIBLE" : "OUT_OF_VIEW";
        if (earth && Math.acos(clamp(dot(earth, sun), -1, 1)) + .00465 < radius)
          this.sunStatus = "EARTH_OCCLUDED";
      }
      if (earth) this.drawEarthHorizon(basis, pixels, earth, sun, radius);
    }

    drawSunDisc(basis, pixels, sun) {
      // Project a distant direction onto the same orbit-camera view as the CAD.
      // A source outside the camera hemisphere stays out of sight.
      const depth = -dot(sun, basis.eye);
      if (depth <= 0.02) return false;
      const focal = pixels * 4.3;
      const x = this.canvas.width / 2 + dot(sun, basis.right) * focal / depth;
      const y = this.canvas.height / 2 - dot(sun, basis.up) * focal / depth;
      if (x < -100 || x > this.canvas.width + 100 ||
          y < -100 || y > this.canvas.height + 100) return false;
      const ctx = this.ctx;
      const halo = ctx.createRadialGradient(x, y, 1, x, y, 88);
      halo.addColorStop(0, "rgba(255,248,205,0.96)");
      halo.addColorStop(0.09, "rgba(255,216,133,0.68)");
      halo.addColorStop(0.38, "rgba(255,193,91,0.13)");
      halo.addColorStop(1, "rgba(255,193,91,0)");
      ctx.fillStyle = halo;
      ctx.beginPath(); ctx.arc(x, y, 88, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = "#fff8da";
      const discRadius = Math.max(4, focal * 0.00465);
      ctx.beginPath(); ctx.arc(x, y, discRadius, 0, Math.PI * 2); ctx.fill();
      return x >= -discRadius && x <= this.canvas.width + discRadius &&
        y >= -discRadius && y <= this.canvas.height + discRadius;
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
          const sunlight = sun ? Math.max(0, nx * sun[0] + ny * sun[1] + nz * sun[2]) : null;
          // No Sun history means unknown lighting, rather than a fabricated
          // day/night terminator in a direction chosen by the viewer.
          const illumination = sunlight === null ? 0.72 : 0.24 + 0.76 * Math.sqrt(sunlight);
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

  return {create: (canvas, onStatus) => new Viewer(canvas, onStatus), meshFromJson,
    appearanceFromJson};
})();
