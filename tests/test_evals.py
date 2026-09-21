"""离线 Evaluation 集本身的回归测试。"""
import unittest
from pathlib import Path

from scripts.run_evals import evaluate_suite


class EvaluationSuiteTests(unittest.TestCase):
    def test_all_fixed_cases_meet_expectations(self):
        path = Path(__file__).resolve().parent.parent / 'evals' / 'report_validation_cases.json'
        name, results = evaluate_suite(path)
        self.assertEqual(name, 'controlled_report_validation_v1')
        self.assertEqual(len(results), 12)
        self.assertTrue(all(result['passed'] for result in results), results)


if __name__ == '__main__':
    unittest.main()
