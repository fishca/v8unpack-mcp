"""Лёгкий парсер структуры BSL-модуля.

Выделяет из текста модуля три части:
- объявления переменных (`Перем`) в начале;
- процедуры и функции (имя, вид, Экспорт, директивы, докстринг, параметры, позиции);
- секцию «основной программы» — код на уровне модуля после процедур/функций.
"""

import re

_END_KEYWORD = {
    "Процедура": "КонецПроцедуры",
    "Функция": "КонецФункции",
}

_DECL_RE = re.compile(r"^(Процедура|Функция)\s+([\w]+)")
_DIRECTIVE_RE = re.compile(r"^&[\w]+")
_COMMENT_RE = re.compile(r"^//")
_VAR_RE = re.compile(r"^Перем\s+(.+)$")


def _scan_params(lines, i):
    """Прочитать список параметров (внутри внешних скобок), допуская переносы строк.

    Возвращает (params_text, close_line_idx, close_col) либо (None, None, None), если
    на строке i нет открывающей скобки.
    """
    start_col = lines[i].find("(")
    if start_col == -1:
        return None, None, None
    depth = 0
    in_str = False
    str_char = None
    buf = []
    line_idx = i
    col = start_col
    while line_idx < len(lines):
        line = lines[line_idx]
        while col < len(line):
            ch = line[col]
            if in_str:
                buf.append(ch)
                if ch == str_char:
                    if col + 1 < len(line) and line[col + 1] == str_char:
                        buf.append(line[col + 1])
                        col += 1
                    else:
                        in_str = False
                        str_char = None
                col += 1
                continue
            if ch in "\"'":
                in_str = True
                str_char = ch
                buf.append(ch)
                col += 1
                continue
            if ch == "(":
                if depth > 0:
                    buf.append(ch)
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return "".join(buf), line_idx, col
                buf.append(ch)
            else:
                if depth > 0:
                    buf.append(ch)
            col += 1
        line_idx += 1
        col = 0
    return None, None, None


def _find_end(lines, start_idx, end_kw):
    """Индекс строки с ключевым словом конца процедуры/функции (или последняя строка)."""
    for k in range(start_idx + 1, len(lines)):
        s = lines[k].strip()
        if s.startswith(end_kw) and (len(s) == len(end_kw) or not s[len(end_kw)].isalnum()):
            return k
    return len(lines) - 1


def _split_top(text):
    """Разбить строку параметров на top-level запятые (вне скобок и строк)."""
    if not text.strip():
        return []
    parts = []
    depth = 0
    in_str = False
    str_char = None
    cur = []
    i = 0
    while i < len(text):
        ch = text[i]
        if in_str:
            cur.append(ch)
            if ch == str_char:
                if i + 1 < len(text) and text[i + 1] == str_char:
                    cur.append(text[i + 1])
                    i += 1
                else:
                    in_str = False
            i += 1
            continue
        if ch in "\"'":
            in_str = True
            str_char = ch
            cur.append(ch)
        elif ch in "([{":
            depth += 1
            cur.append(ch)
        elif ch in ")]}":
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
        i += 1
    if "".join(cur).strip():
        parts.append("".join(cur).strip())
    return parts


def _parse_param(text):
    t = text.strip()
    by_value = False
    m = re.match(r"^Знач\s+(.+)$", t, re.IGNORECASE)
    if m:
        by_value = True
        t = m.group(1).strip()
    name = t
    default = None
    if "=" in t:
        idx = t.find("=")
        name = t[:idx].strip()
        default = t[idx + 1:].strip()
    return {"name": name, "by_value": by_value, "default": default}


def _parse_vars(text):
    """Разобрать правую часть `Перем ...` в список (имя, экспорт)."""
    t = text.strip().rstrip(";").strip()
    export = False
    m = re.search(r"\bЭкспорт\b", t, re.IGNORECASE)
    if m:
        export = True
        t = (t[:m.start()] + " " + t[m.end():]).strip()
    return [(n.strip(), export) for n in t.split(",") if n.strip()]


def parse_module(text):
    """Разобрать BSL-модуль на переменные / процедуры-функции / основную программу.

    Возвращает dict:
      variables  — [{name, export, line}]
      procedures — [{name, kind, export, directives, docstring, params, start_line, end_line}]
      main       — {start_line, end_line, line_count, content} либо None
      total_lines — число строк модуля
    """
    lines = text.splitlines()
    n = len(lines)
    variables = []
    procedures = []
    main_line_nos = []

    i = 0
    pending_doc = []         # [(line_no, text)]
    pending_directives = []  # [(line_no, text)]

    while i < n:
        line = lines[i]
        lineno = i + 1
        s = line.strip()

        if not s:
            pending_doc = []
            pending_directives = []
            i += 1
            continue

        if _COMMENT_RE.match(s):
            pending_doc.append((lineno, s[2:].strip()))
            i += 1
            continue

        if _DIRECTIVE_RE.match(s):
            pending_directives.append((lineno, s))
            i += 1
            continue

        decl = _DECL_RE.match(s)
        if decl:
            kind = decl.group(1)
            name = decl.group(2)
            params_text, close_idx, close_col = _scan_params(lines, i)
            export = False
            if params_text is not None and close_idx is not None:
                after = lines[close_idx][close_col + 1:]
                export = bool(re.search(r"\bЭкспорт\b", after, re.IGNORECASE))
                body_start_idx = close_idx
            else:
                params_text = ""
                body_start_idx = i
            end_idx = _find_end(lines, body_start_idx, _END_KEYWORD[kind])
            procedures.append({
                "name": name,
                "kind": kind,
                "export": export,
                "directives": [d for _, d in pending_directives],
                "docstring": "\n".join(d for _, d in pending_doc),
                "params": [_parse_param(p) for p in _split_top(params_text)],
                "start_line": lineno,
                "end_line": end_idx + 1,
            })
            pending_doc = []
            pending_directives = []
            i = end_idx + 1
            continue

        varm = _VAR_RE.match(s)
        if varm:
            for name, export in _parse_vars(varm.group(1)):
                variables.append({"name": name, "export": export, "line": lineno})
            pending_doc = []
            pending_directives = []
            i += 1
            continue

        main_line_nos.append(lineno)
        pending_doc = []
        pending_directives = []
        i += 1

    main = None
    if main_line_nos:
        main = {
            "start_line": main_line_nos[0],
            "end_line": main_line_nos[-1],
            "line_count": main_line_nos[-1] - main_line_nos[0] + 1,
            "content": "\n".join(lines[main_line_nos[0] - 1:main_line_nos[-1]]),
        }

    return {
        "variables": variables,
        "procedures": procedures,
        "main": main,
        "total_lines": n,
    }
