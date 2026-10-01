# -*- coding: utf-8 -*-
"""构造发送给大模型的 messages。

两种目标形态:
  qmt_builtin : 大QMT 内置策略(init/handlebar, 粘贴进客户端即可运行)
  miniqmt     : miniQMT + xtquant 外部脚本
三种运行模式:
  backtest : 回测优先(实盘开关默认关闭)
  signal   : 只输出信号,绝不调用下单函数
  live     : 实盘就绪(仍带保险开关)
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
KNOWLEDGE_DIR = os.path.join(BASE_DIR, "knowledge")

MODE_TEXT = {
    "backtest": (
        "运行模式:回测优先。代码必须能直接在 QMT 回测中运行(回测账号 testS),"
        "同时保留实盘入口但默认关闭(LIVE_TRADING_ENABLED=False、ACCOUNT_ID=''、"
        "不满足条件时仅打印 [SIGNAL] 日志不下单)。"
    ),
    "signal": (
        "运行模式:仅信号。代码中绝不调用 passorder/order_shares 等任何下单函数,"
        "所有买卖点只通过 print 输出 [SIGNAL] 日志,并说明如何人工核对。"
    ),
    "live": (
        "运行模式:实盘就绪。账号 ID 用一个醒目的空常量让用户自行填写,"
        "启动时校验账号与资金查询,任何校验不过一律只打信号不下单;"
        "下单前必须做资金/持仓/涨跌停价/整手校验。"
    ),
}

BUILTIN_PROMPT = """你是资深A股量化策略工程师,精通迅投QMT(大QMT)内置策略开发。用户给出策略需求,你输出一份可直接粘贴进 QMT 策略编辑器运行的单文件 Python 策略代码。

【硬性环境约束】
- QMT 内置 Python 为 3.6.5(64位 Windows):可用 f-string、math、datetime、pandas、numpy;不可用 dataclasses、海象运算符 :=、match、3.7+ 新语法。
- 文件第一行必须是 #coding:gbk,且第一行之前不能有空行或注释。
- 单文件交付,不读写外部文件,不 import QMT 内置库之外的第三方包(pandas/numpy 除外)。
- 框架: def init(ContextInfo) 初始化一次; def handlebar(ContextInfo) 每根K线执行一次。参数习惯命名 C 或 ContextInfo,全文保持一致。

【常用API速查】
- ContextInfo 常用属性: C.stockcode(不带市场后缀) C.market C.period C.barpos(当前K线序号) C.capital(回测资金) C.do_back_test(是否回测) C.accountID C.accountType
- 当前K线时间: timetag_to_datetime(C.get_bar_timetag(C.barpos), '%Y%m%d')
- 板块成分: C.get_stock_list_in_sector('沪深A股'),常用板块名: 沪深A股/沪深300/上证50/中证500/上证A股/深证A股;代码格式 '600000.SH'、'000001.SZ'
- C.get_stock_name(code) 取证券名称; C.set_universe(list) 设置订阅列表(用 get_history_data 前必须先设置)
- 行情: C.get_market_data_ex(['open','high','low','close','volume','amount'], stock_list, period='1d', count=N, dividend_type='front', subscribe=False) → {code: DataFrame(index=时间字符串, columns=字段)};回测中 subscribe 必须 False
- C.get_history_data(count, period, field, dividend_type) → {code: 序列}(需先 set_universe)
- 实时tick: C.get_full_tick([codes]) → {code: {'lastPrice':..,'lastClose':..,'askPrice':[..],'bidPrice':[..],'volume':..,'amount':..}}
- 账户/持仓/委托/成交查询: get_trade_detail_data(accountID, accountType, 'ACCOUNT'|'POSITION'|'ORDER'|'DEAL') → 对象列表
  POSITION 常用字段: m_strInstrumentID(6位代码) m_strMarket m_nVolume(总持仓) m_nCanUseVolume(可用,卖出只能用这个) m_dOpenPrice
  ACCOUNT 常用字段: m_dAvailable(可用资金,单位元) m_dBalance(总资产)。不同券商版本字段单位可能有差异,涉及资金判断时保守取整。
- 下单: passorder(opType, orderType, accountID, code, prType, price, volume, C)
  opType: 23=买入 24=卖出; orderType: 1101=股票按股数
  prType: 11=限价(price>0) 14=最新价(price传-1);不确定时用 11 限价
  volume: 股数,买入必须是100的整数倍
- 简便下单: order_shares(code, 股数, 'fix', 价格, C, accountID),卖出时股数传负数

