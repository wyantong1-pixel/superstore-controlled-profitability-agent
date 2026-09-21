"""受限 SQLite 查询工具：引擎授权、query_only、函数白名单、资源与返回量限制。

不靠关键词黑名单充当安全边界；不执行 eval/exec，不给模型 shell、文件或网络工具。
内存库也不是 OS 沙箱：只适用于本地公开小样本，不直接接生产敏感数据。
"""
import json
import math
import sqlite3
import time

ALLOWED_FUNCTIONS = set('sum total count avg min max round nullif coalesce ifnull abs '
                        'lower upper length substr substring trim ltrim rtrim '
                        'date strftime julianday'.split())


class QueryEngine:
    def __init__(self, df, limits):
        self.limits = limits
        self.connection = sqlite3.connect(':memory:')
        # 即便小样本金额恰好全是整数，也必须使用REAL，否则SQLite整数除法会截断利润率。
        df.to_sql('orders', self.connection, index=False,
                  dtype={'sales': 'REAL', 'profit': 'REAL', 'discount': 'REAL'})
        self.connection.execute('PRAGMA query_only = ON')
        self.connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 200000)
        self.connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, limits.sql_chars)
        self.connection.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 64)
        self.connection.setlimit(sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 40)
        self.connection.set_authorizer(self._authorize)
        self.evidence = {}
        self.counter = 0

    @staticmethod
    def _authorize(action, arg1, arg2, database, trigger):
        """SQLite 在编译语句时授权；未明确允许的操作一律拒绝。"""
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ and arg1 == 'orders' and (
                database == 'main' or (database is None and arg2 == '')):
            # SQLite 的 COUNT(*) 授权回调可能不带数据库名，仅允许这个特定空列读取。
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION and (arg2 or '').lower() in ALLOWED_FUNCTIONS:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    def run(self, sql):
        """成功结果附证据编号；截断结果可探索，但不能作为最终事实引用。"""
        if not isinstance(sql, str) or not sql.strip() or len(sql) > self.limits.sql_chars:
            return {'ok': False, 'error': 'sql 必须为非空字符串且不超过长度限制。'}
        start = time.monotonic()
        ticks = 0

        def progress():
            nonlocal ticks
            ticks += 1000
            return int(ticks > self.limits.vm_steps or time.monotonic() - start > self.limits.query_seconds)

        self.connection.set_progress_handler(progress, 1000)
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql)  # sqlite3.execute 拒绝多条 SQL；授权器拒绝写入/PRAGMA/ATTACH。
            if cursor.description is None:
                raise ValueError('只能执行返回结果的查询。')
            columns = [x[0] for x in cursor.description]
            if len(set(columns)) != len(columns):
                raise ValueError('输出列名重复，请使用不同别名。')
            rows = [list(r) for r in cursor.fetchmany(self.limits.rows + 1)]
            truncated = len(rows) > self.limits.rows
            rows = rows[:self.limits.rows]
            for row in rows:
                for value in row:
                    if isinstance(value, float) and not math.isfinite(value):
                        raise ValueError('查询产生无穷值，请检查分母和运算。')
            while len(json.dumps(rows, ensure_ascii=False)) > self.limits.result_chars:
                rows.pop()
                truncated = True
            self.counter += 1
            query_id = f'Q{self.counter:03d}'
            result = {'ok': True, 'query_id': query_id, 'columns': columns, 'rows': rows,
                      'returned_rows': len(rows), 'truncated': truncated,
                      'elapsed_ms': round((time.monotonic() - start) * 1000, 2)}
            self.evidence[query_id] = {'sql': sql, **result}
            return result
        except (sqlite3.Error, ValueError, TypeError) as exc:
            return {'ok': False, 'error': str(exc)[:500],
                    'hint': '检查字段/函数白名单；写操作、递归、系统表禁止；interrupted 表示资源超限。'}
        finally:
            cursor.close()
            self.connection.set_progress_handler(None, 0)

    def close(self):
        self.connection.close()
