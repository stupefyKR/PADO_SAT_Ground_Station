"""The published plot and download must represent one hashed FDS PASS."""

import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools.doppler_profiles import (BUNDLE_NAME, DopplerError, LIGHT_SPEED_MPS,
    PROFILE_HEADER, SCHEDULE_HEADER, build_bundle, select_profile)
from tools.publish_fds_view import publish


SCRATCH = Path(__file__).resolve().parents[1] / "out"


def make_run(root: Path, now: datetime | None = None):
    """An actual publication fixture, also reused by the HTTP endpoint tests."""
    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    stamp = lambda value: value.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    start, end, epoch = now - timedelta(seconds=300), now + timedelta(hours=1), now - timedelta(seconds=302)
    run = root / "fds-profile-test"
    run.mkdir()
    directory = run / "doppler_profiles"
    directory.mkdir()
    rows = []
    raw_profiles = {}
    stations = []
    for station, band, frequency, offset in (("yonsei", "UHF", 435000000., -100),
                                             ("kari", "S_BAND", 2200000000., 100)):
        identifier = station + "-001"
        aos = now + timedelta(seconds=offset)
        peak, los = aos + timedelta(seconds=10), aos + timedelta(seconds=20)
        rows.append(f"fds.contact-schedule.v1,{station},{band},{identifier},{stamp(aos)},"
                    f"{stamp(los)},{stamp(peak)},45.000000,10.000000,false,false\n")
        stations.append({"id": station, "band": band, "latitude_deg": 37., "longitude_deg": 127.,
                         "altitude_m": 0., "minimum_elevation_deg": 10., "passes": [{
                             "aos_utc": stamp(aos), "los_utc": stamp(los), "peak_utc": stamp(peak),
                             "max_elevation_deg": 45., "clipped_start": False, "clipped_end": False}]})
        profile = PROFILE_HEADER + "\n"
        for index, rate in enumerate((-5000., 0., 5000.)):
            shift = -frequency * rate / LIGHT_SPEED_MPS
            profile += (f"fds.pass-information.v1,{station},{band},{identifier},"
                        f"{stamp(aos + timedelta(seconds=index * 10))},90.000000,"
                        f"{(45. if index == 1 else 10.):.6f},1000000.000,{rate:.6f},"
                        f"{frequency:.3f},{shift:.6f},{frequency + shift:.6f},true\n")
        raw_profiles[identifier] = profile.encode("ascii")
        (directory / (identifier + ".csv")).write_bytes(raw_profiles[identifier])
    schedule = (SCHEDULE_HEADER + "\n" + "".join(rows)).encode("ascii")
    aggregate = (PROFILE_HEADER + "\n").encode("ascii") + b"".join(
        raw.split(b"\n", 1)[1] for raw in raw_profiles.values())
    digest = hashlib.sha256(schedule).hexdigest()
    for name, kind, recipient, body in (("contact_schedule.csv", "CONTACT_SCHEDULE", "mps", schedule),
        ("pass_information.csv", "PASS_INFORMATION", "dgs-archive", aggregate)):
        (run / name).write_bytes(body)
        (run / (name + ".manifest.json")).write_text(json.dumps({"file": name, "type": kind,
            "from": "fds", "to": recipient, "version": 1, "created": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sha256": hashlib.sha256(body).hexdigest()}), encoding="ascii")
    orbit = {"schema": "fds.orbit-view.v1", "quality": "PRELIMINARY_NOT_FOR_OPERATIONS",
        "norad_id": 99999, "source": "TLE", "model": "SGP4", "input_sha256": "a" * 64,
        "input_epoch_utc": stamp(epoch), "start_utc": stamp(start), "end_utc": stamp(end),
        "contact_schedule_sha256": digest, "sample_step_s": 60, "orbital_period_s": 5500.,
        "generated_utc": stamp(now), "stations": stations, "samples": [
            [stamp(start + timedelta(seconds=offset)), 6878137., 0., 0., 0., 0., 500000., 1., 0., 0.]
            for offset in range(0, int((end - start).total_seconds()) + 1, 60)]}
    (run / "orbit_view.json").write_text(json.dumps(orbit), encoding="ascii")
    (run / "run.properties").write_text("schema=fds.run.v1\nsource=TLE\nexpected_norad_id=99999\n"
        "input_sha256=" + "a" * 64 + "\nprofile_step_s=10\n", encoding="ascii")
    return run, orbit, digest, raw_profiles


class DopplerPublicationTests(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.run, self.orbit, self.digest, self.profiles = make_run(self.root, self.now)

    def read(self, view, identifier="yonsei-001", digest=None):
        return select_profile(view, identifier, digest or self.digest, self.now.timestamp())

    def test_both_stations_and_past_pass_retain_exact_fds_csv(self):
        view = publish(self.run, self.root / "served")
        for identifier, expected in self.profiles.items():
            payload, raw = self.read(view, identifier)
            self.assertEqual(raw, expected)
            self.assertEqual(payload["profile_sha256"], hashlib.sha256(expected).hexdigest())
            self.assertEqual(payload["schema"], "fds.doppler-profile.v1")
            self.assertEqual(payload["schedule_sha256"], self.digest)
            self.assertEqual(payload["station_id"], identifier.split("-")[0])
            self.assertEqual(payload["sample_step_s"], 10)
            self.assertTrue(payload["frequency_example"])
            self.assertGreater(payload["samples"][0][2], 0)
            self.assertLess(payload["samples"][-1][2], 0)
        payload, _ = self.read(view)
        self.assertLess(datetime.fromisoformat(payload["los_utc"].replace("Z", "+00:00")), self.now)

    def test_schedule_change_unknown_pass_and_client_path_are_distinct(self):
        view = publish(self.run, self.root / "served")
        for identifier, digest, code in (("yonsei-001", "b" * 64, "schedule_changed"),
            ("yonsei-002", self.digest, "pass_not_found"),
            ("../secret", self.digest, "invalid_query")):
            with self.subTest(code=code), self.assertRaises(DopplerError) as context:
                self.read(view, identifier, digest)
            self.assertEqual(context.exception.code, code)

    def test_mismatched_or_stale_publication_cannot_be_served(self):
        view = publish(self.run, self.root / "served")
        bundle_path = view.with_name(BUNDLE_NAME)
        original = bundle_path.read_bytes()
        bundle = json.loads(original)
        bundle["provenance"]["input_sha256"] = "c" * 64
        bundle_path.write_text(json.dumps(bundle), encoding="ascii")
        with self.assertRaises(DopplerError):
            self.read(view)
        bundle_path.write_bytes(original)
        with self.assertRaises(DopplerError):
            select_profile(view, "yonsei-001", self.digest, (self.now + timedelta(hours=73)).timestamp())

    def test_tampered_bundle_csv_and_missing_bundle_are_rejected(self):
        view = publish(self.run, self.root / "served")
        path = view.with_name(BUNDLE_NAME)
        bundle = json.loads(path.read_bytes())
        bundle["profiles"]["yonsei-001"]["csv"] = bundle["profiles"]["yonsei-001"]["csv"].replace(
            "1000000.000", "1000001.000")
        path.write_text(json.dumps(bundle), encoding="ascii")
        with self.assertRaises(DopplerError):
            self.read(view)
        path.unlink()
        with self.assertRaises(DopplerError):
            self.read(view)

    def test_broken_files_cannot_replace_a_published_view(self):
        view = publish(self.run, self.root / "served")
        before = view.read_bytes(), view.with_name(BUNDLE_NAME).read_bytes()
        path = self.run / "doppler_profiles/yonsei-001.csv"
        raw = path.read_bytes()
        cases = [raw.replace(b"1000000.000", b"1000001.000"),
                 b"\n".join([*raw.split(b"\n")[:1], *reversed(raw.split(b"\n")[1:-1])]) + b"\n"]
        for broken in cases:
            with self.subTest(case=broken[:50]):
                path.write_bytes(broken)
                with self.assertRaises(ValueError):
                    publish(self.run, view.parent)
                self.assertEqual((view.read_bytes(), view.with_name(BUNDLE_NAME).read_bytes()), before)
        path.write_bytes(raw)
        self.orbit["contact_schedule_sha256"] = "b" * 64
        with self.assertRaises(ValueError):
            build_bundle(self.run, self.orbit)

    def test_semantic_errors_are_checked_after_valid_manifest_hashes(self):
        identifier = "yonsei-001"
        original = self.profiles[identifier]
        changes = ((1, 10, "0.000000"), (1, 11, "435000000.000000"), (1, 8, "NaN"),
                   (2, 4, original.decode("ascii").splitlines()[1].split(",")[4]),
                   (2, 12, "false"), (1, 2, "S_BAND"))
        for row_index, column, replacement in changes:
            with self.subTest(column=column, value=replacement):
                lines = original.decode("ascii").splitlines()
                row = lines[row_index].split(",")
                row[column] = replacement
                lines[row_index] = ",".join(row)
                changed = ("\n".join(lines) + "\n").encode("ascii")
                (self.run / "doppler_profiles" / (identifier + ".csv")).write_bytes(changed)
                body = (PROFILE_HEADER + "\n").encode("ascii") + changed.split(b"\n", 1)[1] + \
                    self.profiles["kari-001"].split(b"\n", 1)[1]
                (self.run / "pass_information.csv").write_bytes(body)
                manifest_path = self.run / "pass_information.csv.manifest.json"
                manifest = json.loads(manifest_path.read_bytes())
                manifest["sha256"] = hashlib.sha256(body).hexdigest()
                manifest_path.write_text(json.dumps(manifest), encoding="ascii")
                with self.assertRaises(ValueError):
                    build_bundle(self.run, self.orbit)

    def test_fractional_carrier_rounding_preserves_original_fds_frequencies(self):
        frequency = 435000000.12349
        lines = self.profiles["yonsei-001"].decode("ascii").splitlines()
        for index in range(1, len(lines)):
            fields = lines[index].split(",")
            shift = -frequency * float(fields[8]) / LIGHT_SPEED_MPS
            fields[9:12] = [f"{frequency:.3f}", f"{shift:.6f}", f"{frequency + shift:.6f}"]
            lines[index] = ",".join(fields)
        raw = ("\n".join(lines) + "\n").encode("ascii")
        (self.run / "doppler_profiles/yonsei-001.csv").write_bytes(raw)
        body = (PROFILE_HEADER + "\n").encode("ascii") + raw.split(b"\n", 1)[1] + \
            self.profiles["kari-001"].split(b"\n", 1)[1]
        (self.run / "pass_information.csv").write_bytes(body)
        path = self.run / "pass_information.csv.manifest.json"
        manifest = json.loads(path.read_bytes())
        manifest["sha256"] = hashlib.sha256(body).hexdigest()
        path.write_text(json.dumps(manifest), encoding="ascii")
        view = publish(self.run, self.root / "served")
        payload, downloaded = self.read(view)
        self.assertEqual(downloaded, raw)
        self.assertEqual(payload["carrier_hz"], 435000000.123)
        self.assertGreater(abs(payload["samples"][1][3] - payload["carrier_hz"]), .0004)

    def test_schedule_or_entry_metadata_cannot_be_relabelled(self):
        view = publish(self.run, self.root / "served")
        path = view.with_name(BUNDLE_NAME)
        original = json.loads(path.read_bytes())
        bundle = json.loads(json.dumps(original))
        bundle["schedule_csv"] = bundle["schedule_csv"].replace("yonsei-001", "yonsei-003")
        path.write_text(json.dumps(bundle), encoding="ascii")
        with self.assertRaises(DopplerError):
            self.read(view)
        bundle = original
        bundle["profiles"]["yonsei-001"]["pass"]["aos_utc"] = self.orbit["start_utc"]
        path.write_text(json.dumps(bundle), encoding="ascii")
        with self.assertRaises(DopplerError):
            self.read(view)

    def test_empty_passes_and_legacy_runs_are_supported(self):
        for path in (self.run / "doppler_profiles").iterdir():
            path.unlink()
        for name, header in (("contact_schedule.csv", SCHEDULE_HEADER), ("pass_information.csv", PROFILE_HEADER)):
            body = (header + "\n").encode("ascii")
            (self.run / name).write_bytes(body)
            path = self.run / (name + ".manifest.json")
            manifest = json.loads(path.read_bytes())
            manifest["sha256"] = hashlib.sha256(body).hexdigest()
            path.write_text(json.dumps(manifest), encoding="ascii")
        self.orbit["contact_schedule_sha256"] = hashlib.sha256(
            (self.run / "contact_schedule.csv").read_bytes()).hexdigest()
        for station in self.orbit["stations"]:
            station["passes"] = []
        self.assertEqual(json.loads(build_bundle(self.run, self.orbit))["profiles"], {})
        (self.run / "doppler_profiles").rmdir()
        with self.assertRaises(ValueError):
            build_bundle(self.run, self.orbit)
        del self.orbit["contact_schedule_sha256"]
        self.assertIsNone(build_bundle(self.run, self.orbit))

    @unittest.skipUnless(os.name == "posix", "POSIX symlink fixture")
    def test_profile_and_publication_symlinks_are_rejected(self):
        path = self.run / "doppler_profiles/yonsei-001.csv"
        original = self.root / "original.csv"
        path.rename(original)
        path.symlink_to(original)
        with self.assertRaises(ValueError):
            build_bundle(self.run, self.orbit)
        path.unlink()
        original.rename(path)
        view = publish(self.run, self.root / "served")
        bundle = view.with_name(BUNDLE_NAME)
        target = self.root / "bundle.json"
        bundle.rename(target)
        bundle.symlink_to(target)
        with self.assertRaises(DopplerError):
            self.read(view)


if __name__ == "__main__":
    unittest.main()
