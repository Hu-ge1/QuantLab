# -*- coding: utf-8 -*-
"""极简 Markdown → HTML 渲染(知识库阅读用),纯标准库,支持常用语法。"""
import html
import re


def _inline(text):
    text = html.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2" target="_blank">\1</a>', text)
    return text


def md_to_html(md):
    lines = md.replace("\r\n", "\n").split("\n")
    out, i, n = [], 0, len(lines)
    in_code, code_buf, code_lang = False, [], ""

    def flush_list(items, ordered):
        if not items:
            return
        tag = "ol" if ordered else "ul"
        out.append("<%s>%s</%s>" % (tag, "".join("<li>%s</li>" % it for it in items), tag))

    list_items, list_ordered = [], False
    in_table = False
    while i < n:
        line = lines[i]

        if line.strip().startswith("```"):
            if in_code:
                lang_css = " class=\"%s\"" % code_lang if code_lang else ""
                out.append("<pre><code%s>%s</code></pre>" % (
                    lang_css, html.escape("\n".join(code_buf))))
                in_code, code_buf = False, []
            else:
                flush_list(list_items, list_ordered)
                list_items, list_ordered = [], False
                if in_table:
                    out.append("</tbody></table>")
                    in_table = False
                in_code, code_lang = True, line.strip()[3:].strip()
            i += 1
            continue
        if in_code:
            code_buf.append(line)
            i += 1
            continue

        stripped = line.strip()

        # 表格
        if stripped.startswith("|") and stripped.endswith("|") and i + 1 < n and \
                re.match(r"^\|[\s:\-|]+\|$", lines[i + 1].strip()):
            if not in_table:
                out.append('<table><thead><tr>')
                cells = [c.strip() for c in stripped.strip("|").split("|")]
                out.append("".join("<th>%s</th>" % _inline(c) for c in cells))
                out.append("</tr></thead><tbody>")
                in_table = True
                i += 2
                continue
        if in_table:
            if stripped.startswith("|") and stripped.endswith("|"):
                cells = [c.strip() for c in stripped.strip("|").split("|")]
                out.append("<tr>%s</tr>" % "".join("<td>%s</td>" % _inline(c) for c in cells))
                i += 1
                continue
            out.append("</tbody></table>")
            in_table = False

        # 标题
        m = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if m:
            flush_list(list_items, list_ordered)
            list_items, list_ordered = [], False
            level = len(m.group(1))
            anchor = re.sub(r"[^\w\u4e00-\u9fa5]+", "-", m.group(2))[:40]
            out.append('<h%d id="%s">%s</h%d>' % (level, anchor, _inline(m.group(2)), level))
            i += 1
            continue

        # 分隔线
        if re.match(r"^(-{3,}|\*{3,})$", stripped):
            out.append("<hr>")
            i += 1
            continue

        # 列表
        m_ul = re.match(r"^[-*]\s+(.*)$", stripped)
        m_ol = re.match(r"^\d+[.)]\s+(.*)$", stripped)
        if m_ul or m_ol:
            ordered = bool(m_ol)
            content = m_ul.group(1) if m_ul else m_ol.group(1)
            if list_items and list_ordered != ordered:
                flush_list(list_items, list_ordered)
                list_items = []
            list_ordered = ordered
            list_items.append(_inline(content))
            i += 1
            continue
        flush_list(list_items, list_ordered)
        list_items, list_ordered = [], False

        # 引用
        if stripped.startswith(">"):
            out.append("<blockquote>%s</blockquote>" % _inline(stripped.lstrip("> ")))
            i += 1
            continue

        if not stripped:
            i += 1
            continue

        # 普通段落(连续行合并)
        para = [stripped]
        while i + 1 < n and lines[i + 1].strip() and \
                not re.match(r"^(#|\||```|>|-|\*|\d+[.)]|-{3,})", lines[i + 1].strip()):
            i += 1
            para.append(lines[i].strip())
        out.append("<p>%s</p>" % _inline(" ".join(para)))
        i += 1

    if in_code:
        out.append("<pre><code>%s</code></pre>" % html.escape("\n".join(code_buf)))
    flush_list(list_items, list_ordered)
    if in_table:
        out.append("</tbody></table>")
    return "\n".join(out)
