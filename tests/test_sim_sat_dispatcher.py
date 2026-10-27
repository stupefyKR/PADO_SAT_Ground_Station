"""A beacon announces contact; only a fresh accepted TC releases stored TM."""

import json
import struct
import tempfile
import unittest
import zlib
from unittest.mock import patch
from datetime import timedelta
from pathlib import Path

from services.sim_sat.dispatcher import (CommandAction, CommandContext, TcDispatcher,
                                          REASON_HANDLER, REASON_PARAMETERS, REASON_REPLAY)
from services.sim_sat.emulator import SatelliteSession, TmSpool, parse_orbit_view, run_forever
from tests.test_sim_sat_emulator import START, _view_document
from tests.test_sim_sat_store_forward import _Wire
from tools.pado_packet_codec import CRC, HEADER, decode, encode_tc


class Gate:
    def __init__(self, active=True):
        self.open = active

    def active(self, _now_ms):
        return self.open


class SimSatDispatcherTests(unittest.TestCase):
    def setUp(self):
        output = Path(__file__).resolve().parents[1] / "out"
        output.mkdir(exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=output)
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "spool.sqlite3"
        self.spool = TmSpool(self.path)
        self.addCleanup(lambda: self.spool.close())
        self.view = parse_orbit_view(json.dumps(_view_document()).encode())
        self.stamp = int(START.timestamp()) * 1000
        self.gate = Gate()
        self.wire = _Wire()
        self.session = SatelliteSession(self.spool)

    def queue(self, *seconds):
        for offset in seconds:
            self.spool.queue_tick(self.view, START + timedelta(seconds=offset))

    def tc(self, name="hk_dump", sequence=1, fields=None):
        return encode_tc(name, sequence, self.stamp + 20_000,
                         {"start_utc_ms": 0, "end_utc_ms": 0}
                         if fields is None else fields)

    def dispatch(self, packet):
        self.session.handle_tc(packet, self.wire, self.gate, now_ms=self.stamp + 20_100)
        return decode(self.wire.frames[-1])["fields"]

    def test_beacon_only_without_tc_and_query_does_not_dump(self):
        self.queue(0, 5, 10)
        self.assertTrue(self.session.send_beacon(self.wire, self.gate,
                                                 now_ms=self.stamp + 20_000))
        self.assertEqual(self.session.flush_requested(
            self.wire, self.gate, clock_ms=lambda: self.stamp + 20_000), 0)
        beacon = decode(self.wire.frames[0])
        self.assertEqual(beacon["name"], "BEACON_CORE")
        self.assertEqual(beacon["fields"]["beacon_period_s"], 5)
        self.assertEqual(beacon["fields"]["beacon_counter"], 1)
        self.assertEqual(self.spool.pending_count(), 9)
        self.assertEqual(self.dispatch(self.tc("hk_get", fields={}))["result"], 0)
        self.assertIsNone(self.session.dump_window)
        self.assertEqual(self.spool.pending_count(), 9)

    def test_ack_precedes_snapshot_dump_and_new_samples_stay_queued(self):
        self.queue(0, 5, 10)
        self.assertEqual(self.dispatch(self.tc())["result"], 0)
        self.assertEqual([decode(p)["name"] for p in self.wire.frames], ["COMMAND_RESULT"])
        self.queue(15)
        self.assertEqual(self.session.flush_requested(
            self.wire, self.gate, clock_ms=lambda: self.stamp + 21_000), 9)
        self.assertIsNone(self.session.dump_window)
        self.assertEqual(self.spool.pending_count(), 3)
        self.assertEqual([decode(p)["utc_ms"] for _, p in self.spool.iter_pending()],
                         [self.stamp + 15_000] * 3)
        self.assertEqual(self.session.flush_requested(
            self.wire, self.gate, clock_ms=lambda: self.stamp + 21_000), 0)

    def test_dump_range_is_inclusive_and_invalid_range_rejected(self):
        self.queue(0, 5, 10, 15)
        fields = {"start_utc_ms": self.stamp + 5_000, "end_utc_ms": self.stamp + 10_000}
        self.assertEqual(self.dispatch(self.tc(fields=fields))["result"], 0)
        self.assertEqual(self.session.flush_requested(
            self.wire, self.gate, clock_ms=lambda: self.stamp + 21_000), 6)
        self.assertEqual(self.spool.pending_count(), 6)
        for index, fields in enumerate((
            {"start_utc_ms": 0, "end_utc_ms": 1},
            {"start_utc_ms": self.stamp + 15_000, "end_utc_ms": self.stamp + 5_000},
            {"start_utc_ms": self.stamp, "end_utc_ms": self.stamp + 30_000},
            {"start_utc_ms": self.stamp - 86_400_000, "end_utc_ms": self.stamp - 86_390_000},
        ), 2):
            # Simulate a peer which bypassed encoder semantic checks but sent
            # well-framed, CRC-valid command fields. Dispatcher still rejects.
            malformed = bytearray(self.tc(sequence=index))
            struct.pack_into(">QQ", malformed, HEADER.size + 8,
                             fields["start_utc_ms"], fields["end_utc_ms"])
            CRC.pack_into(malformed, len(malformed) - CRC.size,
                          zlib.crc32(malformed[:-CRC.size]) & 0xffffffff)
            self.assertEqual(self.dispatch(bytes(malformed))["reason"],
                             REASON_PARAMETERS)
        self.assertIsNone(self.session.dump_window)

    def test_los_cancels_authorization_and_restart_keeps_replay_and_tm(self):
        self.queue(0, 5, 10)
        command = self.tc()
        self.dispatch(command)
        self.assertEqual(self.session.flush_requested(
            self.wire, self.gate, clock_ms=lambda: self.stamp + 21_000, max_pdus=2), 2)
        self.gate.open = False
        self.assertEqual(self.session.flush_requested(
            self.wire, self.gate, clock_ms=lambda: self.stamp + 21_000), 0)
        self.assertIsNone(self.session.dump_window)
        self.gate.open = True
        self.spool.close()
        self.spool = TmSpool(self.path)
        self.session = SatelliteSession(self.spool)
        self.assertEqual(self.spool.pending_count(), 7)
        self.assertEqual(self.session.flush_requested(
            self.wire, self.gate, clock_ms=lambda: self.stamp + 21_000), 0)
        self.assertEqual(self.dispatch(command)["reason"], REASON_REPLAY)
        self.assertIsNone(self.session.dump_window)
        self.assertEqual(self.dispatch(self.tc(sequence=2))["result"], 0)
        self.assertEqual(self.session.flush_requested(
            self.wire, self.gate, clock_ms=lambda: self.stamp + 21_000), 7)

    def test_closed_contact_neither_beacons_nor_dispatches(self):
        self.queue(0)
        self.gate.open = False
        self.assertFalse(self.session.send_beacon(self.wire, self.gate, now_ms=self.stamp))
        self.assertFalse(self.session.handle_tc(self.tc(), self.wire, self.gate,
                                                now_ms=self.stamp + 20_100))
        self.assertEqual(self.wire.frames, [])
        self.assertEqual(self.session.dispatcher.replay.seen, {})

    def test_registry_extension_and_handler_failure(self):
        dispatcher = TcDispatcher()
        calls = []
        def handler(command, context):
            calls.append((command["name"], context.received_ms))
            return CommandAction()
        dispatcher.register("ds_ls", handler)
        result = dispatcher.dispatch(self.tc("ds_ls", fields={}), 1,
                                     CommandContext(self.stamp + 20_100))
        self.assertEqual(decode(result.ack)["fields"]["result"], 0)
        self.assertEqual(calls, [("ds_ls", self.stamp + 20_100)])
        with self.assertRaisesRegex(ValueError, "already registered"):
            dispatcher.register("ds_ls", handler)
        dispatcher.register("sp_get_state", lambda *_: 42)
        result = dispatcher.dispatch(self.tc("sp_get_state", sequence=2, fields={}), 2,
                                     CommandContext(self.stamp + 20_100))
        self.assertEqual(decode(result.ack)["fields"]["reason"], REASON_HANDLER)
        self.assertIsNone(result.action)

    def test_beacon_counter_survives_restart_independent_of_tm_sequence(self):
        self.queue(0)
        self.session.send_beacon(self.wire, self.gate, now_ms=self.stamp + 20_000)
        self.dispatch(self.tc("hk_get", fields={}))
        self.spool.close()
        self.spool = TmSpool(self.path)
        self.session = SatelliteSession(self.spool)
        self.session.send_beacon(self.wire, self.gate, now_ms=self.stamp + 25_000)
        beacon = decode(self.wire.frames[-1])
        self.assertEqual(beacon["fields"]["beacon_counter"], 2)
        self.assertEqual(beacon["sequence"], 3)
        self.assertEqual(self.spool.pending_count(), 3)

    def test_runtime_contact_does_not_automatically_drain_backlog(self):
        class StopLoop(RuntimeError):
            pass

        class Wire(_Wire):
            def settimeout(self, _timeout):
                pass

            def close(self):
                pass

        ticks = [0.0]
        def monotonic():
            ticks[0] += 0.5
            return ticks[0]

        def stop(_delay):
            raise StopLoop()

        runtime_path = self.path.parent / "runtime.sqlite3"
        wire = Wire()
        with patch("services.sim_sat.emulator.load_orbit_view", return_value=self.view), \
                patch("services.sim_sat.emulator.ContactGate", return_value=self.gate), \
                patch("services.sim_sat.emulator.socket.create_connection", return_value=wire), \
                patch("services.sim_sat.emulator.select.select", return_value=([], [], [])), \
                patch("services.sim_sat.emulator.time.monotonic", side_effect=monotonic):
            with self.assertRaises(StopLoop):
                run_forever(Path("unused.json"), "sim-ground", 9200, 5, 1, runtime_path,
                            clock=lambda: START + timedelta(seconds=20), sleeper=stop)
        self.assertEqual([decode(p)["name"] for p in wire.frames], ["BEACON_CORE"])
        reopened = TmSpool(runtime_path)
        try:
            self.assertEqual(reopened.pending_count(), 3)
        finally:
            reopened.close()


if __name__ == "__main__":
    unittest.main()
