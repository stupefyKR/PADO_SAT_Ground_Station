"use strict";

// Received measurement time owns the replay axis. Reception IDs only own the
// incremental cursor, so a later downlink can fill an earlier attitude gap.
(function (global) {
  const WINDOW_MS = 72 * 3600000;
  const ATTITUDE_GAP_MS = 10000;
  const POSITION_GAP_MS = 120000;
  const MAX_RECORDS = 100000;
  const FRAMES = new Set(["BODY_TO_ITRF", "SIM_BODY_TO_ITRF", "SIM_BODY_TO_ITRF_NADIR"]);
  const finite = value => typeof value === "number" && Number.isFinite(value);
  const vector = (value, size = 3) => Array.isArray(value) && value.length === size && value.every(finite);
  const milliseconds = value => typeof value === "string" && value.endsWith("Z") ? Date.parse(value) : NaN;
  const iso = value => new Date(value).toISOString();
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
  const unit = value => {const length = Math.hypot(...value); return length > 0 ? value.map(x => x / length) : null;};
  const copy = value => value.slice();

  function brackets(rows, at) {
    let low = 0, high = rows.length;
    while (low < high) {
      const middle = (low + high) >>> 1;
      if (rows[middle].time < at) low = middle + 1; else high = middle;
    }
    if (rows[low]?.time === at) return {exact: rows[low], left: rows[low], right: rows[low]};
    return {exact: null, left: rows[low - 1] || null, right: rows[low] || null};
  }

  function slerp(left, right, fraction) {
    let a = unit(left), b = unit(right);
    if (!a || !b) return null;
    let cosine = a.reduce((sum, value, index) => sum + value * b[index], 0);
    if (cosine < 0) {b = b.map(value => -value); cosine = -cosine;}
    if (cosine > .9995) return unit(a.map((value, index) => value + fraction * (b[index] - value)));
    const angle = Math.acos(clamp(cosine, -1, 1)), denominator = Math.sin(angle);
    return a.map((value, index) => (Math.sin((1 - fraction) * angle) * value + Math.sin(fraction * angle) * b[index]) / denominator);
  }

  class Cache {
    constructor() {
      this.positions = new Map(); this.attitudes = new Map(); this.fds = [];
      this.reconstructedRows = [];
      this.currentOrbit = null; this.currentRows = []; this.cursor = 0; this.revision = 0;
      this.orbitRevision = null; this.truncated = false; this.dirty = true;
      this.mutationVersion = 0;
      this.positionRows = []; this.attitudeRows = []; this.validAttitudeRows = [];
    }
    mergeTm(page) {
      if (page?.schema === "pado.state-history.v1" && page.state === "RESET_REQUIRED") {
        this.resetTm(); return {added:0,backfilled:0,reset:true};
      }
      if (!page || page.schema !== "pado.state-history.v1" ||
          !Array.isArray(page.positions) || !Array.isArray(page.attitudes) ||
          page.positions.length + page.attitudes.length > 1000 ||
          !Number.isSafeInteger(page.next_after_id) || page.next_after_id < this.cursor ||
          !Number.isSafeInteger(page.revision) || page.revision < page.next_after_id ||
          typeof page.has_more !== "boolean") throw Error("위치·자세 이력 응답 형식 오류");
      let added = 0, backfilled = 0;
      for (const [kind, records] of [["position", page.positions], ["attitude", page.attitudes]]) {
        const target = kind === "position" ? this.positions : this.attitudes;
        for (const record of records) {
          const time = milliseconds(record.utc), received = milliseconds(record.received_utc);
          if (!Number.isSafeInteger(record.id) || record.id <= 0 || !finite(time) || !finite(received) ||
              received < time || record.id > page.revision) continue;
          const valid = record.valid !== false && (kind === "position" ?
            record.norad_id === 99999 && vector(record.position_itrf_m) &&
              Math.hypot(...record.position_itrf_m) > 6378137 && Math.hypot(...record.position_itrf_m) <= 1e8 :
            vector(record.quaternion_wxyz, 4) && Math.abs(Math.hypot(...record.quaternion_wxyz)-1) <= .02 && FRAMES.has(record.frame));
          if (target.has(time) && target.get(time).id >= record.id) continue;
          target.set(time, {...record, time, received, valid});
          added++;
          if (received - time > 120000) backfilled++;
        }
      }
      this.cursor = page.next_after_id; this.revision = page.revision;
      if (added) {this.dirty = true;this.mutationVersion++;}
      return {added, backfilled};
    }
    resetTm() {
      this.positions.clear(); this.attitudes.clear(); this.cursor=0; this.revision=0;this.dirty=true;this.mutationVersion++;
    }
    setOrbit(page) {
      if (!page || page.schema !== "pado.orbit-history.v1" || page.norad_id !== 99999 ||
          !Array.isArray(page.samples) || page.samples.length > 5005) throw Error("FDS 이력 응답 형식 오류");
      this.fds = page.samples.filter(row => Array.isArray(row) && row.length === 9 &&
        finite(milliseconds(row[0])) && vector(row.slice(1, 4)) &&
        Math.hypot(...row.slice(1,4))>6378137 && Math.hypot(...row.slice(1,4))<=1e8 &&
        vector(row.slice(4, 7)) && Math.abs(Math.hypot(...row.slice(4,7))-1)<.1 &&
        ["TLE", "GNSS"].includes(row[7]) && finite(milliseconds(row[8])) &&
        milliseconds(row[0]) >= milliseconds(row[8]) && milliseconds(row[0]) - milliseconds(row[8]) < WINDOW_MS)
        .map(row => ({time: milliseconds(row[0]), position: row.slice(1, 4), sun: row.slice(4, 7),
          source: row[7], epoch: row[8]})).sort((a, b) => a.time - b.time);
      this.orbitRevision = page.revision;
      const replay = page.reconstruction;
      const epoch = milliseconds(replay?.input_epoch_utc);
      const start = milliseconds(replay?.start_utc), end = milliseconds(replay?.end_utc);
      this.reconstructedRows = replay?.schema === "pado.orbit-reconstruction.v1" &&
        replay.quality === "RECONSTRUCTED_NOT_MEASURED" && replay.norad_id === 99999 &&
        replay.source === "TLE" && finite(epoch) && finite(start) && finite(end) &&
        start < end && end-start <= WINDOW_MS && Array.isArray(replay.samples) &&
        replay.samples.length <= 4323 ? replay.samples.filter(row => Array.isArray(row) &&
          row.length === 7 && finite(milliseconds(row[0])) &&
          milliseconds(row[0]) >= start && milliseconds(row[0]) <= end &&
          Math.abs(milliseconds(row[0])-epoch) <= WINDOW_MS+1000 &&
          vector(row.slice(1,4)) && Math.hypot(...row.slice(1,4))>6378137 &&
          Math.hypot(...row.slice(1,4))<=8e6 && vector(row.slice(4,7)) &&
          Math.abs(Math.hypot(...row.slice(4,7))-1)<.1)
          .map(row=>({time:milliseconds(row[0]),position:row.slice(1,4),sun:row.slice(4,7),
            source:"RECONSTRUCTED_TLE",epoch:replay.input_epoch_utc}))
          .sort((a,b)=>a.time-b.time) : [];
    }
    setCurrentOrbit(view) {
      this.currentOrbit = view?.schema === "fds.orbit-view.v1" && view.norad_id === 99999 &&
        Array.isArray(view.samples) && view.samples.length <= 5000 ? view : null;
      this.currentRows=this.currentOrbit ? view.samples.filter(row=>Array.isArray(row) && row.length===10 &&
        finite(milliseconds(row[0])) && vector(row.slice(1,4)) &&
        Math.hypot(...row.slice(1,4))>6378137 && Math.hypot(...row.slice(1,4))<=1e8 &&
        vector(row.slice(7,10)) && Math.abs(Math.hypot(...row.slice(7,10))-1)<.1)
        .map(row=>({time:milliseconds(row[0]),position:row.slice(1,4),sun:row.slice(7,10),
          source:view.source,epoch:view.input_epoch_utc})).sort((a,b)=>a.time-b.time) : [];
    }
    prune(now) {
      const cutoff = now - WINDOW_MS;
      for (const target of [this.positions, this.attitudes]) {
        for (const [time] of target) if (time < cutoff || time > now + 30000) {target.delete(time); this.dirty = true; this.mutationVersion++;}
        if (target.size > MAX_RECORDS) {
          const oldest = [...target.keys()].sort((a, b) => a - b).slice(0, target.size - MAX_RECORDS);
          oldest.forEach(time => target.delete(time)); this.dirty = true; this.truncated = true;
        }
      }
      const previous = this.fds.filter(row => row.time < cutoff).at(-1);
      const next = this.fds.find(row => row.time > now);
      this.fds = [previous, ...this.fds.filter(row => row.time >= cutoff && row.time <= now), next].filter(Boolean);
    }
    sorted() {
      if (this.dirty) {
        this.positionRows = [...this.positions.values()].sort((a, b) => a.time - b.time);
        this.attitudeRows = [...this.attitudes.values()].sort((a, b) => a.time - b.time);
        this.validAttitudeRows = this.attitudeRows.filter(row=>row.valid);
        this.dirty = false;
      }
    }
    orbitAt(at) {
      const result = (rows, reconstructed = false) => {
        const pair = brackets(rows, at);
        if (pair.exact) return {...pair.exact, position: copy(pair.exact.position), sun: unit(pair.exact.sun)};
        const {left, right} = pair;
        if (!left || !right || right.time - left.time > POSITION_GAP_MS ||
            (!reconstructed && (at < milliseconds(left.epoch) || at < milliseconds(right.epoch))) ||
            Math.hypot(...right.position.map((value,index)=>value-left.position[index])) /
              ((right.time-left.time)/1000) > 15000) return null;
        const fraction = (at - left.time) / (right.time - left.time);
        return {time: at, position: left.position.map((value, index) => value + fraction * (right.position[index] - value)),
          sun: unit(left.sun.map((value, index) => value + fraction * (right.sun[index] - value))),
          source: left.source === right.source ? left.source : "MIXED", epoch: left.epoch};
      };
      const historical = result(this.fds);
      if (historical) return historical;
      const view = this.currentOrbit;
      if (view && ["TLE", "GNSS"].includes(view.source) && finite(milliseconds(view.input_epoch_utc)) &&
          at >= milliseconds(view.input_epoch_utc) && at - milliseconds(view.input_epoch_utc) < WINDOW_MS) {
        const current = result(this.currentRows);
        if (current) return current;
      }
      // A separate FDS product explicitly reconstructs earlier positions from
      // TLE. It does not turn newer GNSS inputs into historical measurements.
      return result(this.reconstructedRows, true);
    }
    resolve(at, {live = false} = {}) {
      this.sorted();
      const predicted = this.orbitAt(at), pair = brackets(this.positionRows, at);
      let position = null, positionSource = "UNKNOWN", positionUtc = null, positionReceivedUtc = null;
      if (pair.exact?.valid) {
        position = copy(pair.exact.position_itrf_m); positionSource = "GNSS_TM";
        positionUtc = pair.exact.utc; positionReceivedUtc = pair.exact.received_utc;
      } else if (pair.left?.valid && pair.right?.valid && pair.right.time - pair.left.time <= POSITION_GAP_MS &&
          Math.hypot(...pair.right.position_itrf_m.map((value,index)=>value-pair.left.position_itrf_m[index])) /
            ((pair.right.time-pair.left.time)/1000) <= 15000) {
        const a = pair.left, b = pair.right, fraction = (at - a.time) / (b.time - a.time), dt = (b.time - a.time) / 1000;
        if (vector(a.velocity_itrf_mps) && vector(b.velocity_itrf_mps) &&
            Math.hypot(...a.velocity_itrf_mps)<=15000 && Math.hypot(...b.velocity_itrf_mps)<=15000) {
          const f2 = fraction * fraction, f3 = f2 * fraction;
          position = a.position_itrf_m.map((value, index) => (2*f3-3*f2+1)*value +
            (f3-2*f2+fraction)*dt*a.velocity_itrf_mps[index] + (-2*f3+3*f2)*b.position_itrf_m[index] +
            (f3-f2)*dt*b.velocity_itrf_mps[index]);
        } else position = a.position_itrf_m.map((value, index) => value + fraction * (b.position_itrf_m[index] - value));
        positionSource = "GNSS_INTERPOLATED"; positionUtc = iso(at);
        positionReceivedUtc = a.received > b.received ? a.received_utc : b.received_utc;
      } else if (predicted) {position = predicted.position; positionSource = `FDS_${predicted.source}`; positionUtc = iso(at);}
      if (position && (Math.hypot(...position)<=6378137 || Math.hypot(...position)>1e8)) {
        position=predicted?.position || null;positionSource=predicted ? `FDS_${predicted.source}` : "UNKNOWN";
        positionUtc=predicted ? iso(at) : null;positionReceivedUtc=null;
      }
      const q = brackets(this.attitudeRows, at);
      let attitude = null, attitudeStatus = "UNKNOWN";
      if (q.exact?.valid) {attitude = {...q.exact}; attitudeStatus = "MEASURED";}
      else if (q.left?.valid && q.right?.valid && q.right.time-q.left.time <= ATTITUDE_GAP_MS && q.left.frame === q.right.frame) {
        attitude = {utc: iso(at), frame: q.left.frame,
          quaternion_wxyz: slerp(q.left.quaternion_wxyz, q.right.quaternion_wxyz, (at-q.left.time)/(q.right.time-q.left.time)),
          received_utc: q.left.received > q.right.received ? q.left.received_utc : q.right.received_utc,
          bracket_utc: [q.left.utc, q.right.utc]}; attitudeStatus = "INTERPOLATED";
      } else if (live && q.left?.valid && at >= q.left.time && at-q.left.time <= ATTITUDE_GAP_MS) {
        attitude = {...q.left}; attitudeStatus = "RECENT_SAMPLE";
      }
      // Keep a recognizable CAD pose during a TM gap without asserting that
      // the held orientation is known at the selected measurement time.
      const lastValid = attitude ? null : brackets(this.validAttitudeRows, at);
      const held = lastValid?.exact || lastValid?.left || null;
      const displayAttitude = attitude || (held ? {...held,
        quaternion_wxyz: copy(held.quaternion_wxyz)} : null);
      const displayAttitudeStatus = attitude ? attitudeStatus : held ? "LAST_KNOWN" : "UNKNOWN";
      return {utc: iso(at), position_itrf_m: position, sun_itrf: predicted?.sun || null,
        positionSource, positionUtc, positionReceivedUtc, attitude, attitudeStatus,
        displayAttitude, displayAttitudeStatus,
        attitudeReason: q.exact && !q.exact.valid ? q.exact.reason || "invalid_attitude" : null};
    }
    coverage() {
      this.sorted();
      const segments = []; let last = null;
      for (const sample of this.attitudeRows) {
        if (!sample.valid) {last=null;continue;}
        if (last && sample.time - last[1] <= ATTITUDE_GAP_MS) last[1] = sample.time;
        else {last=[sample.time,sample.time];segments.push(last);}
      }
      return segments;
    }
    neighbors(at) {
      this.sorted();
      const pair=brackets(this.validAttitudeRows,at);
      if (pair.exact) {
        const index=this.validAttitudeRows.indexOf(pair.exact);
        return {previous:this.validAttitudeRows[index-1]?.time ?? null,next:this.validAttitudeRows[index+1]?.time ?? null};
      }
      return {previous:pair.left?.time ?? null,next:pair.right?.time ?? null};
    }
  }

  class Controller {
    constructor({api, onChange = () => {}, onStatus = () => {}, now = () => Date.now(), cache = new Cache()} = {}) {
      this.api = api; this.onChange = onChange; this.onStatus = onStatus; this.clock = now; this.cache = cache;
      this.offset = 0; this.live = true; this.playing = false; this.speed = 60; this.cursor = this.now();
      this.lastTick = this.now(); this.pending = null; this.lastFds = -Infinity; this.lastPrune = -Infinity;
      this.loading = false; this.morePending = false; this.error = null; this.backfilled = 0; this.lastRefresh = null;
    }
    now() {return this.clock() + this.offset;}
    state() {
      const end = this.now(), start = end - WINDOW_MS;
      const neighbors=this.cache.neighbors(clamp(this.cursor,start,end));
      return {...this.cache.resolve(clamp(this.cursor, start, end), {live:this.live}),
        live:this.live, playing:this.playing, speed:this.speed, startMs:start, endMs:end,
        selectedMs:clamp(this.cursor,start,end), loading:this.loading || this.morePending, error:this.error,
        revision:this.cache.revision, cacheRevision:this.cache.mutationVersion, backfilled:this.backfilled, lastRefresh:this.lastRefresh,
        truncated:this.cache.truncated,previousSampleMs:neighbors.previous,nextSampleMs:neighbors.next};
    }
    emit() {this.onChange(this.state());}
    seek(at) {if (!finite(at)) return; this.live=false; this.playing=false; this.cursor=clamp(at,this.now()-WINDOW_MS,this.now()); this.lastTick=this.now(); this.emit();}
    goLive() {this.live=true; this.playing=false; this.cursor=this.now(); this.lastTick=this.now(); this.emit();}
    togglePlay() {if (this.live) {this.live=false; this.cursor=this.now()-60000;} this.playing=!this.playing; this.lastTick=this.now(); this.emit();}
    setSpeed(value) {if ([1,10,60,300].includes(Number(value))) {this.speed=Number(value); this.emit();}}
    stepSample(direction) {const adjacent=this.cache.neighbors(this.cursor);const at=direction<0?adjacent.previous:adjacent.next;if(at!==null)this.seek(at);}
    tick() {
      const now=this.now(), elapsed=clamp(now-this.lastTick,0,2000); this.lastTick=now;
      if (this.live) this.cursor=now;
      else if (this.playing) {this.cursor+=elapsed*this.speed; if (this.cursor>=now) {this.live=true;this.playing=false;this.cursor=now;}}
      this.cursor=clamp(this.cursor,now-WINDOW_MS,now);
      if (now-this.lastPrune>=60000) {this.cache.prune(now);this.lastPrune=now;}
      this.emit();
    }
    async refresh() {
      if (this.pending) return this.pending;
      this.loading=true; this.error=null; this.emit();
      const run=async()=>{
        const end=Math.floor(this.now()/1000),start=end-WINDOW_MS/1000;
        const errors=[];
        const tm=async()=>{
          try {
            for (let page=0;page<128;page++) {
              const previous=this.cache.cursor;
              const response=await this.api(`/api/ros/state-history?start_utc_s=${start}&end_utc_s=${end}&after_id=${previous}&limit=1000&follow=1`);
              const merged=this.cache.mergeTm(response); this.backfilled+=merged.backfilled;
              if (merged.reset) {this.emit();continue;}
              this.morePending=response.has_more;
              if (Number.isSafeInteger(response.server_utc_s)) this.offset=response.server_utc_s*1000-this.clock();
              this.cache.prune(this.now()); this.emit();
              if (!response.has_more) break;
              if (this.cache.cursor<=previous) throw Error("이력 페이지가 진행되지 않습니다");
            }
          } catch (_) {errors.push("수신 TM 이력 연결 대기");}
        };
        const fds=async()=>{
          if (this.now()-this.lastFds<60000) return;
          try {
            this.cache.setOrbit(await this.api(`/api/fds/orbit-history?target=synthetic&start_utc_s=${start}&end_utc_s=${end}`));
            this.lastFds=this.now();
          } catch (_) {errors.push("FDS 위치 이력 연결 대기");}
        };
        await Promise.all([tm(),fds()]);
        this.error=errors.join(" · ") || null; this.lastRefresh=iso(this.now());
        this.onStatus(this.error || "위치·자세 이력 갱신");
      };
      this.pending=run().finally(()=>{this.loading=false;this.pending=null;this.emit();});
      return this.pending;
    }
  }
  global.rosStateHistory=Object.freeze({Cache,Controller,slerp,WINDOW_MS,ATTITUDE_GAP_MS,POSITION_GAP_MS});
})(globalThis);
