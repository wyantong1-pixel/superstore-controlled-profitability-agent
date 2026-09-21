"""配置层：不保存密钥；集中定义可审计的运行上限，不冒充生产级安全沙箱。"""
from dataclasses import dataclass, asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Limits:
    """不可由模型修改的边界；金额预算仍需平台账户配合限制。"""
    rounds: int = 10
    tool_calls: int = 18
    rows: int = 100
    result_chars: int = 24000
    sql_chars: int = 12000
    query_seconds: float = 2.0
    vm_steps: int = 2_000_000
    request_seconds: int = 120
    context_chars: int = 100000
    output_tokens: int = 3500

    def public(self):
        return asdict(self)
