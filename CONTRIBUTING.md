# 贡献指南

欢迎 Issue 和 PR。提之前先花两分钟看这一页，能省掉一轮来回。

## 提 Issue

- **先搜一下现有 issue**，避免重复。
- **贴脱敏后的信息**：报错文本、复现步骤、系统与 Python/Node 版本。
- **不要贴**：API Key、资金账号、券商名称、桥接 Token、含用户名的本机绝对路径。需要贴日志时请先把这些替换成 `***`。
- **功能建议先讲场景和痛点，再谈方案**。上来就给方案，容易在错误的问题上做正确的事。

## 提 PR

### 分支与提交

- 从 `main` 切分支，PR 目标为 `main`。
- 提交信息用「动词开头的一句话 + 空行 + 项目符号细节」，中文英文都可以，但一个 PR 内保持一致。
- 一个 PR 只做一件事。顺手重构请单独提。

### 提交前跑一遍 CI 会做的事

```bash
python -m compileall -q backend -x 'backups'   # 后端语法
cd frontend && npm run build                   # 前端类型检查 + 构建
```

`-x 'backups'` 排除 `backend/data/studio/backups/`：那是**运行时**目录（已 gitignore），里面是应用每次保存 QMT 策略时自动留的旧版本副本。如果你本机跑过旧版本的应用，这里可能残留着不可编译的历史文件，而 CI 的全新检出根本不存在该目录——不排除它，你本地会红、CI 会绿，白排查一圈。排除只针对这一个目录，别处的语法错照样会被抓出来。

CI（`.github/workflows/ci.yml`）会在 Python 3.10 / 3.12 上跑后端编译、应用加载自检与健康检查，在 Node 20 上跑前端构建。

> **「应用加载自检」这一步会在未配置任何 QMT 路径的环境中导入 `main`。** `main.py` 在模块级就会初始化数据库、尝试迁移、加载持久化、启动注册表看门狗——它能加载成功，说明全新克隆的机器开箱可用。改动 `main.py` / `database.py` / `qmt_bridge.py` 的初始化路径时尤其注意别把这一步弄红。

### 在 Windows 本机构建前端

本机 `npm ci` 可能在 `esbuild` 的 postinstall 阶段因文件锁报 `EBUSY`（它要 spawn 当前 `node.exe`）。这是 Windows 环境问题，CI 的 Linux runner 上不存在。绕过方式：

```bash
npm ci --ignore-scripts
npm run build
```

## 代码约定

### 编码与换行符

`.gitattributes` 与 `.editorconfig` 已经定好，不要手动改：

- 文本文件统一 **UTF-8 + LF**；`*.bat` / `*.cmd` 强制 **CRLF**（否则别人的 Windows 上双击会失败）。
- Python 缩进 4 空格，前端 2 空格。
- **改文件时用字节级或保留换行的方式写回**。用文本模式重写会把 CRLF 归一成 LF，`*.cmd` 会静默坏掉——这种问题只在别人的机器上复现。

### QMT 策略脚本的编码

`backend/data/studio/bridge/QMT可视化桥接.py` 在**仓库里是 UTF-8**，安装到 QMT 目录时才转成 GBK。转码由 `qmt_bridge.encode_strategy_for_qmt()` 统一下发：

- 源码自带 `#coding:` 声明就尊重它，没有就补 `#coding:gbk` 再转码。
- **不要在仓库内的这份源文件里写 `#coding:gbk`**——文件字节是 UTF-8，声明却是 GBK，任何静态检查（`compileall`、IDE 索引）都会直接报 `SyntaxError`。

### 其他

- 不要硬编码券商路径、账号或本机绝对路径。QMT 路径走设置项或 `QUANTLAB_QMT_*` 环境变量，解析优先级见 README。
- 新增数据源要保留 demo 兜底，断网时必须仍能跑通全部功能。
- 涉及交易或资金的计算，请在 PR 描述里写清楚你怎么验证的。

## 报告安全问题

**不要开公开 Issue**，走 [SECURITY.md](SECURITY.md) 里的私密渠道。
