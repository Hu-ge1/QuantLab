# -*- coding: utf-8 -*-
"""生成代码静态质检:语法编译 + QMT(Python 3.6)兼容性检查。"""


def check_code(code, form="qmt_builtin"):
    issues = []
    if not code or not code.strip():
        return ["代码为空"]
    try:
        compile(code, "strategy", "exec")
    except SyntaxError as exc:
        return ["语法错误: 第%s行 %s" % (exc.lineno, exc.msg)]
    except Exception as exc:
        return ["编译失败: %s" % exc]

    import ast
    try:
        tree = ast.parse(code)
    except Exception as exc:
        return ["解析失败: %s" % exc]

    if form != "qmt_builtin":
        return issues

    # Python 3.7+ 语法在 QMT(3.6.5)不可用
    for node in ast.walk(tree):
        if isinstance(node, ast.NamedExpr):
            issues.append("第%d行使用了海象运算符 :=(需 Python 3.8+,QMT 是 3.6)" % node.lineno)
        if isinstance(node, ast.Match):
            issues.append("第%d行使用了 match/case(需 Python 3.10+,QMT 是 3.6)" % node.lineno)
        if isinstance(node, ast.AsyncFunctionDef):
            issues.append("第%d行使用了 async def(QMT 内置环境不支持)" % node.lineno)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for name in names:
                root = (name or "").split(".")[0]
                if root == "dataclasses":
                    issues.append("第%d行 import dataclasses(需 Python 3.7+)" % node.lineno)

    # QMT 内置策略结构检查
    first = code.lstrip().splitlines()[0].strip() if code.strip() else ""
    if not (first.startswith("#coding:") or first.startswith("# coding:")
            or first.startswith("#coding =") or first.startswith("# coding=")):
        issues.append("缺少第一行编码声明 #coding:gbk")
    func_names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    if "init" not in func_names:
        issues.append("缺少 init(ContextInfo) 初始化函数")
    if "handlebar" not in func_names:
        issues.append("缺少 handlebar(ContextInfo) 主逻辑函数")
    return issues
