"""模型适配层：在线百炼 Chat Completions 与离线脚本模拟器。

仅这个模块向云端发请求。密钥从环境变量读取，不写入日志；禁止跨主机重定向。
离线模拟固定调用序列，只测试软件流程，不能测量 LLM 自主性或能力。
"""
import json
import os
import socket
import time
import urllib.error
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError('拒绝 HTTP 重定向，避免凭证发往非预期主机。')


class Bailian:
    ENDPOINT = 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'

    def __init__(self, limits):
        self.key = os.environ.get('DASHSCOPE_API_KEY', '').strip()
        if not self.key:
            raise ValueError('未配置 DASHSCOPE_API_KEY；请阅读配置指南，不要把密钥发到聊天里。')
        self.model = os.environ.get('BAILIAN_MODEL', 'qwen-plus')
        self.limits = limits
        self.usage = []
        self.requests = 0
        self.opener = urllib.request.build_opener(NoRedirect())

    def respond(self, messages, tools):
        payload = {'model': self.model, 'messages': messages, 'stream': False,
                   'enable_thinking': False, 'max_tokens': self.limits.output_tokens}
        if tools:
            payload.update(tools=tools, tool_choice='auto')
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        # 仅429与5xx重试一次。连接超时不自动重发，因为无法确认服务端是否已经计费。
        for attempt in range(2):
            request = urllib.request.Request(self.ENDPOINT, data=body, headers={
                'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/json'})
            try:
                self.requests += 1
                with self.opener.open(request, timeout=self.limits.request_seconds) as response:
                    raw = response.read(1_000_001)
                if len(raw) > 1_000_000:
                    raise RuntimeError('模型响应过大。')
                data = json.loads(raw)
                self.usage.append(data.get('usage', {}))
                choice = data['choices'][0]
                if choice.get('finish_reason') in ['length', 'content_filter']:
                    raise RuntimeError('响应被截断或被平台过滤；本次停止，不接受半份报告。')
                msg = choice['message']
                # 非思考模式仅保存协议所需字段，不收集隐藏推理。
                return {k: msg[k] for k in ['role', 'content', 'tool_calls'] if k in msg}
            except urllib.error.HTTPError as exc:
                if attempt == 0 and (exc.code == 429 or 500 <= exc.code < 600):
                    time.sleep(1)
                    continue
                raise RuntimeError(f'模型 HTTP {exc.code}；请按排错指南核实密钥/地域/模型/额度。') from None
            except urllib.error.URLError as exc:
                if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                    raise RuntimeError(
                        f'模型请求超时（网络异常包装的超时，上限{self.limits.request_seconds}秒）；'
                        '本次不自动重试，请先在平台确认请求状态和计费。') from None
                raise RuntimeError(
                    '模型连接失败（网络定位或建立连接阶段，非已识别超时）；'
                    '请检查网络与服务可用性，本次不自动重试。') from None
            except (TimeoutError, socket.timeout):
                raise RuntimeError(
                    f'模型请求超时（上限{self.limits.request_seconds}秒）；'
                    '本次不自动重试，请先在平台确认请求状态和计费。') from None
            except OSError:
                raise RuntimeError(
                    '模型连接失败（本地网络或套接字错误，非已识别超时）；'
                    '请检查网络与服务可用性，本次不自动重试。') from None
            except (KeyError, IndexError, json.JSONDecodeError, TypeError):
                raise RuntimeError('平台响应结构不符合约定，请检查模型和接口兼容性。') from None


class DemoModel:
    """确定性模拟器：按阶段产生工具请求，结果来自真实 SQLite，不是编造固定金额。"""
    model = 'OFFLINE_SCRIPT_NOT_LLM'

    def __init__(self, category):
        self.category, self.step = category, 0
        self.usage, self.requests = [], 0

    def respond(self, messages, tools):
        self.step += 1
        self.requests += 1
        scope = '' if self.category == 'ALL' else " WHERE category = '" + self.category.replace("'", "''") + "'"
        if self.step == 1:
            name, arguments = 'get_context', {}
        elif self.step == 2:
            name = 'run_sql'
            arguments = {'sql': 'SELECT SUM(sales) AS sales, SUM(profit) AS profit, '
                         'SUM(profit)/NULLIF(SUM(sales),0) AS profit_margin, '
                         'COUNT(DISTINCT order_id) AS order_count FROM orders' + scope}
        elif self.step == 3:
            name = 'run_sql'
            arguments = {'sql': 'SELECT sub_category, SUM(sales) AS sales, SUM(profit) AS profit '
                         'FROM orders' + scope + ' GROUP BY sub_category ORDER BY profit, sub_category'}
        else:
            results = [json.loads(m['content']) for m in messages if m.get('role') == 'tool']
            queries = [r for r in results if r.get('ok') and 'query_id' in r]
            if len(queries) < 2:
                raise RuntimeError('模拟演示缺少查询结果。')
            overall, detail = queries[0], queries[1]
            metrics = dict(zip(overall['columns'], overall['rows'][0]))
            name, arguments = 'submit_report', {'report': {
                'title': '离线流程演示（不是 LLM 分析）', 'scope': self.category, 'status': 'complete',
                'data_grain': '一条商品交易明细；row_id唯一，order_id可重复',
                'evidence_level': 'observational_descriptive', 'causal_effect_estimated': False,
                'summary': '演示自主接口的固定调用序列；不能据此评价真实模型质量。',
                'core_metrics': metrics,
                'facts': [{'statement': '范围内总利润', 'claim_type': 'descriptive',
                           'query_id': overall['query_id'], 'row': 0,
                           'column': 'profit', 'value': metrics['profit']},
                          {'statement': '按利润升序的首个子品类，需结合规模核查',
                           'claim_type': 'descriptive',
                           'query_id': detail['query_id'], 'row': 0,
                           'column': 'sub_category', 'value': detail['rows'][0][0]}],
                'hypotheses': [{'statement': '折扣与产品组合可能有关。',
                                'status': 'unverified',
                                'required_evidence': ['按折扣与子品类交叉的观察性汇总']}],
                'actions': [{'description': '人工复算关键指标，再使用真实模型进行下钻评测。',
                             'action_type': 'validate', 'requires_human_decision': True}],
                'limitations': ['静态历史样本；离线模拟不是模型评测；无干预实验。']}}
        return {'role': 'assistant', 'content': None, 'tool_calls': [
            {'id': f'demo_{self.step}', 'type': 'function', 'function':
             {'name': name, 'arguments': json.dumps(arguments, ensure_ascii=False)}}]}
