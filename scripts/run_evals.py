"""运行固定离线报告评测集；不读取密钥，不调用模型。"""
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_b.validation import validate_report


def apply_override(target, path, value):
    parts = path.split('.')
    current = target
    for part in parts[:-1]:
        current = current[int(part)] if isinstance(current, list) else current[part]
    leaf = parts[-1]
    if isinstance(value, dict) and value == {'$delete': True}:
        if isinstance(current, list):
            del current[int(leaf)]
        else:
            del current[leaf]
    elif isinstance(current, list):
        current[int(leaf)] = value
    else:
        current[leaf] = value


def evaluate_suite(path):
    suite = json.loads(Path(path).read_text(encoding='utf-8'))
    results = []
    for case in suite['cases']:
        report = copy.deepcopy(suite['base_report'])
        for key, value in case.get('overrides', {}).items():
            apply_override(report, key, value)
        errors = validate_report(report, suite['reference'], suite['evidence'], suite['category'])
        valid = not errors
        expected_text = case.get('expected_error_contains')
        passed = valid is case['expected_valid']
        if expected_text is not None:
            passed = passed and any(expected_text in error for error in errors)
        results.append({'id': case['id'], 'dimension': case['dimension'],
                        'passed': passed, 'valid': valid, 'errors': errors})
    return suite['suite'], results


def main():
    path = ROOT / 'evals' / 'report_validation_cases.json'
    name, results = evaluate_suite(path)
    print(f'评测集：{name}')
    for result in results:
        print(f'{result["id"]} {result["dimension"]}: '
              f'{"PASS" if result["passed"] else "FAIL"}')
    passed = sum(result['passed'] for result in results)
    print(f'结果：{passed}/{len(results)} 通过')
    return 0 if passed == len(results) else 2


if __name__ == '__main__':
    raise SystemExit(main())
