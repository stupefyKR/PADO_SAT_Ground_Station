import copy
import json
import tempfile
import unittest
from pathlib import Path

from tools import check_flows


class Stage7FlowTests(unittest.TestCase):
    def setUp(self):
        self.chart = json.loads(check_flows.N2_PATH.read_text(encoding="utf-8"))

    def chart_path(self, chart, directory):
        path = Path(directory) / "n2.json"
        path.write_text(json.dumps(chart), encoding="utf-8")
        return path

    def test_current_n2_has_no_publication_or_external_probe(self):
        connections = check_flows.approved_connections()
        pairs = check_flows.probe_pairs(connections)
        self.assertEqual(len(connections), 18)
        self.assertIn(("dgs-archive", "dgs-gs"), pairs)
        self.assertIn(("dgs-net", "fds"), pairs)
        self.assertIn(("dgs-gs", "ros"), pairs)
        self.assertTrue(all(a in check_flows.ROLES and b in check_flows.ROLES
                            for a, b in pairs))
        self.assertEqual(check_flows.plan(connections)["publications"], [])
        compose = check_flows.compose_text(pairs)
        self.assertNotIn("ports:", compose)
        self.assertEqual(compose.count("    internal: true"), len(pairs))

    def test_unknown_icd_cannot_open_firewall_route(self):
        chart = copy.deepcopy(self.chart)
        chart["approved_flows"][0]["connection_from"] = "ground-antenna"
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "TBD flow opened"):
                check_flows.approved_connections(self.chart_path(chart, temp))

    def test_dmz_cannot_initiate_into_operations(self):
        chart = copy.deepcopy(self.chart)
        edge = next(e for e in chart["approved_flows"] if e["id"] == "N06")
        edge["connection_from"], edge["connection_to"] = "dgs-gs", "ros"
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "DMZ initiated"):
                check_flows.approved_connections(self.chart_path(chart, temp))

    def test_sdc_is_limited_to_archive(self):
        chart = copy.deepcopy(self.chart)
        edge = next(e for e in chart["approved_flows"] if e["id"] == "N16")
        edge["connection_to"] = "mps"
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "SDC reaches"):
                check_flows.approved_connections(self.chart_path(chart, temp))

    def test_unknown_endpoint_and_port_are_rejected(self):
        chart = copy.deepcopy(self.chart)
        edge = next(e for e in chart["approved_flows"] if e["id"] == "N08")
        edge["connection_from"] = "unknown-container"
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "unknown endpoint"):
                check_flows.approved_connections(self.chart_path(chart, temp))
        edge["connection_from"] = "fds"
        edge["port"] = 2223
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "unexpected transport or port"):
                check_flows.approved_connections(self.chart_path(chart, temp))

    def test_checked_files_must_match_n2(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            check_flows.render(out)
            check_flows.check(out)
            with (out / "compose.probe.yaml").open("a", encoding="utf-8") as stream:
                stream.write("ports: [\"443:443\"]\n")
            with self.assertRaisesRegex(ValueError, "unexpected topology"):
                check_flows.check(out)


if __name__ == "__main__":
    unittest.main()
