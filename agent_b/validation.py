"""验证层：核验核心指标与证据单元格，不声称验证全部文本语义或因果结论。"""
import math
import re
from .contracts import DATA_GRAIN


RANKING_TERMS = ('最高', '最低', '最大', '最小', '最多', '最少', '最严重')
HIGH_RANKING_TERMS = ('最高', '最大', '最多')
LOW_RANKING_TERMS = ('最低', '最小', '最少')
UNVERIFIED_PROFIT_TERMS = ('毛利', '净利润')
UNSUPPORTED_MONEY_EFFECT = re.compile(
    r'(?:提升|增加|净增|收益|多赚|利润可达|利润将).{0,24}?'
    r'[-+]?[0-9]+(?:\.[0-9]+)?\s*(?:元|万元)')


def same(actual, expected, tolerance=1e-6):
    if expected is None:
        return actual is None
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return (isinstance(actual, (int, float)) and not isinstance(actual, bool)
                and math.isfinite(actual) and math.isclose(actual, expected, rel_tol=1e-9, abs_tol=tolerance))
    return type(actual) is type(expected) and actual == expected


def comparison_error(fact, entry):
    """排名主张必须声明比较列和方向；使用整个未截断结果集核验。"""
    statement = fact['statement']
    comparison = fact.get('comparison')
    if not any(term in statement for term in RANKING_TERMS) and comparison is None:
        return None
    # SQL 已用 MIN/MAX 算出的单行标量不是返回行之间的排名，无需再提供 comparison。
    if comparison is None and len(entry.get('rows', [])) == 1:
        column = fact.get('column', '')
        sql = entry.get('sql', '').lower()
        high = any(term in statement for term in HIGH_RANKING_TERMS)
        low = any(term in statement for term in LOW_RANKING_TERMS)
        aggregate = 'max' if high and not low else ('min' if low and not high else None)
        if aggregate and isinstance(column, str):
            alias_pattern = rf'\b{aggregate}\s*\([^)]*\)\s+as\s+[\["`]?{re.escape(column.lower())}[\]"`]?\b'
            if re.search(alias_pattern, sql):
                return None
    if (not isinstance(comparison, dict) or set(comparison) != {'column', 'direction'}
            or comparison['direction'] not in ['min', 'max']):
        return '排名主张必须提供 comparison.column 和 min/max 方向。'
    try:
        index = entry['columns'].index(comparison['column'])
        values = [row[index] for row in entry['rows']]
        if (not values or not all(isinstance(value, (int, float)) and not isinstance(value, bool)
                                  and math.isfinite(value) for value in values)):
            return 'comparison.column 必须是每行均有有限数值的列。'
        expected = min(values) if comparison['direction'] == 'min' else max(values)
        actual = entry['rows'][fact['row']][index]
        if not same(actual, expected):
            return '引用行不是声明指标和方向的极值行。'
    except (KeyError, ValueError, IndexError, TypeError):
        return 'comparison.column 不存在或排名证据无效。'
    return None


