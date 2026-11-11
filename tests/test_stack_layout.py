"""Stack changes update reference anchors without executing supplied HTML."""

import copy
import json
from pathlib import Path
import tempfile
import unittest

from tools.extract_stack_layout import extract


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "config/equipment-layout.example.json"
HTML = '''<html><script>
const OBJ = {
  crest:{name:"CREST", vendor:"Local", sub:"CREST", h:95, stack:true, conn:{}},
  dmc:{name:"DMC-3", vendor:"GomSpace", sub:"CDHS", h:22, stack:true, conn:{}},
  p60:{name:"P60 Dock", vendor:"GomSpace", sub:"EPS", h:24, stack:true, conn:{}}
};
const ORDER = {
  pdr:["crest","dmc","p60"],
  p2:["crest","dmc","p60"]
};
const FRAME_H = 340.5;
const w = 90, d = 96;
const gap = 3 + explode * 38;
</script></html>'''


class StackLayoutTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "out").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="stack-layout-", dir=ROOT / "out")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "stack_261026.html"
        self.source.write_text(HTML, encoding="utf-8")
        self.base = json.loads(BASE.read_text(encoding="utf-8"))

    def test_reference_mappings_use_existing_fields_and_limits(self):
        catalog = json.loads((ROOT / "interfaces/pado_packets/draft_v0.json").read_text(encoding="utf-8"))
        packets = {item["name"]: item["fields"] for item in catalog["telemetry"]}
        limits = json.loads((ROOT / "config/spacecraft.example.json").read_text(encoding="utf-8"))["sensor_limits"]
        seen = set()
        for equipment in self.base["equipment"]:
            self.assertNotIn(equipment["id"], seen)
            seen.add(equipment["id"])
            self.assertEqual(equipment["anchor_kind"], "BOARD_CENTER_PROXY")
            self.assertEqual(equipment["anchor_body_mm"][:2], [0, 0])
            for reading in equipment["readings"]:
                if reading.get("tm") is not None:
                    selector = reading["tm"]
                    self.assertIn(selector["field"], packets[selector["packet"]])
                if "sensor_limit_key" in reading:
                    self.assertIn(reading["sensor_limit_key"], limits)
            if equipment["telemetry_status"] == "UNMAPPED":
                self.assertEqual(equipment["readings"], [])
        self.assertTrue(self.base["source"]["provisional"])

    def test_order_and_height_changes_relocate_hosts_and_preserve_tm_selectors(self):
        previous = copy.deepcopy(self.base)
        first = extract(self.source, self.base)
        self.assertEqual(self.base, previous, "candidate extraction does not mutate baseline")
        shared = [e for e in first["equipment"] if e["source_id"] == "dmc"]
        self.assertEqual({e["anchor_body_mm"][2] for e in shared}, {-35.5})
        before = next(e for e in first["equipment"] if e["id"] == "a3200")
        self.source.write_text(HTML.replace('p2:["crest","dmc","p60"]',
            'p2:["p60","dmc","crest"]').replace('h:22,', 'h:30,'), encoding="utf-8")
        second = extract(self.source, self.base)
        after = next(e for e in second["equipment"] if e["id"] == "a3200")
        self.assertNotEqual(after["anchor_body_mm"], before["anchor_body_mm"])
        self.assertEqual(after["readings"], before["readings"])
        self.assertEqual(second["stack"]["compact_total_height_mm"], 155)
        self.assertEqual(after["size_body_mm"], [90, 96, 30])

    def test_new_hardware_is_not_assigned_old_equipment_telemetry(self):
        source = HTML.replace('  p60:{', '  radio:{name:"New radio", vendor:"Local", sub:"CS", h:10, stack:true, conn:{}},\n  p60:{')
        source = source.replace('p2:["crest","dmc","p60"]', 'p2:["crest","radio","p60"]')
        self.source.write_text(source, encoding="utf-8")
        candidate = extract(self.source, self.base)
        self.assertFalse(any(e["source_id"] == "dmc" for e in candidate["equipment"]))
        radio = next(e for e in candidate["equipment"] if e["id"] == "radio")
        self.assertEqual(radio["readings"], [])
        self.assertEqual(radio["telemetry_status"], "UNMAPPED")

    def test_unsupported_expressions_duplicate_ids_and_unknown_orders_fail_closed(self):
        for source in (HTML.replace('h:22,', 'h:(writeFile()),'),
                       HTML.replace('"crest","dmc","p60"]', '"crest","unknown","p60"]'),
                       HTML.replace('  dmc:{', '  crest:{'),
                       HTML.replace('const gap = 3 + explode * 38;', 'const gap = calculateGap();')):
            with self.subTest(source=source):
                self.source.write_text(source, encoding="utf-8")
                with self.assertRaises(ValueError):
                    extract(self.source, self.base)


if __name__ == "__main__":
    unittest.main()
