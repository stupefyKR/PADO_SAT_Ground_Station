"""Checks that the stage-1 contracts preserve the approved security boundaries."""

import hashlib
import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class InterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chart = json.loads((ROOT / "interfaces/n2_chart.json").read_text(encoding="utf-8"))
        cls.flows = cls.chart["approved_flows"]

    def test_source_pdf_is_exactly_the_reviewed_version(self):
        pdf = ROOT / self.chart["source"]["document"]
        self.assertEqual(hashlib.sha256(pdf.read_bytes()).hexdigest(),
                         self.chart["source"]["sha256"])
        self.assertEqual(len(self.chart["source_n2_edges"]), 21)

    def test_unique_flows_reference_declared_allow_table(self):
        self.assertEqual(len({flow["id"] for flow in self.flows}), len(self.flows))
        document = (ROOT / "docs/PADO-SAT_지상국_개발방향.md").read_text(encoding="utf-8")
        for flow in self.flows:
            self.assertIn(f"| {flow['reference']} |", document)
            self.assertTrue(flow["types"])
            self.assertEqual(len(set(flow["types"])), len(flow["types"]))
            if flow["connection_from"] is None:
                self.assertIsNone(flow["connection_to"])
                self.assertIsNone(flow["port"])
                self.assertEqual(flow["transport"], "TBD(ICD)")
            else:
                self.assertIsNotNone(flow["connection_to"])
                self.assertIsInstance(flow["port"], int)
                self.assertGreater(flow["port"], 0)

    def test_dmz_never_starts_connection_into_operations(self):
        for flow in self.flows:
            self.assertFalse(flow["connection_from"] == "dgs-gs" and
                             flow["connection_to"] in
                             {"ros", "mps", "fds", "dgs-archive"})

    def test_raw_data_is_archive_pull_and_sdc_only_touches_archive(self):
        raw = next(flow for flow in self.flows if flow["id"] == "N09")
        self.assertEqual((raw["data_from"], raw["data_to"]),
                         ("dgs-gs", "dgs-archive"))
        self.assertEqual((raw["connection_from"], raw["connection_to"], raw["port"]),
                         ("dgs-archive", "dgs-gs", 2222))
        self.assertEqual(raw["transport"], "SFTP_PULL")
        for flow in self.flows:
            if flow["connection_from"] == "sdc":
                self.assertEqual(flow["connection_to"], "dgs-archive")

    def test_tle_goes_to_fds_and_never_ros(self):
        tle = [flow for flow in self.flows if "NORAD_TLE" in flow["types"]]
        self.assertTrue(any(flow["data_to"] == "fds" for flow in tle))
        self.assertFalse(any(flow["data_to"] == "ros" for flow in tle))

    def test_unknown_command_and_tm_values_are_not_operational(self):
        for path, member in (("command_db/commands.json", "commands"),
                             ("tm_db/telemetry.json", "parameters")):
            value = json.loads((ROOT / "interfaces" / path).read_text(encoding="utf-8"))
            self.assertFalse(value["operational"])
            self.assertEqual(value["status"], "TBD(ICD)")
            self.assertEqual(value[member], [])


if __name__ == "__main__":
    unittest.main()
