"""Exercise authenticated MPS planning through a scheduled satellite TM dump.

Only the FDS schedule provider is replaced by a short, explicit test PASS.
HTTP handlers, saved drafts, handoff checks, reservation scheduler, Unix bridge,
packet codec, satellite dispatcher, and ROS/N11 publication are real code paths.
This is a Linux integration harness, not a browser or RF/geometry acceptance test.
"""

from __future__ import annotations

import argparse
import json
import math
import secrets
import select
import socket
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from services.sim_ground.receiver import ReceiverError, TmReceiver, load_limits
from services.sim_sat.contact import ContactGate
from services.sim_sat.emulator import SatelliteSession, TmSpool, load_orbit_view, read_test_tc
from services.webapp.auth import AuthStore
from services.webapp.main import AppServer, initialize_internal_token
from services.webapp.sim_tc_dispatch import SimTcDispatcher
from tools.pado_packet_codec import decode, encode_tc


ROOT = Path(__file__).resolve().parents[1]


class _TestHasher:
    """Fast isolated test account; production password hashing is tested separately."""

    def hash(self, password):
        return "$argon2id$test$" + password[::-1]

    def verify(self, encoded, password):
        return encoded == self.hash(password)


def _utc(seconds: int) -> str:
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _truth(start_s: int, aos_s: int, los_s: int) -> dict:
    radius = 6_878_000.0
    inclination = math.radians(97.4)
    samples = []
    for elapsed in range(0, 541, 60):
        angle = elapsed * 0.0011
        xyz = (radius * math.cos(angle),
               radius * math.sin(angle) * math.cos(inclination),
               radius * math.sin(angle) * math.sin(inclination))
        samples.append([_utc(start_s + elapsed), *xyz,
                        0.0, 0.0, 500_000.0, 1.0, 0.0, 0.0])
    return {"schema": "fds.orbit-view.v1", "source": "TLE", "norad_id": 99999,
            "quality": "PRELIMINARY_NOT_FOR_OPERATIONS",
            "input_epoch_utc": _utc(start_s), "start_utc": _utc(start_s),
            "end_utc": _utc(start_s + 540), "sample_step_s": 60, "samples": samples,
            "stations": [{"id": "yonsei", "band": "UHF", "passes": [{
                "aos_utc": _utc(aos_s), "los_utc": _utc(los_s)}]}]}


def _wait(predicate, timeout_s: float, label: str):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.025)
    raise TimeoutError(label)


