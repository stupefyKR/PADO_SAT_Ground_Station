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

    def test_external_data_paths_require_their_designated_dgs_peer(self):
        # Check even TBD(ICD) edges: a lack of transport must never permit a
        # direct payload or antenna data path to an operations subsystem.
        for edge_id, field, replacement in (
            ("N01", "data_to", "ros"),
            ("N03", "data_to", "fds"),
            ("N04", "data_to", "fds"),
            ("N16", "data_to", "mps"),
        ):
            with self.subTest(edge=edge_id):
                chart = copy.deepcopy(self.chart)
                edge = next(e for e in chart["approved_flows"] if e["id"] == edge_id)
                edge[field] = replacement
                with tempfile.TemporaryDirectory() as temp:
                    with self.assertRaisesRegex(ValueError, "external endpoint bypasses DGS"):
                        check_flows.approved_connections(self.chart_path(chart, temp))

    def test_external_connection_cannot_bypass_or_use_wrong_dgs_role(self):
        for source, destination in (("fds", "space-track"),
                                    ("dgs-net", "sdc")):
            with self.subTest(source=source, destination=destination):
                chart = copy.deepcopy(self.chart)
                edge = next(e for e in chart["approved_flows"] if e["id"] == "N03")
                edge["connection_from"] = source
                edge["connection_to"] = destination
                with tempfile.TemporaryDirectory() as temp:
                    with self.assertRaisesRegex(ValueError, "external endpoint bypasses DGS"):
                        check_flows.approved_connections(self.chart_path(chart, temp))

    def test_existing_external_flows_end_at_dgs(self):
        by_id = {edge["id"]: edge for edge in check_flows.approved_connections()}
        self.assertEqual((by_id["N03"]["source"], by_id["N03"]["destination"]),
                         ("dgs-net", "space-track"))
        for edge_id in ("N16", "N17"):
            self.assertEqual((by_id[edge_id]["source"], by_id[edge_id]["destination"]),
                             ("sdc", "dgs-archive"))

    def test_radio_and_external_data_dgs_zones_cannot_connect_directly(self):
        for field, replacement in (("data_to", "dgs-gs"),
                                   ("connection_from", "dgs-gs")):
            with self.subTest(field=field):
                chart = copy.deepcopy(self.chart)
                edge = next(e for e in chart["approved_flows"] if e["id"] == "N05")
                edge[field] = replacement
                with tempfile.TemporaryDirectory() as temp:
                    with self.assertRaisesRegex(
                        ValueError, "DGS-GS and DGS-NET must be separated"):
                        check_flows.approved_connections(self.chart_path(chart, temp))

    def test_external_boundary_metadata_cannot_be_weakened(self):
        changes = (
            ("external_access", "all_subsystems"),
            ("external_peer_map", {"space-track": "fds"}),
            ("dgs_gs_to_dgs_net", "allow"),
        )
        for key, replacement in changes:
            with self.subTest(rule=key):
                chart = copy.deepcopy(self.chart)
                chart["rules"][key] = replacement
                with tempfile.TemporaryDirectory() as temp:
                    with self.assertRaises(ValueError):
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
