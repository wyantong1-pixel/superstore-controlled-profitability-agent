"""数据层：规范化 CSV、强制质量检查、最小化列、独立 Pandas 参考计算。

不自动去重或补零：异常先阻断，由人判断，而不是悄悄改变业务数据。
参考计算只供验证器使用，不能作为模型输入或工具返回。
"""
import hashlib
import re
from pathlib import Path
import numpy as np
import pandas as pd

FIELDS = ['row_id', 'order_id', 'order_date', 'category', 'sub_category',
          'region', 'segment', 'product_id', 'sales', 'profit', 'discount', 'quantity']
NUMBERS = ['sales', 'profit', 'discount', 'quantity']


def load_checked(path):
    """返回 (最小化数据, 质量报告)。读取失败抛异常；质量失败由控制层禁止入库。"""
    path = Path(path)
    if path.stat().st_size > 30_000_000:
        raise ValueError('Demo 仅接收不超过 30 MB 的 CSV。')
    raw = pd.read_csv(path, encoding='latin1')
    raw.columns = [re.sub(r'[^a-z0-9]+', '_', c.lower()).strip('_') for c in raw.columns]
    errors, warnings = [], []
    if raw.columns.duplicated().any():
        errors.append('规范化后出现重名列。')
    missing = sorted(set(FIELDS) - set(raw.columns))
    if missing:
        errors.append('缺少字段：' + ', '.join(missing))
    report = {'passed': False, 'rows': len(raw), 'errors': errors, 'warnings': warnings,
              'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
              'note': '日期覆盖不等于上游完整性；本项目不能证明没有漏采数据。'}
    if errors:
        return pd.DataFrame(), report
    df = raw[FIELDS].copy()  # 不向查询环境提供姓名、地址、邮编等无关信息。
    if df.empty:
        errors.append('没有数据。')
    for col in FIELDS:
        if df[col].isna().any() or df[col].astype(str).str.strip().eq('').any():
            errors.append(f'{col} 存在缺失或空字符串。')
    for col in NUMBERS:
        df[col] = pd.to_numeric(df[col], errors='coerce')
        if not np.isfinite(df[col]).all():
            errors.append(f'{col} 包含非数值、NaN 或无穷值。')
    dates = pd.to_datetime(df['order_date'], format='%m/%d/%Y', errors='coerce')
    if dates.isna().any():
        errors.append('order_date 不是预期的月/日/年格式；请核实源数据而非猜测日期。')
    df['order_date'] = dates.dt.strftime('%Y-%m-%d')
    if not dates.dropna().empty:
        report['date_min'] = dates.min().strftime('%Y-%m-%d')
        report['date_max'] = dates.max().strftime('%Y-%m-%d')
    if df['row_id'].duplicated().any():
        errors.append('row_id 重复：可能重复导入明细，不能按 order_id 去重修复。')
    if ((df['discount'] < 0) | (df['discount'] > 1)).any():
        errors.append('discount 超出 [0,1]。')
    if (df['sales'] < 0).any():
        errors.append('sales 出现负数，当前样本规则不支持退款记录，需人工核实。')
    if ((df['quantity'] <= 0) | (df['quantity'] % 1 != 0)).any():
        errors.append('quantity 不是正整数，需核实粒度和业务规则。')
    if (df['sales'] == 0).any():
        warnings.append('存在零销售额；利润率分母为零时应返回 null。')
    warnings.append('负利润允许；order_id 重复允许；不保证全量源数据完整。')
    report['passed'] = not errors
    return df, report


def reference_metrics(df, category):
    """独立 Pandas 聚合；不使用 Agent SQL。独立实现不等于绝对正确，仍需抽样复算。"""
    part = df if category == 'ALL' else df[df['category'] == category]
    if part.empty:
        raise ValueError('该 category 没有数据，请使用检查结果中的真实类别。')
    sales, profit = float(part.sales.sum()), float(part.profit.sum())
    return {'sales': sales, 'profit': profit, 'profit_margin': profit / sales if sales else None,
            'order_count': int(part.order_id.nunique())}

