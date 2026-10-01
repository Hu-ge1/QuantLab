# 更新日志

本文件记录值得用户知道的变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [未发布]

### 修复

- **Windows 上回测必然失败**：回测子进程用 `python -I` 启动，而 `-I` 隐含 `-E`、会忽略 `PYTHONIOENCODING`，于是子进程按系统 ANSI 代码页（中文 Windows 为 GBK）读取 stdin，父进程写入的 UTF-8 payload 被解坏——策略里只要有中文注释就会得到 `'utf-8' codec can't encode character '\udcbx'`。现改为显式读 `sys.stdin.buffer` 并按 UTF-8 解码，同时把子进程 stdout/stderr 重设为 UTF-8，并在写回结果前替换掉孤立代理字符。见 `backend/backtest_worker.py`。
- **选股结果为空却不说明原因**：降级行情源（不含均线）下，界面上默认勾选的「仅保留站上 MA60」「严格多头排列」会让命中数必然为 0，而调用方只能看到一个空列表。现在当 MA60/120/250 覆盖率为 0 时会明确告知是均线字段缺失、以及取消勾选或配置 FFD 后重试。见 `backend/screener.py`。

### 新增

- 为 README 补充 10 张界面截图（`docs/screenshots/`）。
- `SECURITY.md`：本项目涉及真实资金账户与模型密钥，写明漏洞上报的私密渠道、禁止提交的内容、密钥泄漏后的处理顺序与部署边界。
- `CONTRIBUTING.md`：开发环境、CI 校验、编码与换行符约定、Windows 前端构建的已知问题。
- `.editorconfig`：与 `.gitattributes` 对齐的缩进/换行符约定。

### 工程

- CI 的语法检查改为 `python -m compileall -q backend -x 'backups'`，排除运行时目录 `backend/data/studio/backups/`。该目录已 gitignore、全新检出中不存在，不排除它会让贡献者本地报错而 CI 通过，误导排查方向；`CONTRIBUTING.md` 中的命令已同步为同一条。
- `.gitignore` 忽略 vite 构建被中断时残留的 `vite.config.ts.timestamp-*` 临时文件。

### 文档

- README「核心功能」章节编号修正（此前「市场复盘」「贝叶斯决策引擎」「策略回测」三节都写作 3.，且仅编到 5）。
- README 增补界面截图与对应说明。

## [1.0.0] — 2026-10-01

首次发布。

### 新增

- **AI 对话**：Agentic 工具调用循环，8 个工具（行情、估值、指数成分、全市场筛选、持仓、贝叶斯决策、长期记忆），带最大轮数、错误预算、重复调用指纹与结果截断等护栏。
- **量化选股**：沪深 A 股主板全市场扫描，多因子排序，三套透明权重（中期趋势 / 均衡 / 价值），FFD 全市场日频优先、公开批量行情降级，字段覆盖率可见，缺失字段不补零。
- **市场复盘**：指数看板、市场宽度、49 个行业板块热度、由当日数据模板化生成的复盘要点。
- **贝叶斯决策引擎**：对数几率逐条更新信念、信念更新轨迹、行动期望值对比、先验扫描与逐条剔除的敏感性分析。纯 Python、零依赖、可复现。
- **策略回测**：浏览器内编写聚宽语法策略，服务端实现聚宽 API 子集；日频撮合、资金约束、佣金、印花税、滑点、A 股 T+1 均已模拟；一次性子进程 + 120 秒硬超时，记录代码 SHA-256、参数、Python 版本与耗时。
- **策略自进化引擎**：三层框架（策略协议层 / 版本管理层 / 样本隔离层），训练 50% / 验证 25% / 测试 25%，验证集决定保留、测试集只做最终复核。
- **网格交易实验室**：7 种网格模式，A 股 100 股整手约束，状态持久化到 SQLite。
- **QMT 实盘接入**：桥接状态机（2 秒轮询 push/pull、token 鉴权、指令三态队列、服务端强制风控链、注册表看门狗），L1/L2/L3 三级交易能力（仅 L1 默认可用），三重保险（交易总闸、当日亏损熔断、QMT 端物理保险）。
- **策略工坊**：AI 生成 QMT 策略并自动质检（语法 + Python 3.6 兼容）、自动修复、diff 对比、一键保存并注册进 QMT。
- 跨平台启动器：Windows `.cmd` / `.bat` 与 `start.sh` / `stop.sh`，含端口预检、依赖自装、健康探测与按命令行特征精确停止。
- 行情源：akshare（东财 + 新浪双通道）免费无账号，断连自动降级 demo，全部功能仍可运行。

### 安全

- QMT 安装与策略目录改由设置项或 `QUANTLAB_QMT_*` 环境变量提供，源码内不含券商名与本机绝对路径。
- 后端默认仅监听 `127.0.0.1`，校验 Host 与浏览器 Origin，拒绝跨站 API 调用。
- 提交历史中不含真实邮箱（统一为 GitHub noreply 形式）。

[未发布]: https://github.com/Hu-ge1/QuantLab/commits/main
[1.0.0]: https://github.com/Hu-ge1/QuantLab/releases/tag/v1.0.0