def validate_report(report, reference, evidence, category):
    """只返回错误名称，不向模型泄露参考数值，避免把评测变成抄答案。"""
    errors = []
    if not isinstance(report, dict):
        return ['report 必须是对象。']
    for key in ['title', 'scope', 'status', 'summary']:
        if not isinstance(report.get(key), str) or not report[key].strip():
            errors.append(f'{key} 必须是非空字符串。')
    if report.get('scope') != category:
        errors.append('scope 与本次固定 category 不一致。')
    if report.get('status') not in ['complete', 'needs_human']:
        errors.append('status 只能为 complete 或 needs_human。')
    if report.get('data_grain') != DATA_GRAIN:
        errors.append('data_grain 必须与 get_context 的商品交易明细粒度完全一致。')
    if report.get('evidence_level') != 'observational_descriptive':
        errors.append('evidence_level 必须为 observational_descriptive。')
    if report.get('causal_effect_estimated') is not False:
        errors.append('causal_effect_estimated 必须为 false；当前工具没有因果识别。')
    hypotheses = report.get('hypotheses')
    if not isinstance(hypotheses, list):
        errors.append('hypotheses 必须是结构化对象列表。')
    else:
        for i, item in enumerate(hypotheses):
            valid = (isinstance(item, dict)
                     and set(item) == {'statement', 'status', 'required_evidence'}
                     and isinstance(item['statement'], str) and item['statement'].strip()
                     and item['status'] == 'unverified'
                     and isinstance(item['required_evidence'], list) and item['required_evidence']
                     and all(isinstance(x, str) and x.strip() for x in item['required_evidence']))
            if not valid:
                errors.append(f'hypotheses[{i}] 必须标记 unverified 并列出所需证据。')
    actions = report.get('actions')
    if not isinstance(actions, list):
        errors.append('actions 必须是结构化对象列表。')
    else:
        for i, item in enumerate(actions):
            valid = (isinstance(item, dict)
                     and set(item) == {'description', 'action_type', 'requires_human_decision'}
                     and isinstance(item['description'], str) and item['description'].strip()
                     and item['action_type'] in ['investigate', 'validate', 'experiment']
                     and item['requires_human_decision'] is True)
            if not valid:
                errors.append(f'actions[{i}] 只允许核查/验证/试验，且必须保留人工决策。')
    limitations = report.get('limitations')
    if not isinstance(limitations, list) or not all(isinstance(x, str) and x.strip() for x in limitations):
        errors.append('limitations 必须是字符串列表。')
    if not report.get('limitations'):
        errors.append('必须说明数据与结论限制。')
    text_fields = [report.get('title', ''), report.get('summary', '')]
    text_fields += [fact.get('statement', '') for fact in report.get('facts', [])
                    if isinstance(fact, dict)] if isinstance(report.get('facts'), list) else []
    if isinstance(hypotheses, list):
        text_fields += [item.get('statement', '') for item in hypotheses if isinstance(item, dict)]
        text_fields += [text for item in hypotheses if isinstance(item, dict)
                        for text in item.get('required_evidence', []) if isinstance(text, str)]
    if isinstance(actions, list):
        text_fields += [item.get('description', '') for item in actions if isinstance(item, dict)]
    if isinstance(limitations, list):
        text_fields += [item for item in limitations if isinstance(item, str)]
    if any(term in text for term in UNVERIFIED_PROFIT_TERMS for text in text_fields):
        errors.append('profit 口径未核对，报告不得称为毛利或净利润。')
    if report.get('causal_effect_estimated') is False:
        guarded_text = [report.get('summary', '')]
        guarded_text += [item.get('statement', '') for item in hypotheses if isinstance(item, dict)] \
            if isinstance(hypotheses, list) else []
        guarded_text += [item.get('description', '') for item in actions if isinstance(item, dict)] \
            if isinstance(actions, list) else []
        if any(UNSUPPORTED_MONEY_EFFECT.search(text) for text in guarded_text):
            errors.append('未估计因果效应时，不得在摘要、假设或行动中给出精确货币增量预测。')
    metrics = report.get('core_metrics')
    if not isinstance(metrics, dict):
        errors.append('缺少 core_metrics 对象。')
    else:
        for key, expected in reference.items():
            tolerance = 0.01 if key in ['sales', 'profit'] else (0 if key == 'order_count' else 1e-6)
            if key not in metrics or not same(metrics[key], expected, tolerance):
                errors.append(f'核心指标 {key} 校验失败，请核实范围与计算。')
    facts = report.get('facts')
    if not isinstance(facts, list) or not facts:
        errors.append('facts 至少一项。')
        return errors
    for i, fact in enumerate(facts):
        try:
            if not isinstance(fact['statement'], str) or not fact['statement'].strip():
                raise ValueError()
            if fact.get('claim_type') not in ['descriptive', 'association']:
                errors.append(f'facts[{i}] claim_type 只能为 descriptive 或 association。')
            entry = evidence[fact['query_id']]
            row = fact['row']
            if entry['truncated'] or type(row) is not int or row < 0:
                raise ValueError()
            value = entry['rows'][row][entry['columns'].index(fact['column'])]
            if not same(fact['value'], value):
                raise ValueError()
            rank_error = comparison_error(fact, entry)
            if rank_error:
                errors.append(f'facts[{i}] {rank_error}')
        except (KeyError, ValueError, IndexError, TypeError):
            errors.append(f'facts[{i}] 证据引用、类型或数值不匹配。')
    return errors