def run_smoke(base: Path) -> dict:
    if not hasattr(socket, "AF_UNIX"):
        raise RuntimeError("Linux AF_UNIX support is required for the real private TC bridge")
    base.mkdir(parents=True, exist_ok=False)
    # Keep the Unix path short even when the checkout path is long.
    bridge_temp = tempfile.TemporaryDirectory(prefix="pado-scheduled-")
    bridge_path = Path(bridge_temp.name) / "bridge.sock"
    now_s = int(time.time())
    aos_s, target_s, los_s = now_s + 8, now_s + 18, now_s + 34
    start_s = (now_s - 60) // 5 * 5
    truth_path = base / "orbit_view.json"
    truth_path.write_text(json.dumps(_truth(start_s, aos_s, los_s)), encoding="utf-8")
    schedule = {"schedule_state": "validated", "schedule_sha256": "d" * 64,
                "operational": False, "passes": [{
                    "pass_id": "yonsei-scheduled-dump-test", "station_id": "yonsei",
                    "band": "UHF", "aos_utc_s": aos_s, "los_utc_s": los_s,
                    "peak_utc_s": (aos_s + los_s) // 2, "minimum_elevation_deg": 10,
                    "max_elevation_deg": 42, "clipped_start": False, "clipped_end": False}]}
    snapshot_path = base / "test_tm_snapshot.json"
    receiver = TmReceiver(snapshot_path, base / "n11-inbox",
                          load_limits(ROOT / "config/spacecraft.example.json"), truth_path)
    gate = ContactGate(truth_path)
    stop = threading.Event()
    bridge_ready = threading.Event()
    satellite_ready = threading.Event()
    errors = []
    commands = []
    stats = {"queued": 0, "sent_pdus": 0, "pending": 0, "sample_ticks": 0,
             "transfer_active": False, "first_tm_sent_ms": None,
             "tm_sent_before_dump": False}
    satellite, ground = socket.socketpair()
    satellite.settimeout(3)
    ground.settimeout(3)

    def receive():
        try:
            receiver.process_connection(ground)
        except Exception as error:
            if not stop.is_set():
                errors.append(repr(error))
        finally:
            ground.close()

    def bridge():
        try:
            receiver.serve_tc_socket(bridge_path, stop=stop, ready=bridge_ready)
        except Exception as error:
            errors.append(repr(error))
            bridge_ready.set()

    def emulate():
        spool = None
        try:
            view = load_orbit_view(truth_path)
            spool = TmSpool(base / "tm_spool.sqlite3", full_housekeeping=True)
            for sample_s in range(start_s + 5, now_s // 5 * 5 + 1, 5):
                spool.queue_tick(view, datetime.fromtimestamp(sample_s, timezone.utc))
                stats["sample_ticks"] += 1
            stats["queued"] = spool.pending_count()
            session = SatelliteSession(spool)
            satellite_ready.set()
            next_beacon_s = 0
            while not stop.is_set():
                current_ms = int(time.time() * 1000)
                current_s = current_ms // 1000
                sample_s = current_s // 5 * 5
                spool.queue_tick(view, datetime.fromtimestamp(sample_s, timezone.utc))
                if gate.active(current_ms) and current_s >= next_beacon_s:
                    session.send_beacon(satellite, gate, now_ms=current_ms, view=view)
                    next_beacon_s = current_s + 10
                if select.select([satellite], [], [], 0.025)[0]:
                    packet = read_test_tc(satellite)
                    if packet is None:
                        break
                    if not packet.startswith(b"PSR1"):
                        decoded = decode(packet)
                        commands.append({"name": decoded["name"], "id": decoded["id"],
                                         "sequence": decoded["sequence"], "utc_ms": decoded["utc_ms"],
                                         "received_ms": int(time.time() * 1000)})
                    session.handle_uplink(packet, satellite, gate, now_ms=int(time.time() * 1000))
                sent = session.flush_requested(
                    satellite, gate, clock_ms=lambda: int(time.time() * 1000))
                if sent:
                    if stats["first_tm_sent_ms"] is None:
                        stats["first_tm_sent_ms"] = int(time.time() * 1000)
                    if not any(command["name"] == "hk_dump" for command in commands):
                        stats["tm_sent_before_dump"] = True
                stats["sent_pdus"] += sent
                stats["pending"] = spool.pending_count()
                stats["transfer_active"] = session.dump_window is not None
        except Exception as error:
            if not stop.is_set():
                errors.append(repr(error))
        finally:
            satellite_ready.set()
            satellite.close()
            if spool is not None:
                spool.close()

    threads = [threading.Thread(target=target, daemon=True)
               for target in (receive, bridge, emulate)]
    servers = []
    server_threads = []
    dispatcher = None
    auth = None
    report = None
    try:
        for thread in threads:
            thread.start()
        assert satellite_ready.wait(3) and bridge_ready.wait(3), "simulation setup timed out"
        assert not errors, errors
        token = base / "internal-token"
        initialize_internal_token(token)
        auth = AuthStore(base / "accounts.db", _TestHasher())
        run_password = secrets.token_urlsafe(24)
        auth.create_user("planner01", "planner", run_password)
        with patch("services.webapp.main.mps_passes", return_value=schedule):
            mps = AppServer(("127.0.0.1", 0), mode="mps", transport="http",
                            internal_token_file=token, candidate_db=base / "mps.db")
            ros = AppServer(("127.0.0.1", 0), mode="ros", transport="http",
                            internal_token_file=token, draft_db=base / "ros.db",
                            tm_preview=snapshot_path, sim_tc_socket=bridge_path)
            portal = AppServer(("127.0.0.1", 0), mode="portal", transport="http",
                               internal_token_file=token, auth=auth,
                               mps_url=f"http://127.0.0.1:{mps.server_port}",
                               ros_url=f"http://127.0.0.1:{ros.server_port}")
            servers.extend((portal, ros, mps))
            for server in servers:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                server_threads.append(thread)
            dispatcher = SimTcDispatcher(ros.drafts, bridge_path)
            dispatcher.start()

            def request(path, body=None, headers=None, expected=200):
                payload = None if body is None else json.dumps(body).encode()
                req = Request(f"http://127.0.0.1:{portal.server_port}" + path,
                              data=payload, headers={**(headers or {}),
                                  **({"Content-Type": "application/json"} if payload else {})})
                try:
                    response = urlopen(req, timeout=3)
                except HTTPError as error:
                    response = error
                with response:
                    data = json.loads(response.read())
                    assert response.status == expected, (path, response.status, data)
                    return dict(response.headers), data

            headers, login = request("/api/login", dict(username="planner01", password=run_password))
            cookie = {"Cookie": headers["Set-Cookie"].split(";", 1)[0]}
            authorized = {**cookie, "X-CSRF-Token": login["csrf"]}
            _, listed = request("/api/mps/passes", headers=cookie)
            assert listed["passes"][0]["aos_utc_s"] == aos_s
            reference = {"pass_id": schedule["passes"][0]["pass_id"],
                         "schedule_sha256": schedule["schedule_sha256"], "expected_revision": 1}
            command_order = ["mm_get_mode", "eps_get_hk", "hk_dump"]
            _, saved = request("/api/mps/test-sequences", {**reference,
                "expected_revision": 0, "steps": [{"offset_s": target_s - aos_s,
                    "command_name": name, "parameters": {
                        "start_utc_ms": 0, "end_utc_ms": 0} if name == "hk_dump" else {}}
                    for name in command_order]}, authorized)
            assert saved["revision"] == 1
            _, handoff = request("/api/mps/test-sequences/handoff", reference, authorized)
            assert [step["command_name"] for step in handoff["steps"]] == command_order
            _, batch = request("/api/ros/test-sim-reservations", {
                "sha256": handoff["sha256"]}, authorized)
            reservations = batch["reservations"]
            assert batch["count"] == len(command_order)
            assert batch["target_utc_s"] == target_s
            assert [item["step_id"] for item in reservations] == [1, 2, 3]
            assert all(item["status"] == "SCHEDULED" and item["target_utc_s"] == target_s
                       and item["batch_id"] == batch["batch_id"] for item in reservations)
            assert time.time() < aos_s, "test setup consumed pre-AOS observation window"
            tc = encode_tc("hk_dump", 500, int(time.time() * 1000),
                           {"start_utc_ms": 0, "end_utc_ms": 0})
            try:
                receiver.submit_tc(tc)
            except ReceiverError as error:
                assert "outside Yonsei UHF contact" in str(error), error
            else:
                raise AssertionError("pre-AOS TC was accepted")
            _wait(lambda: time.time() >= target_s - 0.75,
                  max(1, target_s - time.time() + 1), "pre-target observation")
            assert commands == [], commands
            assert not snapshot_path.exists(), "TM arrived before scheduled command"
            _, before = request("/api/ros/test-sim-reservations", headers=cookie)
            assert len(before["items"]) == len(command_order)
            assert all(item["status"] == "SCHEDULED" for item in before["items"])
            _, beacon = request("/api/ros/test-beacon", headers=cookie)
            assert beacon["type"] == "TEST_ONLY_BEACON"

            def acked():
                _, result = request("/api/ros/test-sim-reservations", headers=cookie)
                items = sorted((item for item in result["items"]
                    if item["batch_id"] == batch["batch_id"]), key=lambda item: item["step_id"])
                assert len(items) == len(command_order)
                assert all(item["status"] not in ("REJECTED", "SEND_FAILED", "TIMEOUT",
                                                 "MISSED", "CANCELLED", "UNKNOWN")
                           for item in items), items
                return items if all(item["status"] == "ACKED" for item in items) else None

            completed = _wait(acked, 8, "ordered sequence did not ACK")
            def snapshot_ready():
                assert not errors, errors
                return snapshot_path.exists()
            _wait(snapshot_ready, 10, "TM snapshot not published")
            _, snapshot = request("/api/ros/test-tm-snapshot", headers=cookie)
            assert [command["name"] for command in commands] == command_order, commands
            assert all(target_s * 1000 <= command["received_ms"] < los_s * 1000
                       for command in commands)
            assert all(left["received_ms"] <= right["received_ms"]
                       for left, right in zip(commands, commands[1:]))
            assert not stats["tm_sent_before_dump"]
            assert stats["first_tm_sent_ms"] >= commands[-1]["received_ms"]
            for command, item in zip(commands, completed):
                assert item["ack"]["command_id"] == command["id"]
                assert item["ack"]["command_sequence"] == command["sequence"]
                assert item["ack"]["result"] == 0
                assert item["target_utc_s"] == target_s
                assert item["start_offset_s"] == target_s - aos_s
            assert snapshot["attitude"]["frame"] == "SIM_BODY_TO_ITRF_NADIR"
            sample_counts = {row["id"]: len(row["samples"]) for row in snapshot["sensors"]}
            assert min(sample_counts.values()) >= stats["sample_ticks"], sample_counts
            n11_pairs = len(list((base / "n11-inbox").glob("*.manifest.json")))
            assert n11_pairs >= 1
            _wait(lambda: stats["sent_pdus"] > 0 and not stats["transfer_active"],
                  10, "ground durable receipts did not complete the stored dump")
            _, archived = request("/api/ros/received-tm?limit=100", headers=cookie)
            assert archived["total"] >= stats["sent_pdus"] + 1
            _, eps_rows = request("/api/ros/received-tm?limit=1&name=EPS_PDU1", headers=cookie)
            _, raw_detail = request("/api/ros/received-tm/" + str(eps_rows["items"][0]["id"]),
                                    headers=cookie)
            restored = decode(bytes.fromhex(raw_detail["raw_hex"]))
            assert restored["fields"] == raw_detail["fields"]
            _, chunks = request("/api/ros/received-tm?limit=1&name=FULL_HK_CHUNK", headers=cookie)
            assert chunks["items"], "full equipment housekeeping record was not transmitted"
            _, full_hk = request("/api/ros/received-tm/" + str(chunks["items"][0]["id"]),
                                headers=cookie)
            assert full_hk["full_hk"]["state"] == "complete"
            assert full_hk["full_hk"]["parameters"]
            _, feedback = request("/api/mps/test-ros-feedback", headers=cookie)
            assert feedback["bus_soh_state"] == "CURRENT", feedback
            assert {item["command_name"] for item in feedback["ack_items"]} == set(command_order), feedback
            assert {item["ack"]["command_sequence"] for item in feedback["ack_items"]} == {
                command["sequence"] for command in commands}, feedback

            _wait(lambda: time.time() >= los_s + 0.05,
                  max(1, los_s - time.time() + 1), "LOS did not arrive")
            tc = encode_tc("hk_dump", 501, int(time.time() * 1000),
                           {"start_utc_ms": 0, "end_utc_ms": 0})
            try:
                receiver.submit_tc(tc)
            except ReceiverError as error:
                assert "outside Yonsei UHF contact" in str(error), error
            else:
                raise AssertionError("post-LOS TC was accepted")
            assert [command["name"] for command in commands] == command_order, commands
            assert stats["pending"] >= 3, stats
            assert not errors, errors
            report = {"schema": "pado.scheduled-tm-smoke.v1", "result": "PASS",
                      "non_operational": True, "schedule_source": "short synthetic PASS fixture",
                      "mps_revision": saved["revision"], "reservation_status": "ACKED",
                      "batch_id": batch["batch_id"], "batch_size": batch["count"],
                      "start_offset_s": target_s - aos_s,
                      "reservation_statuses": [item["status"] for item in completed],
                      "aos_utc": _utc(aos_s), "target_utc": _utc(target_s), "los_utc": _utc(los_s),
                      "commands_received": commands, "ack": completed[-1]["ack"],
                      "acks": [item["ack"] for item in completed],
                      "ordered_sequence_verified": True, "no_tm_before_dump": True,
                      "pre_aos_blocked": True, "no_tm_before_tc": True,
                      "post_los_blocked": True, "sensor_sample_counts": sample_counts,
                      "n11_pairs": n11_pairs, "sent_pdus": stats["sent_pdus"],
                      "archived_pdus": archived["total"], "raw_and_parsed_verified": True,
                      "full_hk_record_complete": True, "durable_receipts_completed": True,
                      "new_pdus_waiting": stats["pending"], "mps_bus_soh": feedback["bus_soh_state"],
                      "limits": ["No browser, Docker, actual RF, or FDS geometric PASS validation",
                                 "Separate unit tests cover production password hashing"]}
    finally:
        if dispatcher is not None:
            dispatcher.stop()
        stop.set()
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in server_threads:
            thread.join(timeout=2)
        for thread in threads:
            thread.join(timeout=3)
        if len(servers) >= 3:
            servers[1].drafts.close()
            servers[2].candidates.close()
        if auth is not None:
            auth.close()
        bridge_temp.cleanup()
    assert report is not None
    (base / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                      encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="new directory for test files and report.json")
    args = parser.parse_args()
    output = args.output or ROOT / "out" / ("scheduled-dump-" + str(time.time_ns()))
    report = run_smoke(output.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Report: {output.resolve() / 'report.json'}")


if __name__ == "__main__":
    main()
