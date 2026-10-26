"use strict";

// The simulator publishes body->ITRF quaternions. FDS publishes ITRF satellite
// positions and Sun unit directions in the same orbit-view sample. Keep the CAD
// fixed in body axes and rotate Earth/Sun vectors into that body frame.
(function (global) {
  const FRAME = "SIM_BODY_TO_ITRF_NADIR";
  const MAX_SAMPLE_GAP_MS = 120_000;
  const MAX_INPUT_AGE_MS = 72 * 3_600_000;
  // A spherical display model; the trajectory and attitude remain FDS/TM data.
  const EARTH_DISPLAY_RADIUS_M = 6_378_137;

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

  function dot(a, b) {
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
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

  function bodyAxesItrf(quaternion) {
    const qLength = Math.hypot(...quaternion);
    if (!Number.isFinite(qLength) || Math.abs(qLength - 1) > 0.02) return null;
    const [w, x, y, z] = quaternion.map((value) => value / qLength);
    const vector = [x, y, z];
    return [[1, 0, 0], [0, 1, 0], [0, 0, 1]].map((axis) => {
      const doubled = cross(vector, axis).map((value) => 2 * value);
      const remainder = cross(vector, doubled);
      return axis.map((value, index) => value + w * doubled[index] + remainder[index]);
    });
  }

  function earthAngularRadius(position) {
    const distance = norm(position);
    return distance > EARTH_DISPLAY_RADIUS_M ?
      Math.asin(EARTH_DISPLAY_RADIUS_M / distance) : null;
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

  function eligibleViewAt(view, utcMs) {
    if (!view || view.schema !== "fds.orbit-view.v1" ||
        view.quality !== "PRELIMINARY_NOT_FOR_OPERATIONS" ||
        view.norad_id !== 99999 || !["TLE", "GNSS"].includes(view.source) ||
        !Number.isFinite(utcMs)) return null;
    const epochMs = Date.parse(view.input_epoch_utc);
    const startMs = Date.parse(view.start_utc);
    const endMs = Date.parse(view.end_utc);
    if (![epochMs, startMs, endMs].every(Number.isFinite) ||
        !(startMs < endMs) || utcMs < startMs || utcMs > endMs ||
        utcMs < epochMs || utcMs - epochMs >= MAX_INPUT_AGE_MS) return null;
    return {startMs, endMs};
  }

  function compute({attitude, orbitView} = {}) {
    if (!attitude || attitude.frame !== FRAME ||
        !Array.isArray(attitude.quaternion_wxyz) ||
        attitude.quaternion_wxyz.length !== 4 ||
        !attitude.quaternion_wxyz.every((value) =>
          typeof value === "number" && Number.isFinite(value))) return null;
    const utcMs = Date.parse(attitude.utc);
    if (!eligibleViewAt(orbitView, utcMs)) return null;
    const sample = sampleAt(orbitView, utcMs);
    if (!sample || norm(sample.position) <= EARTH_DISPLAY_RADIUS_M ||
        norm(sample.position) > 8e6 ||
        norm(sample.sun) < 0.9 || norm(sample.sun) > 1.1) return null;
    const earthItrf = unit(sample.position.map((value) => -value));
    const sunItrf = unit(sample.sun);
    const earthBody = bodyVector(earthItrf, attitude.quaternion_wxyz);
    const sunBody = bodyVector(sunItrf, attitude.quaternion_wxyz);
    const bodyToItrf = bodyAxesItrf(attitude.quaternion_wxyz);
    if (!earthBody || !sunBody || !bodyToItrf) return null;
    return {utc: attitude.utc, earthBody, sunBody, earthItrf, sunItrf,
      bodyToItrf, satelliteItrf: sample.position,
      earthAngularRadiusRad: earthAngularRadius(sample.position),
      earthAlignment: -earthBody[0]};
  }

  function predictNadir({orbitView, utc} = {}) {
    // TEST_ONLY: this is the simulator's expected nadir mode, not received TM.
    // Reuse FDS ITRF positions and Sun vectors; no separate orbit propagation.
    const utcMs = Date.parse(utc);
    const coverage = eligibleViewAt(orbitView, utcMs);
    if (!coverage) return null;
    const sample = sampleAt(orbitView, utcMs);
    if (!sample || norm(sample.position) <= EARTH_DISPLAY_RADIUS_M ||
        norm(sample.position) > 8e6 || norm(sample.sun) < 0.9 ||
        norm(sample.sun) > 1.1) return null;
    const earthItrf = unit(sample.position.map((value) => -value));
    const sunItrf = unit(sample.sun);
    // The simulator uses body -X nadir and body +Z along-track. A central
    // difference of nearby FDS positions estimates its ITRF velocity; at a
    // coverage edge use the available one-sided interval.
    const halfStepMs = Math.min(30_000, orbitView.sample_step_s * 500);
    const beforeMs = Math.max(coverage.startMs, utcMs - halfStepMs);
    const afterMs = Math.min(coverage.endMs, utcMs + halfStepMs);
    if (!(beforeMs < afterMs)) return null;
    const before = sampleAt(orbitView, beforeMs);
    const after = sampleAt(orbitView, afterMs);
    if (!before || !after) return null;
    const velocity = before.position.map((value, axis) =>
      (after.position[axis] - value) / ((afterMs - beforeMs) / 1000));
    const x = earthItrf.map((value) => -value);
    const z = unit(velocity.map((value, axis) =>
      value - dot(velocity, x) * x[axis]));
    if (!z) return null;
    const y = unit(cross(z, x));
    if (!y) return null;
    const sunBody = unit([dot(sunItrf, x), dot(sunItrf, y),
      dot(sunItrf, z)]);
    if (!sunBody) return null;
    return {utc: new Date(utcMs).toISOString(), earthBody: [-1, 0, 0],
      sunBody, earthItrf, sunItrf, bodyToItrf: [x, y, z],
      satelliteItrf: sample.position,
      earthAngularRadiusRad: earthAngularRadius(sample.position),
      earthAlignment: 1,
      provenance: "FDS_TEST_NADIR_PREDICTION"};
  }

  global.rosAttitudeMath = Object.freeze({compute, predictNadir});
})(globalThis);
