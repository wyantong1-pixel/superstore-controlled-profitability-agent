"""无网络自动测试：小样本可手算，验证质量、SQL权限、报告证据和Agent控制器。

从项目根目录执行 python -m unittest discover -s tests -v。
测试不用真实API，也不读取用户密钥；通过不代表在线模型已经验收。
"""
import json
import io
import tempfile
import urllib.error
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch, Mock
import pandas as pd
from agent_b.audit import Audit
from agent_b.config import Limits
from agent_b.controller import run_agent
from agent_b.data import FIELDS, load_checked, reference_metrics
from agent_b.database import QueryEngine
from agent_b.providers import Bailian, DemoModel
from agent_b.validation import validate_report


def fixture():
    """4条明细、3个订单；Furniture为3条明细、2个订单、销售350、利润25。"""
    return pd.DataFrame([
        [1, 'A', '09/01/2020', 'Furniture', 'Tables', 'East', 'Consumer', 'P1', 100, -10, .3, 1],
        [2, 'A', '09/01/2020', 'Furniture', 'Chairs', 'East', 'Consumer', 'P2', 200, 40, 0, 2],
        [3, 'B', '09/02/2020', 'Office Supplies', 'Paper', 'West', 'Corporate', 'P3', 20, 5, .1, 1],
        [4, 'C', '09/03/2020', 'Furniture', 'Tables', 'West', 'Consumer', 'P1', 50, -5, .4, 1]
    ], columns=FIELDS)


class DataTests(unittest.TestCase):
    def check(self, frame):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input.csv'
            frame.to_csv(path, index=False)
            return load_checked(path)

    def test_valid_and_order_duplicates_allowed(self):
        df, quality = self.check(fixture())
        self.assertTrue(quality['passed'])
        self.assertEqual(reference_metrics(df, 'Furniture')['order_count'], 2)

    def test_reference(self):
        result = reference_metrics(fixture(), 'Furniture')
        self.assertEqual(result['sales'], 350)
        self.assertEqual(result['profit'], 25)
        self.assertAlmostEqual(result['profit_margin'], 25/350)

    def test_duplicate_row_blocked(self):
        frame = fixture()
        self.assertFalse(self.check(pd.concat([frame, frame.iloc[:1]]))[1]['passed'])

    def test_missing_column(self):
        self.assertFalse(self.check(fixture().drop(columns='profit'))[1]['passed'])

    def test_bad_numeric(self):
        frame = fixture().astype({'sales': 'object'})
        frame.loc[0, 'sales'] = 'bad'
        self.assertFalse(self.check(frame)[1]['passed'])

    def test_bad_date(self):
        frame = fixture()
        frame.loc[0, 'order_date'] = 'not-a-date'
        self.assertFalse(self.check(frame)[1]['passed'])

    def test_discount_range(self):
        frame = fixture()
        frame.loc[0, 'discount'] = 1.5
        self.assertFalse(self.check(frame)[1]['passed'])

    def test_empty(self):
        self.assertFalse(self.check(fixture().iloc[:0])[1]['passed'])

    def test_zero_denominator(self):
        frame = fixture()
        frame['sales'] = 0
        df, quality = self.check(frame)
        self.assertTrue(quality['passed'])
        self.assertIsNone(reference_metrics(df, 'ALL')['profit_margin'])

    def test_pii_removed(self):
        frame = fixture()
        frame['Customer Name'] = 'PRIVATE'
        self.assertNotIn('customer_name', self.check(frame)[0].columns)