【A股交易铁律】
- T+1: 当日买入不可当日卖出,卖出判断一律基于 m_nCanUseVolume
- 买入股数必须 100 整数倍: int(目标金额/价格/100)*100,不足100股放弃
- 涨跌停价: round(昨收*1.1, 2)/round(昨收*0.9, 2)(四舍五入到分);主板±10%、ST±5%、创业板(300/301/302)与科创板(688)±20%、北交所±30%。判断是否封板用 close >= 涨停价-0.01 的浮点容差
- 涨停价挂单大概率排队不成交,回测按涨停价成交偏乐观,必须在注释里说明
- 过滤 ST/*ST/退市: 证券名称含 'ST' 或 '退' 的剔除;当日停牌(成交量为0)的跳过
- 新股上市首日无涨跌幅限制的品种不适合常规涨跌停逻辑,需求涉及时要处理

【代码规范】
- 可调参数全部集中到文件顶部的 PARAMS 字典,每个参数带中文注释
- 关键动作(初始化/信号/委托/异常)一律 print 日志,格式如 [标签] 内容,方便在 QMT 日志面板排查
- 数据访问与下单段用 try/except 包裹并打印异常,不允许未捕获异常终止策略
- 状态保存在 ContextInfo.xxx 上;handlebar 用"已处理K线时间戳"去重,避免盘中同一根K线重复执行
- 产生买卖信号时调用 _report_signal(C, 代码, "买入"/"卖出"/"信号", 价格, 说明) 上报到本地工具
  的「信号流」面板;该函数已在本骨架给出,保留原样,失败静默不影响策略
- 注释和日志用中文

【输出格式】(严格按以下分节输出,便于分栏展示)
1. 第一部分输出一个完整可运行的 ```python 代码块,不要省略任何函数
2. 代码之后依次输出以下小节(用 ## 二级标题分隔):
   ## 策略逻辑
   用 5~8 条要点说明买卖规则与状态机
   ## 参数表
   用 markdown 表格列出 PARAMS 各参数: 参数名 | 默认值 | 含义 | 调参建议
   ## 风险与注意
   至少 4 条:成交风险(涨跌停/滑点)、失效场景、回测乐观偏差、资金与税费影响
   ## 运行步骤
   粘贴到 QMT 的操作步骤与回测/实盘切换说明
   ## 可优化方向
   2~4 条改进建议
3. 不输出寒暄,不输出与交付无关的内容"""

MINIQMT_PROMPT = """你是资深A股量化策略工程师,精通 miniQMT + xtquant 外部脚本开发。用户给出策略需求,你输出一份可在外部 Python 3.8+ 环境运行的单文件策略脚本。

【环境与背景】
- xtquant 依赖已登录的 miniQMT 客户端(极简模式);注意 miniQMT 自2026年7月起已停止新用户申请,输出末尾提醒用户做好向大QMT内置策略迁移的准备。
- QMT 自带的 xtquant 位于 <QMT安装目录>\\bin.x64\\lib\\site-packages,脚本开头提示用户把该目录加入 sys.path(给出代码)。
- 行情库 xtdata: xtdata.get_market_data_ex / xtdata.get_full_tick / xtdata.download_history_data / xtdata.subscribe_whole_quote
- 交易库 xttrader: XtQuantTrader(<userdata_mini路径>, session_id) 连接,xt_trader.connect() 返回0为成功;XtQuantTraderCallback 处理回报;StockAccount(account_id) 指定账号
- 下单: xt_trader.order_stock_async(acc, code, xtconstant.STOCK_BUY/STOCK_SELL, 股数, xtconstant.FIX_PRICE/LATEST_PRICE, 价格, '策略名', '备注');查询: query_stock_asset / query_stock_positions / query_stock_orders
- 股数同样必须100整数倍,T+1 规则与涨跌停计算同A股主板规则(±10%、ST±5%)

【代码规范】
- 参数集中到顶部 PARAMS 字典并注释;日志用 logging 或 print 带时间戳
- 主循环逻辑清晰(轮询示例用 while + 简单休眠,事件驱动示例用回调注册)
- 所有 xtquant 调用带异常处理;连接失败给出行动建议(检查客户端是否登录、路径是否正确)
- 注释和日志用中文

【输出格式】
1. 一个完整 ```python 代码块
2. 之后简短说明: 运行前置条件(登录miniQMT、路径配置)、逻辑要点、风险点
3. 不输出寒暄"""

SKELETON = """参考骨架(风格基准,不要原样照抄,按需求扩展;信号上报函数保留原样):
#coding:gbk
import math

PARAMS = {
    "max_positions": 2,      # 最大同时持仓数
    "stop_loss": 0.05,       # 止损比例
}

BRIDGE_TOKEN = ""

def _report_signal(C, code, action, price, note):
    # 把信号上报到本地工具的信号流面板,失败静默;回测模式自动跳过。
    try:
        if getattr(C, "do_back_test", False):
            print("[SIGNAL][回测] %s %s %s %s" % (code, action, price, note))
            return
        import json as _json
        import urllib.request as _ur
        payload = _json.dumps({"type": "signal", "strategy": globals().get("STRATEGY_NAME", "未命名策略"),
                               "code": code, "action": action,
                               "price": price, "note": note}).encode("utf-8")
        req = _ur.Request("http://127.0.0.1:17321/api/bridge/push", data=payload,
                          headers={"Content-Type": "application/json",
                                   "X-Bridge-Token": BRIDGE_TOKEN})
        _ur.urlopen(req, timeout=3).close()
    except Exception:
        pass

def init(C):
    C.positions = {}
    C.last_bar = None
    C.universe = C.get_stock_list_in_sector('沪深A股')
    C.set_universe(C.universe)

def handlebar(C):
    bar_time = timetag_to_datetime(C.get_bar_timetag(C.barpos), '%Y%m%d')
    if C.last_bar == bar_time:
        return
    C.last_bar = bar_time
    # ... 信号计算(用历史数据,避免使用未来数据) ...
    # ... _report_signal(C, code, "买入", price, "原因说明") ...
    # ... 下单(回测账号 testS,实盘需配置账号) ...
"""


def _knowledge_block(names):
    """按用户勾选附加知识文档拼接原文(限制总量,防止超长)。"""
    blocks, total = [], 0
    limit = 24000
    for name in names:
        safe = str(name).replace("\\", "/")
        if ".." in safe:
            continue
        fp = os.path.join(KNOWLEDGE_DIR, safe)
        if not os.path.isfile(fp):
            continue
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except Exception:
            continue
        if total + len(text) > limit:
            text = text[: max(0, limit - total)]
        blocks.append("===== 知识文档: %s =====\n%s" % (safe, text))
        total += len(text)
        if total >= limit:
            break
    return "\n\n".join(blocks)


def build_messages(requirement, form, mode, extra, knowledge=None, meta=None):
    messages = _build(requirement, form, mode, extra, knowledge, meta)
    return messages


def _build(requirement, form, mode, extra, knowledge=None, meta=None):
    if form == "miniqmt":
        system = MINIQMT_PROMPT
    else:
        system = BUILTIN_PROMPT
    system += "\n\n" + MODE_TEXT.get(mode, MODE_TEXT["backtest"])
    if form != "miniqmt":
        system += "\n\n" + SKELETON

    kb = _knowledge_block(knowledge or [])
    if kb:
        system += "\n\n【附加参考资料(仅供参考接口用法,代码以本提示词规范为准)】\n" + kb

    meta = meta or {}
    user = "策略需求:\n" + requirement.strip()
    env_lines = []
    if meta.get("capital"):
        env_lines.append("回测资金规模:" + str(meta["capital"]))
    if meta.get("period"):
        env_lines.append("K线周期:" + str(meta["period"]))
    if env_lines:
        user += "\n\n运行环境:\n" + "\n".join(env_lines)
    if extra:
        user += "\n\n补充要求:\n" + extra.strip()
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def build_revision_messages(requirement, base_code, note, form, mode, extra):
    """多轮修改:基于当前代码的修改请求。"""
    system = MINIQMT_PROMPT if form == "miniqmt" else BUILTIN_PROMPT
    system += "\n\n" + MODE_TEXT.get(mode, MODE_TEXT["backtest"])
    system += ("\n\n【本次任务是修订】用户会给出当前版本的策略代码与修改要求。"
               "输出修改后的【完整】策略代码与说明(输出格式与之前一致),"
               "未涉及的部分保持原样,不要省略任何函数。")
    user = ("原始需求:\n" + (requirement or "(未提供)") +
            "\n\n当前版本代码:\n```python\n" + base_code + "\n```" +
            "\n\n修改要求:\n" + note.strip())
    if extra:
        user += "\n\n其他固定要求:\n" + extra.strip()
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def build_repair_messages(code, issues, form):
    """自动修复:静态检查发现问题的代码。"""
    system = ("你是QMT策略代码修复器。用户给出一份策略代码与静态检查发现的问题列表。"
              "只修复列出的问题与明显相关的问题,其余逻辑保持不变。"
              "输出修复后的完整代码,用一个```python代码块,不要输出其他解释。")
    if form != "miniqmt":
        system += (" 注意:QMT 内置 Python 是 3.6.5,文件第一行必须是 #coding:gbk,"
                   "必须有 init(ContextInfo) 与 handlebar(ContextInfo) 函数,"
                   "禁止 dataclasses/海象运算符/match/async。")
    user = ("静态检查问题:\n" + "\n".join("- " + i for i in issues) +
            "\n\n代码:\n```python\n" + code + "\n```")
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
