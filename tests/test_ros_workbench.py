import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path

from services.webapp.ros_workbench import DraftError, RosDraftStore, parse_test_pair


def pair(aos):
    sequence = {
        "type": "TEST_ONLY_SEQUENCE", "non_operational": True, "revision": 1,
        "command_db_version": 1, "pass_id": "yonsei-001",
        "aos_utc_s": aos, "los_utc_s": aos + 600,
        "guard_before_s": 0, "guard_after_s": 0,
        "initial_state": 0, "transfer_rate_bps": 1000, "max_commands": 4,
        "edited_by": "planner01", "approved_by": "lead01",
        "approved_at_utc_s": aos - 60,
        "steps": [{
            "step_id": 1, "command_id": "TEST_PING", "offset_s": 60,
            "duration_s": 2, "resource_mask": 0, "require_set": 0,
            "require_clear": 0, "effect_set": 0, "effect_clear": 0,
            "uplink_bytes": 4
        }]
    }
    raw = json.dumps(sequence, separators=(",", ":"))
    manifest = {
        "file": "TEST_ONLY_fixture.json", "type": "TEST_ONLY_SEQUENCE",
        "from": "mps", "to": "ros", "version": 1,
        "created": "2026-10-23T00:00:00Z",
        "sha256": hashlib.sha256(raw.encode()).hexdigest()
    }
    return raw, json.dumps(manifest)


class RosWorkbenchTests(unittest.TestCase):
    def test_verified_pair_and_draft_lifecycle(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ros.db"
            store = RosDraftStore(path)
            raw, manifest = pair(int(time.time()) + 3600)
            preview = store.import_pair(raw, manifest, "operator01")
            self.assertEqual(preview["steps"][0]["command_id"], "TEST_PING")
            self.assertEqual(len(store.list_sequences()), 1)
            target = preview["aos_utc_s"] + 60
            draft = store.reserve(preview["sha256"], 1, target, "operator01")
            self.assertEqual(draft["status"], "DRAFT_ONLY")
            with self.assertRaises(DraftError):
                store.reserve(preview["sha256"], 1, target, "operator01")
            with self.assertRaises(DraftError):
                store.cancel(draft["id"], "another_user")
            store.cancel(draft["id"], "operator01")
            self.assertEqual(store.reservations()[0]["status"], "CANCELLED")
            store.close()
            reopened = RosDraftStore(path)
            self.assertEqual(reopened.reservations()[0]["status"], "CANCELLED")
            reopened.close()

    def test_tampering_and_invalid_time_rejected(self):
        raw, manifest = pair(int(time.time()) + 3600)
        with self.assertRaises(DraftError):
            parse_test_pair(raw.replace("TEST_PING", "TEST_FIRE"), manifest)
        with self.assertRaises(DraftError):
            parse_test_pair(raw.replace('"type":', '"type":"DUPLICATE","type":'), manifest)
        bad = json.loads(manifest)
        bad["to"] = "dgs-gs"
        with self.assertRaises(DraftError):
            parse_test_pair(raw, json.dumps(bad))
        with tempfile.TemporaryDirectory() as folder:
            store = RosDraftStore(Path(folder) / "ros.db")
            preview = store.import_pair(raw, manifest, "operator01")
            with self.assertRaises(DraftError):
                store.reserve(preview["sha256"], 1, preview["los_utc_s"], "operator01")
            with self.assertRaises(DraftError):
                store.reserve(preview["sha256"], 99, preview["aos_utc_s"] + 100, "operator01")
            store.close()


if __name__ == "__main__":
    unittest.main()
