"""审计层：每次运行独立目录，记录元数据、工具结果、校验和人审；不记录密钥。

这些是本地教学审计文件，不是防篡改合规日志。发布前要人工脱敏。
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Audit:
    def __init__(self, root, secret=''):
        self.folder = Path(root) / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ_') + uuid4().hex[:8])
        self.folder.mkdir(parents=True, exist_ok=False)
        self.secret = secret

    def _json(self, obj):
        text = json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False)
        return text.replace(self.secret, '[REDACTED]') if self.secret else text

    def save(self, name, obj):
        (self.folder / name).write_text(self._json(obj), encoding='utf-8')

    def event(self, kind, payload):
        record = {'time_utc': datetime.now(timezone.utc).isoformat(), 'type': kind, 'payload': payload}
        text = self._json(record)
        # 每行一个JSON对象，方便检索；不是把敏感 HTTP 请求头写入日志。
        with (self.folder / 'events.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(json.loads(text), ensure_ascii=False) + '\n')


def render_report(folder, report, mode):
    """输出人读 Markdown；保留结构化 JSON 为主记录，不声称HTML安全渲染。"""
    lines = ['# ' + report['title'], '', '**待人工审核，不是已批准的业务决策。**', '',
             f'运行模式：{mode}；范围：{report["scope"]}；模型申报状态：{report["status"]}', '',
             f'数据粒度：{report["data_grain"]}',
             f'证据等级：{report["evidence_level"]}；已估计因果效应：{report["causal_effect_estimated"]}',
             '', report['summary'], '', '## 核心指标', '']
    for key, value in report['core_metrics'].items():
        lines.append(f'- {key}: {value}')
    lines += ['', '## 查询证据事实', '']
    for fact in report['facts']:
        comparison = fact.get('comparison')
        rank_note = (f'；排名校验 {comparison["column"]} {comparison["direction"]}'
                     if isinstance(comparison, dict) else '')
        lines.append(f'- {fact["statement"]}：{fact["value"]} '
                     f'〔{fact["query_id"]}，第 {fact["row"]} 行，{fact["column"]} 列{rank_note}〕')
    lines += ['', '## 待验证假设', '']
    for item in report['hypotheses']:
        lines.append(f'- {item["statement"]}（{item["status"]}；所需证据：'
                     + '；'.join(item['required_evidence']) + '）')
    lines += ['', '## 建议的下一步', '']
    for item in report['actions']:
        lines.append(f'- [{item["action_type"]}；需人工决策={item["requires_human_decision"]}] '
                     + item['description'])
    lines += ['', '## 限制', ''] + ['- ' + x for x in report['limitations']]
    lines += ['', '自动检查仅覆盖结构、指定核心指标、证据单元格；文字含义与因果边界仍需人工复核。', '']
    Path(folder, 'report.md').write_text('\n'.join(lines), encoding='utf-8')
