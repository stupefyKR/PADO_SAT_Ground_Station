"use strict";

// The simulator publishes body->ITRF quaternions. FDS publishes ITRF satellite
// positions and Sun unit directions in the same orbit-view sample. Keep the CAD
// fixed in body axes and rotate Earth/Sun vectors into that body frame.
(function (global) {
  const FRAME = "SIM_BODY_TO_ITRF_NADIR";
  const MAX_SAMPLE_GAP_MS = 120_000;
  const MAX_INPUT_AGE_MS = 72 * 3_600_000;

  function finiteVector(values) {
    return Array.isArray(values) && values.length === 3 &&
      values.every((value) => typeof value === "number" && Number.isFinite(value));
  }

  function norm(vector) {
    return Math.hypot(vector[0], vector[1], vector[2]);
  }

  function unit(vector) {
    const length = norm(vector);
    return Number.isFinite(length) && length > 0 ? vector.map((value) => value / length) : null;
  }

  function cross(a, b) {
    return [a[1] * b[2] - a[2] * b[1],
      a[2] * b[0] - a[0] * b[2],
      a[0] * b[1] - a[1] * b[0]];
  }

  function bodyVector(itrf, quaternion) {
    const length = Math.hypot(...quaternion);
    if (!Number.isFinite(length) || Math.abs(length - 1) > 0.02) return null;
    const [w, x, y, z] = quaternion.map((value) => value / length);
    // Conjugate of body->ITRF maps an ITRF direction into body axes.
    const q = [-x, -y, -z];
    const doubled = cross(q, itrf).map((value) => 2 * value);
    const remainder = cross(q, doubled);
    return unit(itrf.map((value, axis) => value + w * doubled[axis] + remainder[axis]));
  }

  function sampleAt(view, utcMs) {
    const rows = view.samples;
    if (!Array.isArray(rows) || rows.length < 2 || rows.length > 5_000 ||
        !Number.isInteger(view.sample_step_s) || view.sample_step_s < 1 ||
        view.sample_step_s > MAX_SAMPLE_GAP_MS / 1000) return null;
    const first = Date.parse(rows[0]?.[0]);
    const last = Date.parse(rows.at(-1)?.[0]);
    if (!Number.isFinite(first) || !Number.isFinite(last) ||
        utcMs < first || utcMs > last) return null;
    let low = 0, high = rows.length - 1;
    while (high - low > 1) {
      const mid = (low + high) >>> 1;
      if (Date.parse(rows[mid]?.[0]) <= utcMs) low = mid;
      else high = mid;
    }
    const a = rows[low], b = rows[high];
    const ta = Date.parse(a?.[0]), tb = Date.parse(b?.[0]);
    if (!Number.isFinite(ta) || !Number.isFinite(tb) ||
        !(ta < tb) || tb - ta > view.sample_step_s * 1000 + 1 ||
        !Array.isArray(a) || !Array.isArray(b) || a.length !== 10 || b.length !== 10 ||
        !finiteVector(a.slice(1, 4)) || !finiteVector(b.slice(1, 4)) ||
        !finiteVector(a.slice(7, 10)) || !finiteVector(b.slice(7, 10))) return null;
    const fraction = (utcMs - ta) / (tb - ta);
    return {position: [1, 2, 3].map((index) =>
      a[index] + fraction * (b[index] - a[index])),
    sun: [7, 8, 9].map((index) => a[index] + fraction * (b[index] - a[index]))};
  }

  function compute({attitude, orbitView} = {}) {
    if (!attitude || attitude.frame !== FRAME ||
        !Array.isArray(attitude.quaternion_wxyz) ||
        attitude.quaternion_wxyz.length !== 4 ||
        !attitude.quaternion_wxyz.every((value) =>
          typeof value === "number" && Number.isFinite(value)) ||
        !orbitView || orbitView.schema !== "fds.orbit-view.v1" ||
        orbitView.quality !== "PRELIMINARY_NOT_FOR_OPERATIONS" ||
        orbitView.norad_id !== 99999 ||
        !["TLE", "GNSS"].includes(orbitView.source)) return null;
    const utcMs = Date.parse(attitude.utc);
    const epochMs = Date.parse(orbitView.input_epoch_utc);
    const startMs = Date.parse(orbitView.start_utc);
    const endMs = Date.parse(orbitView.end_utc);
    if (![utcMs, epochMs, startMs, endMs].every(Number.isFinite) ||
        utcMs < startMs || utcMs > endMs ||
        utcMs < epochMs || utcMs - epochMs >= MAX_INPUT_AGE_MS) return null;
    const sample = sampleAt(orbitView, utcMs);
    if (!sample || norm(sample.position) < 6.3e6 || norm(sample.position) > 8e6 ||
        norm(sample.sun) < 0.9 || norm(sample.sun) > 1.1) return null;
    const earthItrf = unit(sample.position.map((value) => -value));
    const sunItrf = unit(sample.sun);
    const earthBody = bodyVector(earthItrf, attitude.quaternion_wxyz);
    const sunBody = bodyVector(sunItrf, attitude.quaternion_wxyz);
    if (!earthBody || !sunBody) return null;
    return {utc: attitude.utc, earthBody, sunBody, earthItrf, sunItrf,
      earthAlignment: earthBody[2]};
  }

  global.rosAttitudeMath = Object.freeze({compute});
})(globalThis);
