"""FDS N13/N15 outbox route and immutable pair checks."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from tools.run_fds_pipeline import run_cycle
from tools.stage_fds_outbox import FILES, ROOT, stage, stage_n08


class FdsOutboxTest(unittest.TestCase):
    def setUp(self) -> None:
        (ROOT / "out").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / "out")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.run = self.root / "fds-20261023T100000Z-1234abcd"
        self.run.mkdir()
        (self.run / "run.properties").write_text(
            "schema=fds.run.v1\nquality=PRELIMINARY_NOT_FOR_OPERATIONS\n",
            encoding="utf-8",
        )
        for name, kind, recipient, _, header in FILES:
            body = header + b"rest\n"
            (self.run / name).write_bytes(body)
            (self.run / (name + ".manifest.json")).write_text(json.dumps({
                "file": name, "type": kind, "from": "fds", "to": recipient,
                "version": 1, "created": "2026-10-23T10:00:00Z",
                "sha256": hashlib.sha256(body).hexdigest(),
            }), encoding="utf-8")

    def test_stages_unique_pairs_and_rejects_restage(self) -> None:
        target = stage(self.run, self.root / "outbox")
        self.assertEqual(len(list(target.iterdir())), 6)
        for name, kind, recipient, _, _ in FILES:
            staged_name = name.removesuffix(".csv") + "-" + self.run.name + ".csv"
            body = (target / staged_name).read_bytes()
            manifest = json.loads((target / (staged_name + ".manifest.json")).read_text())
            self.assertEqual((manifest["file"], manifest["type"], manifest["to"]),
                             (staged_name, kind, recipient))
            self.assertEqual(hashlib.sha256(body).hexdigest(), manifest["sha256"])
        with self.assertRaisesRegex(ValueError, "already staged"):
            stage(self.run, self.root / "outbox")

    def test_rejects_tampering_before_publication(self) -> None:
        (self.run / "contact_schedule.csv").write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "header mismatch"):
            stage(self.run, self.root / "outbox")
        self.assertFalse((self.root / "outbox" / self.run.name).exists())

    def test_rejects_wrong_route_and_duplicate_manifest_field(self) -> None:
        path = self.run / "contact_schedule.csv.manifest.json"
        metadata = json.loads(path.read_text(encoding="utf-8"))
        metadata["to"] = "ros"
        path.write_text(json.dumps(metadata), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "route or schema"):
            stage(self.run, self.root / "outbox")
        path.write_text(path.read_text(encoding="utf-8").replace('"to": "ros"',
                        '"to": "mps", "to": "mps"'), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate manifest field"):
            stage(self.run, self.root / "outbox")

    def _write_pair(self, name: str, kind: str, recipient: str, body: bytes) -> None:
        (self.run / name).write_bytes(body)
        (self.run / (name + ".manifest.json")).write_text(json.dumps({
            "file": name, "type": kind, "from": "fds", "to": recipient,
            "version": 1, "created": "2026-10-23T10:00:00Z",
            "sha256": hashlib.sha256(body).hexdigest(),
        }), encoding="utf-8")

    def _n08_fixture(self) -> None:
        (self.run / "run.properties").write_text(
            "schema=fds.run.v1\nquality=PRELIMINARY_NOT_FOR_OPERATIONS\nprofile_step_s=10\n",
            encoding="utf-8",
        )
        contact_header = FILES[0][4]
        contacts = (
            contact_header +
            b"max_elevation_deg,minimum_elevation_deg,clipped_start,clipped_end\n"
            b"fds.contact-schedule.v1,yonsei,UHF,yonsei-001,2026-10-23T10:00:00.000Z,2026-10-23T10:00:10.000Z,2026-10-23T10:00:05.000Z,40.000000,5.000000,false,false\n"
            b"fds.contact-schedule.v1,kari,S_BAND,kari-001,2026-10-23T11:00:00.000Z,2026-10-23T11:00:10.000Z,2026-10-23T11:00:05.000Z,20.000000,10.000000,false,false\n"
        )
        self._write_pair("contact_schedule.csv", "CONTACT_SCHEDULE", "mps", contacts)
        self._write_pair("pass_information.csv", "PASS_INFORMATION", "dgs-archive", (
            b"schema,station_id,band,pass_id,utc,azimuth_deg,elevation_deg,range_m,range_rate_mps,carrier_hz,doppler_shift_hz,received_hz,frequency_example\n"
            b"fds.pass-information.v1,yonsei,UHF,yonsei-001,2026-10-23T10:00:00.000Z,10.000000,5.000000,1000.000,0.000000,436000000.000,0.000000,436000000.000000,true\n"
            b"fds.pass-information.v1,yonsei,UHF,yonsei-001,2026-10-23T10:00:10.000Z,200.000000,5.000000,1000.000,0.000000,436000000.000,0.000000,436000000.000000,true\n"
            b"fds.pass-information.v1,kari,S_BAND,kari-001,2026-10-23T11:00:00.000Z,20.000000,10.000000,1000.000,0.000000,2400000000.000,0.000000,2400000000.000000,true\n"
            b"fds.pass-information.v1,kari,S_BAND,kari-001,2026-10-23T11:00:10.000Z,210.000000,10.000000,1000.000,0.000000,2400000000.000,0.000000,2400000000.000000,true\n"
        ))
        self._write_pair("pass_request.csv", "PASS_REQUEST", "dgs-gs", (
            b"schema,station_id,band,pass_id,aos_utc,los_utc,minimum_elevation_deg,clipped_start,clipped_end,quality,station_parameters_status\n"
            b"fds.pass-request.draft.v1,yonsei,UHF,yonsei-001,2026-10-23T10:00:00.000Z,2026-10-23T10:00:10.000Z,5.000000,false,false,PRELIMINARY_NOT_FOR_OPERATIONS,UNVERIFIED_TEST_VALUES\n"
            b"fds.pass-request.draft.v1,kari,S_BAND,kari-001,2026-10-23T11:00:00.000Z,2026-10-23T11:00:10.000Z,10.000000,false,false,PRELIMINARY_NOT_FOR_OPERATIONS,UNVERIFIED_TEST_VALUES\n"
        ))
        self._write_pair("antenna_pointing.csv", "ANTENNA_POINTING", "dgs-gs", (
            b"schema,station_id,band,pass_id,utc,azimuth_deg,elevation_deg,quality,station_parameters_status\n"
            b"fds.antenna-pointing.draft.v1,yonsei,UHF,yonsei-001,2026-10-23T10:00:00.000Z,10.000000,5.000000,PRELIMINARY_NOT_FOR_OPERATIONS,UNVERIFIED_TEST_VALUES\n"
            b"fds.antenna-pointing.draft.v1,yonsei,UHF,yonsei-001,2026-10-23T10:00:10.000Z,200.000000,5.000000,PRELIMINARY_NOT_FOR_OPERATIONS,UNVERIFIED_TEST_VALUES\n"
            b"fds.antenna-pointing.draft.v1,kari,S_BAND,kari-001,2026-10-23T11:00:00.000Z,20.000000,10.000000,PRELIMINARY_NOT_FOR_OPERATIONS,UNVERIFIED_TEST_VALUES\n"
            b"fds.antenna-pointing.draft.v1,kari,S_BAND,kari-001,2026-10-23T11:00:10.000Z,210.000000,10.000000,PRELIMINARY_NOT_FOR_OPERATIONS,UNVERIFIED_TEST_VALUES\n"
        ))

    def test_n08_drafts_are_staged_only_in_separate_outbox(self) -> None:
        self._n08_fixture()
        target = stage_n08(self.run, self.root / "n08-only")
        self.assertEqual(len(list(target.iterdir())), 4)
        for name in ("pass_request", "antenna_pointing"):
            body = next(target.glob(f"{name}-*.csv"))
            manifest = json.loads((target / (body.name + ".manifest.json")).read_text())
            self.assertEqual(manifest["to"], "dgs-gs")
            self.assertEqual(manifest["sha256"], hashlib.sha256(body.read_bytes()).hexdigest())
        self.assertEqual(len(list(stage(self.run, self.root / "n13-n15").iterdir())), 6)

    def test_n08_rejects_bad_station_angle_and_missing_quality(self) -> None:
        self._n08_fixture()
        name = "antenna_pointing.csv"
        good = (self.run / name).read_bytes()
        for changed, reason in ((good.replace(b"kari,S_BAND", b"kari,UHF"), "station"),
                                (good.replace(b"200.000000", b"400.000000"), "physical"),
                                (good.replace(b"UNVERIFIED_TEST_VALUES", b"APPROVED"),
                                 "quality")):
            self._write_pair(name, "ANTENNA_POINTING", "dgs-gs", changed)
            with self.assertRaisesRegex(ValueError, reason):
                stage_n08(self.run, self.root / "n08-only")
            self.assertFalse((self.root / "n08-only" / self.run.name).exists())
        self._write_pair(name, "ANTENNA_POINTING", "dgs-gs", good)
        wrong = json.loads((self.run / (name + ".manifest.json")).read_text())
        wrong["to"] = "mps"
        (self.run / (name + ".manifest.json")).write_text(json.dumps(wrong))
        with self.assertRaisesRegex(ValueError, "route or schema"):
            stage_n08(self.run, self.root / "n08-only")

    def test_n08_rejects_pointing_that_disagrees_with_calculated_profile(self) -> None:
        self._n08_fixture()
        name = "antenna_pointing.csv"
        changed = (self.run / name).read_bytes().replace(
            b",10.000000,5.000000,PRELIMINARY",
            b",11.000000,5.000000,PRELIMINARY",
            1,
        )
        self._write_pair(name, "ANTENNA_POINTING", "dgs-gs", changed)
        with self.assertRaisesRegex(ValueError, "N15 calculated profile"):
            stage_n08(self.run, self.root / "n08-only")

    def test_n08_rejects_gap_larger_than_profile_cadence(self) -> None:
        self._n08_fixture()
        for name, kind, recipient in (
                ("contact_schedule.csv", "CONTACT_SCHEDULE", "mps"),
                ("pass_information.csv", "PASS_INFORMATION", "dgs-archive"),
                ("pass_request.csv", "PASS_REQUEST", "dgs-gs"),
                ("antenna_pointing.csv", "ANTENNA_POINTING", "dgs-gs")):
            changed = (self.run / name).read_bytes().replace(
                b"2026-10-23T10:00:10.000Z", b"2026-10-23T10:00:20.000Z")
            self._write_pair(name, kind, recipient, changed)
        with self.assertRaisesRegex(ValueError, "cadence"):
            stage_n08(self.run, self.root / "n08-only")

    def test_n08_outbox_must_not_overlap_transport_root(self) -> None:
        self._n08_fixture()
        transport = ROOT / "out/fds-outbox"
        for n08 in (transport, transport / "fds-n08", transport.parent):
            with self.subTest(n08=n08), self.assertRaisesRegex(ValueError, "separate local-only"):
                stage_n08(self.run, n08)
        custom = self.root / "transport"
        for n08 in (custom, custom / "fds-n08", self.root):
            with self.subTest(n08=n08), self.assertRaisesRegex(ValueError, "separate local-only"):
                run_cycle(Namespace(outbox_dir=custom, n08_outbox_dir=n08))

    def test_n08_allows_a_prediction_window_without_contact(self) -> None:
        self._n08_fixture()
        for name, kind, recipient in (("contact_schedule.csv", "CONTACT_SCHEDULE", "mps"),
                                       ("pass_information.csv", "PASS_INFORMATION", "dgs-archive"),
                                       ("pass_request.csv", "PASS_REQUEST", "dgs-gs"),
                                       ("antenna_pointing.csv", "ANTENNA_POINTING", "dgs-gs")):
            header = (self.run / name).read_bytes().splitlines(keepends=True)[0]
            self._write_pair(name, kind, recipient, header)
        self.assertEqual(len(list(stage_n08(self.run, self.root / "n08-only").iterdir())), 4)


if __name__ == "__main__":
    unittest.main()
