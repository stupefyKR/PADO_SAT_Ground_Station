"use strict";

// A transparent, depth-buffered CAD layer. The surrounding sky, labels and
// mouse controls remain in the 2D viewer; no external graphics library is used.
window.rosAttitudeGl = (() => {
  const MAX_TEXTURE_TRIANGLES = 512;
  const VERTEX = `
    attribute vec3 a_position;
    attribute vec3 a_normal;
    attribute vec3 a_color;
    attribute vec2 a_uv;
    uniform mediump vec3 u_eye;
    uniform vec3 u_right;
    uniform vec3 u_up;
    uniform vec4 u_projection;
    varying mediump vec3 v_normal;
    varying mediump vec3 v_color;
    varying mediump vec2 v_uv;
    void main() {
      float viewDepth = 4.3 - dot(a_position, u_eye);
      gl_Position = vec4(dot(a_position, u_right) * u_projection.x,
        dot(a_position, u_up) * u_projection.y,
        u_projection.z * viewDepth + u_projection.w, viewDepth);
      v_normal = a_normal;
      v_color = a_color;
      v_uv = a_uv;
    }
  `;
  const FRAGMENT = `
    precision mediump float;
    uniform mediump vec3 u_eye;
    uniform bool u_textured;
    uniform sampler2D u_image;
    varying mediump vec3 v_normal;
    varying mediump vec3 v_color;
    varying mediump vec2 v_uv;
    void main() {
      if (u_textured) {
        if (dot(v_normal, u_eye) <= 0.0001) discard;
        vec4 pixel = texture2D(u_image, v_uv);
        if (pixel.a <= 0.01) discard;
        gl_FragColor = pixel;
      } else {
        float magnitude = length(v_normal);
        float angle = magnitude > 0.000001 ?
          abs(dot(v_normal / magnitude, u_eye)) : 0.0;
        float shade = 0.55 + 0.38 * angle;
        gl_FragColor = vec4(clamp(v_color * shade + vec3(20.0),
          vec3(24.0), vec3(245.0)) / 255.0, 1.0);
      }
    }
  `;
  const finiteVector = (values) => Array.isArray(values) && values.length === 3 &&
    values.every((value) => typeof value === "number" && Number.isFinite(value));

  class Renderer {
    constructor(gl, canvas) {
      this.gl = gl;
      this.canvas = canvas;
      this.buffers = new Set();
      this.textures = new Set();
      this.shaders = new Set();
      this.program = null;
      this.groups = [];
      this.appearance = null;
      this.lost = false;
      canvas.addEventListener?.("webglcontextlost", (event) => {
        event.preventDefault();
        this.lost = true;
      });
    }

    shader(kind, source) {
      const gl = this.gl, shader = gl.createShader(kind);
      if (!shader) throw Error("shader unavailable");
      this.shaders.add(shader);
      gl.shaderSource(shader, source); gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw Error("shader compile failed");
      return shader;
    }

    buffer(data) {
      const gl = this.gl, buffer = gl.createBuffer();
      if (!buffer) throw Error("buffer unavailable");
      this.buffers.add(buffer);
      gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(data), gl.STATIC_DRAW);
      return buffer;
    }

    initialize(mesh) {
      if (!mesh || !Array.isArray(mesh.vertices) || mesh.vertices.length > 40000 ||
          !Array.isArray(mesh.triangles) || !mesh.triangles.length || mesh.triangles.length > 25000 ||
          !Array.isArray(mesh.normals) || !Array.isArray(mesh.colors)) throw Error("invalid mesh");
      const gl = this.gl;
      const program = gl.createProgram();
      if (!program) throw Error("program unavailable");
      this.program = program;
      gl.attachShader(program, this.shader(gl.VERTEX_SHADER, VERTEX));
      gl.attachShader(program, this.shader(gl.FRAGMENT_SHADER, FRAGMENT));
      gl.linkProgram(program);
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw Error("program link failed");
      this.attributes = Object.fromEntries(["position", "normal", "color", "uv"]
        .map((name) => [name, gl.getAttribLocation(program, `a_${name}`)]));
      this.uniforms = Object.fromEntries(["eye", "right", "up", "projection", "textured", "image"]
        .map((name) => [name, gl.getUniformLocation(program, `u_${name}`)]));
      const data = [];
      mesh.triangles.forEach(([a, b, c, material], index) => {
        const normal = mesh.normals[index] || [0, 0, 0], color = mesh.colors[material];
        if (!finiteVector(normal) || !finiteVector(color)) throw Error("invalid mesh attributes");
        for (const id of [a, b, c]) {
          const position = mesh.vertices[id];
          if (!finiteVector(position)) throw Error("invalid mesh vertex");
          data.push(...position, ...normal, ...color, 0, 0);
        }
      });
      this.base = {buffer: this.buffer(data), count: mesh.triangles.length * 3};
    }

    clearAppearance() {
      const gl = this.gl;
      for (const group of this.groups) {
        gl.deleteBuffer(group.buffer); this.buffers.delete(group.buffer);
      }
      for (const texture of this.textures) gl.deleteTexture(texture);
      this.textures.clear(); this.groups = []; this.appearance = null;
    }

    setAppearance(appearance) {
      if (appearance === this.appearance) return;
      this.clearAppearance();
      if (!appearance) return;
      if (!Array.isArray(appearance.triangles) ||
          appearance.triangles.length > MAX_TEXTURE_TRIANGLES) throw Error("invalid appearance");
      const grouped = new Map(), gl = this.gl;
      for (const triangle of appearance.triangles) {
        const asset = triangle.asset;
        if (!asset?.image || !Number.isInteger(asset.width) || !Number.isInteger(asset.height) ||
            asset.width < 1 || asset.height < 1 || asset.width > 4096 || asset.height > 4096 ||
            !finiteVector(triangle.normal) || !Array.isArray(triangle.vertices) ||
            triangle.vertices.length !== 3) throw Error("invalid texture triangle");
        if (!grouped.has(asset.image)) grouped.set(asset.image, {asset, data: []});
        const data = grouped.get(asset.image).data;
        for (const vertex of triangle.vertices) {
          if (!finiteVector(vertex.position) || !Array.isArray(vertex.uv) || vertex.uv.length !== 2 ||
              !vertex.uv.every((value) => Number.isFinite(value) && value >= 0 && value <= 1))
            throw Error("invalid texture vertex");
          data.push(...vertex.position, ...triangle.normal, 0, 0, 0, ...vertex.uv);
        }
      }
      const maximum = gl.getParameter(gl.MAX_TEXTURE_SIZE);
      for (const {asset, data} of grouped.values()) {
        if (asset.width > maximum || asset.height > maximum) throw Error("texture size unsupported");
        const texture = gl.createTexture();
        if (!texture) throw Error("texture unavailable");
        this.textures.add(texture);
        gl.bindTexture(gl.TEXTURE_2D, texture);
        // Source UVs use the image's top-left origin. Do not flip them again.
        gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
        gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, asset.image);
        if (gl.getError && gl.getError() !== gl.NO_ERROR) throw Error("texture upload failed");
        this.groups.push({buffer: this.buffer(data), count: data.length / 11, texture});
      }
      this.appearance = appearance;
    }

    bind(buffer) {
      const gl = this.gl;
      gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
      const stride = 11 * 4;
      for (const [name, size, offset] of [["position", 3, 0], ["normal", 3, 12],
        ["color", 3, 24], ["uv", 2, 36]]) {
        const location = this.attributes[name];
        if (location < 0) continue;
        gl.enableVertexAttribArray(location);
        gl.vertexAttribPointer(location, size, gl.FLOAT, false, stride, offset);
      }
    }

    render(basis, pixels, width, height, appearance) {
      if (this.lost || !this.program || !finiteVector(basis?.eye) ||
          !finiteVector(basis?.right) || !finiteVector(basis?.up) ||
          !Number.isFinite(pixels) || pixels <= 0 || !Number.isInteger(width) ||
          !Number.isInteger(height) || width < 1 || height < 1 || width > 4096 || height > 4096)
        return null;
      const gl = this.gl;
      try {
        if (gl.isContextLost?.()) { this.lost = true; return null; }
        this.setAppearance(appearance);
        if (this.canvas.width !== width) this.canvas.width = width;
        if (this.canvas.height !== height) this.canvas.height = height;
        gl.viewport(0, 0, width, height);
        gl.clearColor(0, 0, 0, 0); gl.clearDepth(1);
        gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
        gl.enable(gl.DEPTH_TEST); gl.depthFunc(gl.LEQUAL); gl.depthMask(true);
        gl.disable(gl.CULL_FACE); gl.disable(gl.BLEND);
        gl.useProgram(this.program);
        gl.uniform3fv(this.uniforms.eye, basis.eye);
        gl.uniform3fv(this.uniforms.right, basis.right);
        gl.uniform3fv(this.uniforms.up, basis.up);
        const near = 0.1, far = 20;
        gl.uniform4f(this.uniforms.projection, 2 * pixels * 4.3 / width,
          2 * pixels * 4.3 / height, (far + near) / (far - near), -2 * far * near / (far - near));
        gl.uniform1i(this.uniforms.textured, 0);
        this.bind(this.base.buffer);
        gl.drawArrays(gl.TRIANGLES, 0, this.base.count);
        if (this.groups.length) {
          gl.enable(gl.BLEND);
          gl.blendFuncSeparate(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA, gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
          gl.enable(gl.POLYGON_OFFSET_FILL); gl.polygonOffset(-1, -1);
          gl.activeTexture(gl.TEXTURE0);
          gl.uniform1i(this.uniforms.image, 0); gl.uniform1i(this.uniforms.textured, 1);
          for (const group of this.groups) {
            gl.bindTexture(gl.TEXTURE_2D, group.texture); this.bind(group.buffer);
            gl.drawArrays(gl.TRIANGLES, 0, group.count);
          }
          gl.disable(gl.POLYGON_OFFSET_FILL);
        }
        gl.flush();
        return this.canvas;
      } catch (_) {
        this.lost = true;
        return null;
      }
    }

    dispose() {
      const gl = this.gl;
      for (const buffer of this.buffers) gl.deleteBuffer(buffer);
      for (const texture of this.textures) gl.deleteTexture(texture);
      for (const shader of this.shaders) gl.deleteShader(shader);
      if (this.program) gl.deleteProgram(this.program);
      this.buffers.clear(); this.textures.clear(); this.shaders.clear();
      this.groups = []; this.program = null; this.lost = true;
    }
  }

  function create(mesh) {
    if (typeof document === "undefined") return null;
    let renderer = null;
    try {
      const canvas = document.createElement("canvas");
      const gl = canvas.getContext("webgl", {alpha: true, depth: true, antialias: true,
        premultipliedAlpha: true, preserveDrawingBuffer: true});
      if (!gl) return null;
      const attributes = gl.getContextAttributes?.();
      if (attributes && attributes.depth !== true) return null;
      renderer = new Renderer(gl, canvas);
      renderer.initialize(mesh);
      return renderer;
    } catch (_) {
      renderer?.dispose();
      return null;
    }
  }

  return Object.freeze({create});
})();
