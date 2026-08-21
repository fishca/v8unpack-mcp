"""Определение текстовых слоёв, доступных для поиска в распакованном дереве.

Какие слои считаем «извлекаемым текстом»:
- `.bsl`  — модули (исходники);
- `.json` — заголовки объектов, реквизиты и дерево элементов форм;
- `.txt`  — текстовые макеты;
- `.html` — HTML-макеты;
- `.bin`  — СКД (схема компоновки данных): бинарный префикс + XML с текстом запроса.

Пропускаем: `.mxl` (табличный документ, бинарный), `.c1brace` (роли), картинки
(`.bin` без XML), зашифрованные модули (`*.obj.bin` без XML).
"""

import os

SEARCHABLE_EXTENSIONS = {".bsl", ".json", ".txt", ".html"}

# Имя слоя поиска по расширению файла.
LAYER_BY_EXT = {
    ".bsl": "modules",            # весь код модулей (общие, объектов, форм, команд)
    ".json": "forms",             # структура форм: реквизиты, элементы
    ".txt": "templates_text",     # текстовые макеты
    ".html": "templates_html",    # HTML-макеты
    ".bin": "dcc",                # схемы компоновки данных (СКД): XML из бинарника
}
ALL_LAYERS = ["modules", "forms", "templates_text", "templates_html", "dcc"]

_XML_BOM = b"\xef\xbb\xbf<?xml"
_XML_PLAIN = b"<?xml"


def decode_text(data: bytes):
    """Пытаемся декодировать байты как текст (utf-8-sig → utf-8 → cp1251)."""
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return None


def extract_xml_from_bin(data: bytes):
    """Из СКД-файла (.bin) достаём XML-часть после бинарного префикса."""
    idx = data.find(_XML_BOM)
    if idx == -1:
        idx = data.find(_XML_PLAIN)
    if idx == -1:
        return None
    return decode_text(data[idx:])


def extract_dcs_queries(data: bytes):
    """Тексты запросов СКД (содержимое `<query>`), склеенные — для поиска по области query.

    Возвращает None, если `.bin` не является СКД (нет ни одного `<query>`).
    Текст декодируется через XML-парсер, поэтому `&lt;`/`&amp;` и т.п. раскрываются.
    """
    from . import dcs
    try:
        parsed = dcs.load(data)
    except Exception:
        return None
    parts = []
    for ds in parsed["schema"]:
        if dcs._local(ds.tag) != "dataSet":
            continue
        q = dcs._child(ds, "query")
        if q is not None and q.text:
            parts.append(q.text)
    return "\n\n".join(parts) if parts else None


def is_text_layer(filename: str) -> bool:
    ext = os.path.splitext(filename)[1].lower()
    return ext in SEARCHABLE_EXTENSIONS


def layer_text(filename: str, data: bytes, dcc_scope: str = None):
    """Вернуть текст слоя или None, если слой бинарный/неизвлекаемый.

    dcc_scope — область СКД: 'query' (только тексты запросов) или None (весь XML).
    """
    ext = os.path.splitext(filename)[1].lower()
    if ext in SEARCHABLE_EXTENSIONS:
        return decode_text(data)
    if ext == ".bin":
        if dcc_scope == "query":
            return extract_dcs_queries(data)
        return extract_xml_from_bin(data)
    return None


def layer_of(filename: str):
    """Имя слоя поиска для файла (None — файл не является извлекаемым текстовым слоем)."""
    return LAYER_BY_EXT.get(os.path.splitext(filename)[1].lower())


def normalize_layers(layers):
    """Привести список слоёв к множеству допустимых значений.

    layers пусто/None → все слои. Неизвестное имя → ValueError со списком допустимых.
    """
    if not layers:
        return set(ALL_LAYERS)
    wanted = set()
    for name in layers:
        if name not in ALL_LAYERS:
            raise ValueError(f"Неизвестная область поиска: {name!r}. Допустимые: {ALL_LAYERS}")
        wanted.add(name)
    return wanted


def iter_searchable_files(root: str, layers=None, dcc_scope: str = None):
    """Обход дерева, отдаём (относительный_путь, текст, имя_слоя).

    layers — список имён областей поиска (см. ALL_LAYERS); пусто/None — все.
    dcc_scope — для слоя dcc: 'query' (только запросы) или None (весь XML).
    """
    wanted = normalize_layers(layers)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in filenames:
            if fn.startswith("."):
                continue
            layer = layer_of(fn)
            if layer is None or layer not in wanted:
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root)
            try:
                with open(full, "rb") as f:
                    data = f.read()
            except OSError:
                continue
            text = layer_text(fn, data, dcc_scope)
            if text is not None:
                yield rel, text, layer
