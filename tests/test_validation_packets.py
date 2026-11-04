import collections
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from services.validation.corpus import generate, tc_matrix
from services.validation.recorder import Recorder, report, parse_tm
from services.spacecraft_config import get_sensor_limits
from tools.pado_packet_codec import decode, PacketError, load_catalog
from services.sim_ground.archive import json_fields

ROOT = Path(__file__).resolve().parents[1]


class ValidationCorpusTests(unittest.TestCase):
    def test_ten_thousand_wire_packets_and_independent_expected_fields(self):
        cases = generate(724, 1793700000000)
        self.assertEqual(collections.Counter(c.category for c in cases),
                         {'valid': 7000, 'malformed': 2500, 'spike': 500})
        self.assertEqual({c.name for c in cases}, {e['name'] for e in load_catalog()['telemetry']})
        self.assertNotEqual([c.index for c in cases], list(range(10000)))
        for case in cases:
            with self.subTest(index=case.index, mutation=case.mutation):
                if case.category == 'malformed':
                    with self.assertRaises((PacketError, ValueError)):
                        parse_tm(case.packet,received_ms=1793711000000)
                else:
                    actual = decode(case.packet)
                    self.assertEqual(actual['name'], case.name)
                    self.assertEqual(json_fields(actual['fields']), case.expected_fields)
        self.assertEqual([c.packet for c in cases], [c.packet for c in generate(724, 1793700000000)])

    def test_observation_report_and_spikes(self):
        (ROOT / 'out').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / 'out') as temporary:
            path = Path(temporary) / 'validation.sqlite3'
            recorder = Recorder(path, limits=get_sensor_limits())
            try:
                key = recorder.begin(724, 1793700000000, 10000)
                for case in generate(724, 1793700000000):
                    recorder.receive(case.packet, 1793711000000)
                result = report(path, key)
                self.assertEqual(result['state'], 'PASS', result)
                self.assertEqual(result['valid_parsed'], 7500)
                self.assertEqual(result['malformed_rejected'], 2500)
                self.assertGreaterEqual(result['sensor_events'], 500)
                recorder.project(Path(temporary) / 'test_tm_snapshot.json', 1793711000000)
                self.assertTrue((Path(temporary) / 'test_tm_snapshot.json').is_file())
            finally:
                recorder.close()

    def test_tc_coverage_excludes_undefined_icds(self):
        matrix = tc_matrix(1793700000000)
        defined = {c['name'] for c in load_catalog()['commands'] if c['fields'] is not None}
        self.assertEqual({c['name'] for c in matrix['cases']}, defined)
        self.assertEqual(matrix['defined_command_count'], len(defined))
        self.assertEqual(len(matrix['undefined_commands']), 22)

    def test_report_cannot_pass_missing_wrong_or_misclassified_packets(self):
        (ROOT/'out').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT/'out') as folder:
            path=Path(folder)/'validation.sqlite3'
            recorder=Recorder(path,limits=get_sensor_limits())
            try:
                key=recorder.begin(3,1793700000000,100)
                cases=generate(3,1793700000000,100)
                bad=next(c for c in cases if c.category=='malformed')
                good=[c for c in cases if c.category=='valid'][:2]
                for case in cases[:-1]:recorder.receive(case.packet,1793711000000)
                self.assertEqual(report(path,key)['missing'],1)
                self.assertNotEqual(report(path,key)['state'],'PASS')
                recorder.receive(cases[-1].packet,1793711000000)
                self.assertEqual(report(path,key)['state'],'PASS')
                # Deliberate decoder regressions must make the comparison fail.
                recorder.db.execute('DELETE FROM received WHERE run=? AND raw IN (?,?,?)',
                                    (key,bad.packet,good[0].packet,good[1].packet));recorder.db.commit()
                with patch('services.validation.recorder.parse_tm',return_value=decode(good[0].packet)):
                    recorder.receive(bad.packet,1793711000000)
                with patch('services.validation.recorder.parse_tm',side_effect=PacketError('injected regression')):
                    recorder.receive(good[0].packet,1793711000000)
                wrong=decode(good[1].packet);wrong['fields']={**wrong['fields'],'unexpected_value':42}
                with patch('services.validation.recorder.parse_tm',return_value=wrong):
                    recorder.receive(good[1].packet,1793711000000)
                result=report(path,key)
                self.assertEqual(result['state'],'FAIL')
                self.assertEqual(result['error_counts']['false_accept'],1)
                self.assertEqual(result['error_counts']['false_reject'],1)
                self.assertEqual(result['error_counts']['wrong_fields'],1)
            finally:recorder.close()


if __name__ == '__main__':
    unittest.main()