class SQLTests(unittest.TestCase):
    def setUp(self):
        self.engine = QueryEngine(fixture(), Limits())

    def tearDown(self):
        self.engine.close()

    def test_sum(self):
        self.assertEqual(self.engine.run('SELECT SUM(sales) AS sales FROM orders')['rows'], [[370]])

    def test_count_star(self):
        self.assertEqual(self.engine.run('SELECT COUNT(*) AS n FROM orders')['rows'], [[4]])

    def test_cte(self):
        self.assertTrue(self.engine.run('WITH t AS (SELECT sales FROM orders) SELECT SUM(sales) FROM t')['ok'])

    def test_mutations_and_system_access_denied(self):
        for sql in ['DELETE FROM orders', 'DROP TABLE orders', 'UPDATE orders SET sales=0',
                    "ATTACH DATABASE ':memory:' AS other", 'PRAGMA table_info(orders)',
                    'SELECT name FROM sqlite_master', "SELECT load_extension('bad')",
                    'WITH RECURSIVE t(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM t) SELECT * FROM t']:
            with self.subTest(sql=sql):
                self.assertFalse(self.engine.run(sql)['ok'])
        self.assertEqual(self.engine.run('SELECT COUNT(*) FROM orders')['rows'], [[4]])

    def test_multiple_statements(self):
        self.assertFalse(self.engine.run('SELECT 1; SELECT 2')['ok'])

    def test_bad_field(self):
        self.assertFalse(self.engine.run('SELECT does_not_exist FROM orders')['ok'])

    def test_duplicate_alias(self):
        self.assertFalse(self.engine.run('SELECT sales AS a, profit AS a FROM orders')['ok'])

    def test_row_limit(self):
        self.engine.limits = replace(Limits(), rows=2)
        result = self.engine.run('SELECT sales FROM orders')
        self.assertTrue(result['truncated'])
        self.assertEqual(len(result['rows']), 2)

    def test_query_work_limit(self):
        self.engine.limits = replace(Limits(), vm_steps=1000)
        result = self.engine.run('SELECT COUNT(*) FROM orders a, orders b, orders c, orders d, orders e, orders f, orders g')
        self.assertFalse(result['ok'])

    def test_long_sql(self):
        self.assertFalse(self.engine.run('SELECT 1' + ' ' * 13000)['ok'])

    def test_character_cap(self):
        self.engine.limits = replace(Limits(), result_chars=10)
        result = self.engine.run('SELECT category FROM orders')
        self.assertTrue(result['truncated'])


