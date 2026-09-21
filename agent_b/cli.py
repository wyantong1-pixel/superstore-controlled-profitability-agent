"""命令行入口：check/demo/run/ping/review。默认无云调用；在线必须显式 --allow-cloud。

所有产物在 runs/新目录，原始 CSV 不修改。人工审核由命令行明确执行，模型没有审核工具。
"""
import argparse
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
from .audit import Audit, digest
from .config import ROOT, Limits
from .controller import run_agent
from .data import load_checked
from .providers import Bailian, DemoModel
from .contracts import SYSTEM
import hashlib


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['check', 'demo', 'run', 'ping', 'review'])
    parser.add_argument('--data', type=Path, default=ROOT / 'data' / 'Sample - Superstore.csv')
    parser.add_argument('--category', default='Furniture', help='真实品类名称，或 ALL')
    parser.add_argument('--question', default='分析该品类盈利表现，定位值得优先排查的子品类、地区或折扣区间，区分事实、假设和建议。')
    parser.add_argument('--allow-cloud', action='store_true', help='确认问题、数据说明和查询结果可发送至百炼')
    parser.add_argument('--run-dir', type=Path)
    parser.add_argument('--decision', choices=['approve', 'revise', 'reject'])
    parser.add_argument('--reviewer')
    parser.add_argument('--note')
    args = parser.parse_args()
    limits = Limits()
    try:
        if args.command == 'review':
            if not all([args.run_dir, args.decision, args.reviewer, args.note]):
                raise ValueError('review 需要 --run-dir --decision --reviewer --note。')
            folder = args.run_dir.resolve()
            if not folder.is_relative_to((ROOT / 'runs').resolve()):
                raise ValueError('只能审核当前项目 runs 下的运行。')
            report = folder / 'report.json'
            state = json.loads((folder / 'state.json').read_text(encoding='utf-8'))
            if not report.exists() or state['status'] not in ['awaiting_human_review', 'needs_human']:
                raise ValueError('没有通过有限自动校验、可供审核的报告。')
            record = {'reviewer': args.reviewer, 'decision': args.decision, 'note': args.note,
                      'report_sha256': digest(report), 'time_utc': datetime.now(timezone.utc).isoformat()}
            with (folder / 'human_reviews.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + '\n')
            print('已追加审核记录；原报告不修改，approve 也不会触发任何业务操作。')
            return 0
        if args.command in ['run', 'ping'] and not args.allow_cloud:
            raise ValueError('在线运行须加 --allow-cloud，确认云端数据传输和可能产生的调用费用。')
        if args.command == 'ping':
            model = Bailian(limits)
            model.respond([{'role': 'user', 'content': 'Reply OK.'}], [])
            print('API 连通性测试成功。未发送销售数据；这不代表工具调用和业务任务已验收。')
            return 0
        df, quality = load_checked(args.data)
        if args.command == 'check':
            print(json.dumps(quality, ensure_ascii=False, indent=2))
            if quality['passed']:
                print('可选 category：', ', '.join(sorted(df.category.unique())))
            return 0 if quality['passed'] else 2
        # 质量失败仍保存阻断记录，且无需API密钥，更不会联网。
        model = Bailian(limits) if args.command == 'run' and quality['passed'] else DemoModel(args.category)
        audit = Audit(ROOT / 'runs', getattr(model, 'key', ''))
        audit.save('manifest.json', {'mode': args.command, 'model': model.model,
                   'source_sha256': quality['sha256'], 'category': args.category, 'question': args.question,
                   'limits': limits.public(), 'python': platform.python_version(), 'pandas': pd.__version__,
                   'system_prompt_sha256': hashlib.sha256(SYSTEM.encode()).hexdigest(),
                   'source_files': {p.name: digest(p) for p in sorted((ROOT / 'agent_b').glob('*.py'))},
                   'cloud_transmission_acknowledged': args.allow_cloud,
                   'disclaimer': 'demo是固定脚本模拟；run才调用LLM。参考答案仅保存在本地验证侧。'})
        state = run_agent(df, quality, args.category, model, limits, audit, args.question, args.command)
        print('状态：', state['status'])
        if 'error' in state:
            print('原因：', state['error'])
        print('结果目录：', audit.folder)
        return 0 if state['status'] in ['awaiting_human_review', 'needs_human'] else 2
    except (ValueError, OSError, RuntimeError, json.JSONDecodeError) as exc:
        print('未完成：', str(exc))
        return 2

