# Superstore 受控盈利分析 Agent · B 版本

这是你现有 Superstore 项目的**独立升级版**，不修改原 GitHub 仓库。目标是展示业务口径、工具调用、结果校验与人工审核，不是训练大模型或搭建生产数仓。

**先读 [配置与运行](docs/01_配置与运行.md)，再读 [架构与原理](docs/02_架构与原理.md)。**

## 能做什么 / 不能宣称什么

- 本地 CSV → 强制质量检查 → SQLite → Agent 自主生成 SQL 与下钻 → 结构化报告 → 独立核心指标核验 → 人工审核记录。
- 在线默认接百炼北京地域 `qwen-plus`，使用非思考模式 Chat Completions 工具调用。具体账号权限、模型能力和费用以你的控制台为准。
- 无密钥的 `demo` 是**固定脚本模拟器**，使用真实 SQLite 结果验证软件链路，不是 LLM，也不能用于宣称 AI 准确率。
- `run` 才是真实模型调用；只有配置自己的 API Key、明确同意云传输后才能运行。
- 原始数据保留本地，但问题、说明、SQL和返回结果会传给模型厂商。不是完全本地AI。
- 当前只分析静态数据全周期和一个指定品类（或 ALL），不支持用户在问题中改成某月的核心指标范围；未来增加日期参数时必须同步改参考计算、契约和测试。
- 不执行任意 Python，不操作业务系统，不做真实调价，不把观察性相关解释为因果。
- 不包含 A/C 的完整对照实验、不保证生产安全，也不等于大数据平台或云数仓实践。

## 快速执行（在本项目目录打开 PowerShell）

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts/fetch_data.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m agent_b check
.\.venv\Scripts\python.exe -m agent_b demo
```

上面除下载与安装外不联网，不调用模型。原始 CSV 如已存在，下载脚本不会覆盖。

配置好环境变量后：

```powershell
.\.venv\Scripts\python.exe -m agent_b ping --allow-cloud
.\.venv\Scripts\python.exe -m agent_b run --allow-cloud --category Furniture
```

运行会输出新的 `runs/时间_随机编号` 目录。查看 `state.json`、`report.md`、`queries.json`，再按指南记录人审。在线产生的报告始终先等待人审，不自动批准。

## 文件地图

每个 Python 文件顶部有中文职责说明，关键安全与理论点有行内注释。配置/文本文件作用列在这里。

| 文件 | 作用 | 建议阅读顺序 |
|---|---|---|
| `README.md` | 项目入口、范围、命令、文件地图 | 1 |
| `docs/01_配置与运行.md` | Windows配置、密钥、命令、状态与产物 | 2 |
| `docs/02_架构与原理.md` | 分层、数据粒度、工具调用、权限、验证理论 | 3 |
| `docs/03_人工任务与验收.md` | 你和Agent的分工、手算练习、在线验收 | 4 |
| `docs/04_故障排查.md` | 分阶段问题、诊断方法和处理边界 | 随用随查 |
| `docs/05_来源与验证记录.md` | 数据/原仓库/官方文档、已验证和未验证事项 | 交付检查 |
| `docs/06_交付测试结果.md` | 本机实际测试记录、发现并修复的问题 | 交付检查 |
| `docs/07_AI辅助语义审核案例.md` | 真实运行的语义审核、失败分类与修订闭环 | 交付检查 |
| `docs/08_离线评测集.md` | 固定真实失败的本地报告契约 Evaluation | 交付检查 |
| `docs/09_项目阶段总结.md` | 两个在线案例、验证结果、失败与改进总结 | 项目总结 |
| `examples/` | 从本地审计运行中整理出的两份脱敏修订报告示例 | 结果示例 |
| `agent_b/config.py` | 固定资源上限；修改须重新测试 | 5 |
| `agent_b/data.py` | CSV规范化、数据门禁、独立Pandas参考计算 | 6 |
| `agent_b/database.py` | SQLite授权、函数白名单、执行与输出限制 | 7 |
| `agent_b/contracts.py` | 模型可见规则、字段、工具JSON Schema | 8 |
| `agent_b/providers.py` | 在线模型适配与离线模拟；唯一模型联网入口 | 9 |
| `agent_b/controller.py` | 状态流转、工具执行、有限重试和报告提交 | 10 |
| `agent_b/validation.py` | 结构、核心指标、证据单元格核验 | 11 |
| `agent_b/audit.py` | 独立运行目录、脱敏日志、Markdown报告 | 12 |
| `agent_b/cli.py` | 命令参数、云传输确认、人审入口 | 13 |
| `agent_b/__main__.py` | 支持 `python -m agent_b` | 14 |
| `agent_b/__init__.py` | 声明Python包 | 14 |
| `scripts/fetch_data.py` | 固定Git提交下载样本，核验Git blob和SHA256 | 需要时 |
| `tests/test_system.py` | 无网络自动测试，4行可手算样本 | 15 |
| `requirements.txt` | 第三方依赖范围（Pandas） | 安装时 |
| `.gitignore` | 排除密钥、虚拟环境、CSV、运行结果；不代替权限控制 | 发布前 |
| `data/source_manifest.json` | 下载生成的来源提交与摘要，不是手工配置 | 下载后 |
| `runs/…` | 运行生成的证据、状态和报告，默认不提交Git | 每次运行后 |

## 开发方式

先 `check` 和 `demo`，再学 `data.py` 与 `database.py`，最后接入在线模型。不要直接大幅改提示词“调到通过”；把失败分类为数据、工具、协议、计算、业务解释五类再处理。

代码由AI协助实现。当前版本已完成 Furniture 证据边界验收、Office Supplies 换对象验收和对应人工修订闭环；复用或扩展本项目时，仍应独立复算关键指标，并能解释每次失败和修改理由。
