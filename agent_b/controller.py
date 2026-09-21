"""控制层：强制数据门禁→模型/工具循环→独立校验→等待人审。

没有任意代码执行；工具参数在本地再次校验。达到上限或出错不伪造成功报告。
"""
import json
import time
from .audit import render_report
from .contracts import SYSTEM, TOOLS, context
from .data import reference_metrics
from .database import QueryEngine
from .validation import validate_report


def run_agent(df, quality, category, model, limits, audit, question, mode):
    """model.respond 是依赖注入点：同一控制器可接真实API或模拟器，便于无费用测试。"""
    start = time.monotonic()
    state = {'status': 'started', 'mode': mode, 'model': model.model, 'category': category,
             'tool_calls': 0, 'rounds': 0}
    audit.save('quality.json', quality)
    engine = None
    try:
        if not quality['passed']:
            state['status'] = 'blocked_data_quality'
            return state
        reference = reference_metrics(df, category)
        audit.save('reference_private.json', reference)  # 只在本地验证；绝不放进messages。
        engine = QueryEngine(df, limits)
        messages = [{'role': 'system', 'content': SYSTEM},
                    {'role': 'user', 'content': f'固定 category={category}。问题：{question}'}]
        context_read = False
        seen_ids = set()
        for turn in range(limits.rounds):
            state['rounds'] = turn + 1
            if len(json.dumps(messages, ensure_ascii=False)) > limits.context_chars:
                state['status'] = 'context_limit'
                break
            response = model.respond(messages, TOOLS)
            if not isinstance(response, dict) or response.get('role') != 'assistant':
                raise ValueError('模型返回了非法消息结构。')
            calls = response.get('tool_calls') or []
            if not isinstance(calls, list):
                raise ValueError('tool_calls 必须为列表。')
            audit.event('assistant', response)
            messages.append(response)
            if not calls:
                messages.append({'role': 'user', 'content': '请通过工具获取真实证据，完成后调用 submit_report。'})
                continue
            # 保证消息协议：整批调用超限时停止，不把半批未完成工具消息送回模型。
            if state['tool_calls'] + len(calls) > limits.tool_calls:
                state['status'] = 'tool_limit'
                break
            for call in calls:
                if not isinstance(call, dict) or not isinstance(call.get('id'), str) or call['id'] in seen_ids:
                    raise ValueError('工具调用ID缺失或重复。')
                seen_ids.add(call['id'])
                state['tool_calls'] += 1
                approved = None
                name = call.get('function', {}).get('name')
                try:
                    if call.get('type') != 'function':
                        raise ValueError('只支持 function 类型。')
                    args = json.loads(call['function']['arguments'])
                    required = {'get_context': set(), 'run_sql': {'sql'}, 'submit_report': {'report'}}
                    if name not in required or not isinstance(args, dict) or set(args) != required[name]:
                        raise ValueError('未知工具或参数名不符合工具契约。')
                    if name == 'get_context':
                        context_read = True
                        result = context(category, quality)
                    elif not context_read:
                        result = {'ok': False, 'error': '必须先读取 get_context。'}
                    elif name == 'run_sql':
                        result = engine.run(args['sql'])
                    else:
                        errors = validate_report(args['report'], reference, engine.evidence, category)
                        if len(engine.evidence) < 2:
                            errors.append('至少完成整体指标与一个下钻查询。')
                        audit.event('report_validation', {'errors': errors, 'candidate': args['report']})
                        result = {'ok': not errors, 'errors': errors,
                                  'note': '通过仅代表有限自动校验；仍需人工审核。'}
                        if not errors:
                            approved = args['report']
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    result = {'ok': False, 'error': str(exc)[:300]}
                audit.event('tool', {'call_id': call['id'], 'name': name, 'result': result})
                messages.append({'role': 'tool', 'tool_call_id': call['id'],
                                 'content': json.dumps(result, ensure_ascii=False, allow_nan=False)})
                if approved is not None:
                    audit.save('report.json', approved)
                    render_report(audit.folder, approved, mode)
                    state['status'] = 'needs_human' if approved['status'] == 'needs_human' else 'awaiting_human_review'
                    return state
        else:
            state['status'] = 'round_limit'
    except Exception as exc:
        # 不打印堆栈/HTTP头；真实网络异常已由适配层转成无密钥的说明。
        state.update(status='failed', error=str(exc)[:500])
        audit.event('failure', {'type': type(exc).__name__, 'error': state['error']})
    finally:
        if engine:
            audit.save('queries.json', engine.evidence)
            engine.close()
        state['elapsed_seconds'] = round(time.monotonic() - start, 3)
        state['provider_requests'] = model.requests
        # 模拟器也有交互次数，但不能把它计为真实API请求或费用。
        state['api_requests'] = model.requests if mode == 'run' else 0
        state['usage'] = model.usage
        audit.save('state.json', state)
    return state