class LoopTests(unittest.TestCase):
    def execute(self, directory, model=None, limits=None, passed=True):
        audit = Audit(directory)
        quality = {'passed': passed, 'errors': [] if passed else ['duplicate'], 'warnings': []}
        state = run_agent(fixture(), quality, 'Furniture', model or DemoModel('Furniture'),
                          limits or Limits(), audit, '分析盈利', 'demo')
        return state, audit.folder

    def test_demo_end_to_end(self):
        with tempfile.TemporaryDirectory() as directory:
            state, folder = self.execute(directory)
            self.assertEqual(state['status'], 'awaiting_human_review')
            self.assertEqual(state['api_requests'], 0)
            report = json.loads((folder / 'report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['core_metrics']['sales'], 350)
            self.assertEqual(report['data_grain'], '一条商品交易明细；row_id唯一，order_id可重复')
            self.assertTrue((folder / 'report.md').exists())
            self.assertFalse((folder / 'human_reviews.jsonl').exists())

    def test_quality_blocks_before_model(self):
        model = DemoModel('Furniture')
        with tempfile.TemporaryDirectory() as directory:
            state, folder = self.execute(directory, model=model, passed=False)
            self.assertEqual(state['status'], 'blocked_data_quality')
            self.assertEqual(model.requests, 0)
            self.assertFalse((folder / 'report.json').exists())

    def test_round_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            state, _ = self.execute(directory, limits=replace(Limits(), rounds=1))
            self.assertEqual(state['status'], 'round_limit')

    def test_tool_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            state, _ = self.execute(directory, limits=replace(Limits(), tool_calls=1))
            self.assertEqual(state['status'], 'tool_limit')

    def test_context_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            state, _ = self.execute(directory, limits=replace(Limits(), context_chars=10))
            self.assertEqual(state['status'], 'context_limit')

    def test_plain_text_is_not_accepted_as_report(self):
        model = DemoModel('Furniture')
        with patch.object(model, 'respond', return_value={'role': 'assistant', 'content': 'done'}):
            with tempfile.TemporaryDirectory() as directory:
                state, folder = self.execute(directory, model=model, limits=replace(Limits(), rounds=2))
                self.assertEqual(state['status'], 'round_limit')
                self.assertFalse((folder / 'report.json').exists())

    def test_bad_tool_arguments_do_not_crash(self):
        model = DemoModel('Furniture')
        msg = {'role': 'assistant', 'content': None, 'tool_calls': [
            {'id': 'bad', 'type': 'function', 'function': {'name': 'run_sql', 'arguments': '{bad'}}]}
        with patch.object(model, 'respond', return_value=msg):
            with tempfile.TemporaryDirectory() as directory:
                state, folder = self.execute(directory, model=model, limits=replace(Limits(), rounds=1))
                self.assertEqual(state['status'], 'round_limit')
                self.assertIn('error', (folder / 'events.jsonl').read_text(encoding='utf-8'))

    def test_reference_not_sent_to_model(self):
        model = DemoModel('Furniture')
        original = model.respond
        def capture(messages, tools):
            self.assertNotIn('reference_private', json.dumps(messages))
            self.assertNotIn('reference_sql', json.dumps(messages))
            if len(messages) == 2:
                self.assertNotIn('350', json.dumps(messages))
            return original(messages, tools)
        with patch.object(model, 'respond', side_effect=capture):
            with tempfile.TemporaryDirectory() as directory:
                self.assertEqual(self.execute(directory, model=model)[0]['status'], 'awaiting_human_review')

    def test_reject_bad_metric_and_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            _, folder = self.execute(directory)
            report = json.loads((folder / 'report.json').read_text(encoding='utf-8'))
            evidence = json.loads((folder / 'queries.json').read_text(encoding='utf-8'))
            reference = reference_metrics(fixture(), 'Furniture')
            self.assertFalse(validate_report(report, reference, evidence, 'Furniture'))
            report['core_metrics']['sales'] = 999
            self.assertTrue(validate_report(report, reference, evidence, 'Furniture'))
            report['core_metrics']['sales'] = 350
            report['facts'][0]['query_id'] = 'FAKE'
            self.assertTrue(validate_report(report, reference, evidence, 'Furniture'))

    def test_truncated_evidence_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            _, folder = self.execute(directory)
            report = json.loads((folder / 'report.json').read_text(encoding='utf-8'))
            evidence = json.loads((folder / 'queries.json').read_text(encoding='utf-8'))
            evidence['Q001']['truncated'] = True
            self.assertTrue(validate_report(report, reference_metrics(fixture(), 'Furniture'), evidence, 'Furniture'))

    def test_missing_or_wrong_data_grain_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            _, folder = self.execute(directory)
            report = json.loads((folder / 'report.json').read_text(encoding='utf-8'))
            evidence = json.loads((folder / 'queries.json').read_text(encoding='utf-8'))
            grain = report.pop('data_grain')
            errors = validate_report(report, reference_metrics(fixture(), 'Furniture'), evidence, 'Furniture')
            self.assertTrue(any('data_grain' in error for error in errors))
            report['data_grain'] = '每行一张订单'
            errors = validate_report(report, reference_metrics(fixture(), 'Furniture'), evidence, 'Furniture')
            self.assertTrue(any('data_grain' in error for error in errors))
            report['data_grain'] = grain
            self.assertFalse(validate_report(report, reference_metrics(fixture(), 'Furniture'), evidence, 'Furniture'))

    def test_unverified_profit_terminology_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            _, folder = self.execute(directory)
            report = json.loads((folder / 'report.json').read_text(encoding='utf-8'))
            evidence = json.loads((folder / 'queries.json').read_text(encoding='utf-8'))
            for term in ['毛利', '净利润']:
                with self.subTest(term=term):
                    report['summary'] = f'当前{term}表现。'
                    errors = validate_report(report, reference_metrics(fixture(), 'Furniture'), evidence, 'Furniture')
                    self.assertTrue(any('profit 口径未核对' in error for error in errors))

    def test_ranking_claim_requires_structured_comparison(self):
        with tempfile.TemporaryDirectory() as directory:
            _, folder = self.execute(directory)
            report = json.loads((folder / 'report.json').read_text(encoding='utf-8'))
            evidence = json.loads((folder / 'queries.json').read_text(encoding='utf-8'))
            report['facts'][1]['statement'] = 'Tables 的 profit 最低。'
            errors = validate_report(report, reference_metrics(fixture(), 'Furniture'), evidence, 'Furniture')
            self.assertTrue(any('comparison.column' in error for error in errors))

    def test_single_row_sql_max_fact_does_not_need_comparison(self):
        with tempfile.TemporaryDirectory() as directory:
            _, folder = self.execute(directory)
            report = json.loads((folder / 'report.json').read_text(encoding='utf-8'))
            evidence = json.loads((folder / 'queries.json').read_text(encoding='utf-8'))
            evidence['Q003'] = {
                'sql': ('SELECT AVG(discount) AS avg_discount, MIN(discount) AS min_discount, '
                        'MAX(discount) AS max_discount, COUNT(*) AS row_count FROM orders '
                        "WHERE category = 'Furniture' AND sub_category = 'Tables'"),
                'columns': ['avg_discount', 'min_discount', 'max_discount', 'row_count'],
                'rows': [[0.35, 0.3, 0.4, 2]],
                'truncated': False
            }
            report['facts'][1] = {
                'statement': 'Tables 子类最高折扣率为 0.4。',
                'claim_type': 'descriptive',
                'query_id': 'Q003', 'row': 0,
                'column': 'max_discount', 'value': 0.4
            }
            self.assertFalse(validate_report(
                report, reference_metrics(fixture(), 'Furniture'), evidence, 'Furniture'))

    def test_precise_money_counterfactual_rejected_without_causal_estimate(self):
        with tempfile.TemporaryDirectory() as directory:
            _, folder = self.execute(directory)
            report = json.loads((folder / 'report.json').read_text(encoding='utf-8'))
            evidence = json.loads((folder / 'queries.json').read_text(encoding='utf-8'))
            report['summary'] = '若取消 Tables 折扣，理论利润可提升约2.2万元。'
            errors = validate_report(report, reference_metrics(fixture(), 'Furniture'), evidence, 'Furniture')
            self.assertTrue(any('精确货币增量预测' in error for error in errors))
            report['summary'] = '当前只能进行观察性描述。'
            report['hypotheses'][0]['statement'] = '取消折扣后利润将增加 31001.78 元。'
            errors = validate_report(report, reference_metrics(fixture(), 'Furniture'), evidence, 'Furniture')
            self.assertTrue(any('精确货币增量预测' in error for error in errors))

    def test_real_q004_wrong_ranking_rows_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            _, folder = self.execute(directory)
            report = json.loads((folder / 'report.json').read_text(encoding='utf-8'))
            evidence = json.loads((folder / 'queries.json').read_text(encoding='utf-8'))
            evidence['Q004'] = {
                'columns': ['discount_bin', 'profit', 'margin'],
                'rows': [['61%+', -3894.9394, -1.5837065571757458],
                         ['41-60%', -21308.9653, -0.6435198451708481],
                         ['21-40%', -29273.8514, -0.18344516158856042]],
                'truncated': False
            }
            report['facts'][1] = {'statement': '41-60% 是 profit 最低的折扣组。',
                                  'claim_type': 'descriptive',
                                  'query_id': 'Q004', 'row': 1,
                                  'column': 'discount_bin', 'value': '41-60%',
                                  'comparison': {'column': 'profit', 'direction': 'min'}}
            errors = validate_report(report, reference_metrics(fixture(), 'Furniture'), evidence, 'Furniture')
            self.assertTrue(any('不是声明指标' in error for error in errors))
            report['facts'][1]['statement'] = '41-60% 是利润率最低的折扣组。'
            report['facts'][1]['comparison']['column'] = 'margin'
            errors = validate_report(report, reference_metrics(fixture(), 'Furniture'), evidence, 'Furniture')
            self.assertTrue(any('不是声明指标' in error for error in errors))

    def test_real_q004_correct_profit_and_margin_rankings_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            _, folder = self.execute(directory)
            report = json.loads((folder / 'report.json').read_text(encoding='utf-8'))
            evidence = json.loads((folder / 'queries.json').read_text(encoding='utf-8'))
            evidence['Q004'] = {
                'columns': ['discount_bin', 'profit', 'margin'],
                'rows': [['61%+', -3894.9394, -1.5837065571757458],
                         ['41-60%', -21308.9653, -0.6435198451708481],
                         ['21-40%', -29273.8514, -0.18344516158856042]],
                'truncated': False
            }
            report['facts'][1] = {'statement': '21-40% 是 profit 最低的折扣组。',
                                  'claim_type': 'descriptive',
                                  'query_id': 'Q004', 'row': 2,
                                  'column': 'discount_bin', 'value': '21-40%',
                                  'comparison': {'column': 'profit', 'direction': 'min'}}
            self.assertFalse(validate_report(report, reference_metrics(fixture(), 'Furniture'), evidence, 'Furniture'))
            report['facts'][1] = {'statement': '61%+ 是利润率最低的折扣组。',
                                  'claim_type': 'descriptive',
                                  'query_id': 'Q004', 'row': 0,
                                  'column': 'discount_bin', 'value': '61%+',
                                  'comparison': {'column': 'margin', 'direction': 'min'}}
            self.assertFalse(validate_report(report, reference_metrics(fixture(), 'Furniture'), evidence, 'Furniture'))

    def test_causal_claim_and_effect_estimate_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            _, folder = self.execute(directory)
            report = json.loads((folder / 'report.json').read_text(encoding='utf-8'))
            evidence = json.loads((folder / 'queries.json').read_text(encoding='utf-8'))
            report['facts'][0]['claim_type'] = 'causal'
            errors = validate_report(report, reference_metrics(fixture(), 'Furniture'), evidence, 'Furniture')
            self.assertTrue(any('claim_type' in error for error in errors))
            report['facts'][0]['claim_type'] = 'descriptive'
            report['causal_effect_estimated'] = True
            errors = validate_report(report, reference_metrics(fixture(), 'Furniture'), evidence, 'Furniture')
            self.assertTrue(any('causal_effect_estimated' in error for error in errors))

    def test_hypothesis_requires_unverified_status_and_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            _, folder = self.execute(directory)
            report = json.loads((folder / 'report.json').read_text(encoding='utf-8'))
            evidence = json.loads((folder / 'queries.json').read_text(encoding='utf-8'))
            report['hypotheses'][0]['status'] = 'confirmed'
            report['hypotheses'][0]['required_evidence'] = []
            errors = validate_report(report, reference_metrics(fixture(), 'Furniture'), evidence, 'Furniture')
            self.assertTrue(any('unverified' in error for error in errors))

    def test_execute_action_or_missing_human_decision_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            _, folder = self.execute(directory)
            report = json.loads((folder / 'report.json').read_text(encoding='utf-8'))
            evidence = json.loads((folder / 'queries.json').read_text(encoding='utf-8'))
            report['actions'][0]['action_type'] = 'execute'
            report['actions'][0]['requires_human_decision'] = False
            errors = validate_report(report, reference_metrics(fixture(), 'Furniture'), evidence, 'Furniture')
            self.assertTrue(any('人工决策' in error for error in errors))

    def test_secret_redaction(self):
        with tempfile.TemporaryDirectory() as directory:
            audit = Audit(directory, 'fake-secret')
            audit.event('test', {'message': 'fake-secret'})
            self.assertNotIn('fake-secret', (audit.folder / 'events.jsonl').read_text(encoding='utf-8'))

    def test_missing_api_key(self):
        with patch.dict('os.environ', {'DASHSCOPE_API_KEY': ''}):
            with self.assertRaises(ValueError):
                Bailian(Limits())


class ProviderTests(unittest.TestCase):
    """使用模拟HTTP响应测试协议，不触发真实网络或费用。"""
    def provider(self):
        with patch.dict('os.environ', {'DASHSCOPE_API_KEY': 'fake-test-key'}):
            return Bailian(Limits())

    def response(self, finish='stop'):
        return io.BytesIO(json.dumps({'choices': [{'finish_reason': finish, 'message':
                          {'role': 'assistant', 'content': 'OK'}}],
                          'usage': {'prompt_tokens': 12, 'completion_tokens': 1}}).encode())

    def test_request_and_usage(self):
        provider = self.provider()
        provider.opener.open = Mock(return_value=self.response())
        self.assertEqual(provider.respond([{'role': 'user', 'content': 'hello'}], [])['content'], 'OK')
        request = provider.opener.open.call_args.args[0]
        payload = json.loads(request.data)
        self.assertFalse(payload['enable_thinking'])
        self.assertNotIn('fake-test-key', request.data.decode())
        self.assertEqual(provider.usage[0]['prompt_tokens'], 12)

    def test_401_not_retried_or_secret_exposed(self):
        provider = self.provider()
        provider.opener.open = Mock(side_effect=urllib.error.HTTPError('https://example.invalid', 401, 'bad', {}, None))
        with self.assertRaisesRegex(RuntimeError, '401') as caught:
            provider.respond([], [])
        self.assertNotIn('fake-test-key', str(caught.exception))
        self.assertEqual(provider.requests, 1)

    def test_429_retry_once(self):
        provider = self.provider()
        provider.opener.open = Mock(side_effect=[
            urllib.error.HTTPError('https://example.invalid', 429, 'busy', {}, None), self.response()])
        with patch('agent_b.providers.time.sleep'):
            self.assertEqual(provider.respond([], [])['content'], 'OK')
        self.assertEqual(provider.requests, 2)

    def test_timeout_not_retried(self):
        provider = self.provider()
        provider.opener.open = Mock(side_effect=TimeoutError())
        with self.assertRaisesRegex(RuntimeError, '请求超时.*120秒') as caught:
            provider.respond([], [])
        self.assertNotIn('fake-test-key', str(caught.exception))
        self.assertEqual(provider.requests, 1)

    def test_urlerror_wrapped_timeout_not_retried(self):
        provider = self.provider()
        provider.opener.open = Mock(side_effect=urllib.error.URLError(TimeoutError('private detail')))
        with self.assertRaisesRegex(RuntimeError, '网络异常包装的超时') as caught:
            provider.respond([], [])
        self.assertNotIn('private detail', str(caught.exception))
        self.assertEqual(provider.requests, 1)

    def test_non_timeout_urlerror_is_connection_failure_not_retried(self):
        provider = self.provider()
        provider.opener.open = Mock(side_effect=urllib.error.URLError('private DNS detail'))
        with self.assertRaisesRegex(RuntimeError, '连接失败.*非已识别超时') as caught:
            provider.respond([], [])
        self.assertNotIn('private DNS detail', str(caught.exception))
        self.assertEqual(provider.requests, 1)

    def test_non_timeout_oserror_is_connection_failure_not_retried(self):
        provider = self.provider()
        provider.opener.open = Mock(side_effect=OSError('private socket detail'))
        with self.assertRaisesRegex(RuntimeError, '本地网络或套接字错误') as caught:
            provider.respond([], [])
        self.assertNotIn('private socket detail', str(caught.exception))
        self.assertEqual(provider.requests, 1)

    def test_truncated_response_rejected(self):
        provider = self.provider()
        provider.opener.open = Mock(return_value=self.response('length'))
        with self.assertRaises(RuntimeError):
            provider.respond([], [])

    def test_bad_json_rejected(self):
        provider = self.provider()
        provider.opener.open = Mock(return_value=io.BytesIO(b'not-json'))
        with self.assertRaises(RuntimeError):
            provider.respond([], [])


if __name__ == '__main__':
    unittest.main()
