"""Разбор схем компоновки данных (СКД) из `.bin`-макетов v8unpack.

`.bin`-макет СКД = бинарный префикс + склейка из нескольких XML-документов,
разделённых BOM + `<?xml`:

- doc[0]  `<SchemaFile>` → `<dataCompositionSchema>` — основная схема:
  `dataSource`, `dataSet` (с `<query>`), `calculatedField`, `totalField`,
  `parameter`, `settingsVariant` (только `<name>` + `<presentation>`).
- doc[1..K] `<Settings>` — настройки вариантов (по одному на settingsVariant).
- doc[последний] `<SchemaFile>` — оформление (`template`/`groupTemplate`) или пусто.

Namespace URI совпадают с XML-выгрузкой платформы, но префиксы в `.bin`
автогенерированные (`d4p1`, `d5p1`, ...), а в выгрузке фиксированные
(`v8:`, `dcsset:`, `dcscor:`). Для чтения важны URI, а не префиксы.
"""

import re
import struct
import xml.etree.ElementTree as ET

# Разделитель XML-документов внутри .bin.
_BOM = b"\xef\xbb\xbf"
_BOM_XML = b"\xef\xbb\xbf<?xml"
_PLAIN_XML = b"<?xml"

# Пространства имён СКД (совпадают с XML-выгрузкой платформы).
NS_SCHEMA = "http://v8.1c.ru/8.1/data-composition-system/schema"
NS_COMMON = "http://v8.1c.ru/8.1/data-composition-system/common"
NS_CORE = "http://v8.1c.ru/8.1/data-composition-system/core"
NS_SETTINGS = "http://v8.1c.ru/8.1/data-composition-system/settings"
NS_DATA_CORE = "http://v8.1c.ru/8.1/data/core"
NS_DATA_UI = "http://v8.1c.ru/8.1/data/ui"
XSD = "http://www.w3.org/2001/XMLSchema"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
NS_UI_STYLE = "http://v8.1c.ru/8.1/data/ui/style"
NS_UI_SYS = "http://v8.1c.ru/8.1/data/ui/fonts/system"
NS_UI_WEB = "http://v8.1c.ru/8.1/data/ui/colors/web"
NS_UI_WIN = "http://v8.1c.ru/8.1/data/ui/colors/windows"

# Канонические префиксы по URI (для XML-выгрузки платформы).
_PREFIX_BY_URI = {
    NS_SCHEMA: "",
    NS_COMMON: "dcscom",
    NS_CORE: "dcscor",
    NS_SETTINGS: "dcsset",
    NS_DATA_CORE: "v8",
    NS_DATA_UI: "v8ui",
    XSD: "xs",
    XSI: "xsi",
    NS_UI_STYLE: "style",
    NS_UI_SYS: "sys",
    NS_UI_WEB: "web",
    NS_UI_WIN: "win",
}

# Элементы оформления, которые в `.bin` лежат в последнем `<SchemaFile>` (tail).
_TAIL_ELEMENTS = {"template", "groupTemplate"}

_NS_DECL_RE = re.compile(r'xmlns:([A-Za-z_][\w.-]*)="([^"]*)"')


def split_documents(data: bytes) -> list:
    """Разрезать `.bin` на отдельные XML-документы (каждый начинается с BOM+`<?xml`)."""
    starts = []
    pos = 0
    while True:
        idx = data.find(_BOM_XML, pos)
        if idx == -1:
            break
        starts.append(idx)
        pos = idx + len(_BOM_XML)
    if not starts:
        return []
    docs = []
    for k, s in enumerate(starts):
        e = starts[k + 1] if k + 1 < len(starts) else len(data)
        docs.append(data[s:e])
    return docs


def _local(tag: str) -> str:
    """Локальное имя тега (без namespace-префикса)."""
    return tag.rsplit("}", 1)[-1]


def _parse(doc: bytes) -> ET.Element:
    """Распарсить один XML-документ (байты, начиная с BOM+`<?xml`)."""
    text = doc.decode("utf-8-sig", "replace")
    text = text[text.find(_PLAIN_XML.decode()):]
    return ET.fromstring(text)


def _as_text(doc: bytes) -> str:
    """Сырой XML-текст документа (как хранится в .bin, без перепарсинга)."""
    text = doc.decode("utf-8-sig", "replace")
    return text[text.find(_PLAIN_XML.decode()):].strip()


def _root_local(doc: bytes) -> str:
    return _local(_parse(doc).tag)


def _child(elem: ET.Element, local_name: str):
    """Первый прямой ребёнок с заданным локальным именем."""
    for c in elem:
        if _local(c.tag) == local_name:
            return c
    return None


def _child_text(elem: ET.Element, local_name: str):
    c = _child(elem, local_name)
    return (c.text or "").strip() if c is not None else None


def _localized(elem: ET.Element):
    """Текст локализованной строки (LocalStringType): вложенный `<content>` (ru)."""
    if elem is None:
        return None
    for c in elem.iter():
        if _local(c.tag) == "content":
            return (c.text or "").strip()
    return (elem.text or "").strip() or None


def load(data: bytes) -> dict:
    """Разобрать `.bin` СКД в {schema, settings, tail}.

    - schema   — Element корня `<SchemaFile>` (основная схема);
    - settings — список сырых XML-текстов документов `<Settings>` (по варианту);
    - tail     — Element последнего `<SchemaFile>` (оформление) или None.
    """
    docs = split_documents(data)
    if not docs:
        raise ValueError("В файле нет XML-документов — это не макет СКД")
    # Основная схема — внутри <SchemaFile>/<dataCompositionSchema>.
    schema = _child(_parse(docs[0]), "dataCompositionSchema") or _parse(docs[0])
    settings = []
    tail = None
    for doc in docs[1:]:
        rt = _root_local(doc)
        if rt == "Settings":
            settings.append(_as_text(doc))
        elif rt == "SchemaFile":
            tail = _child(_parse(doc), "dataCompositionSchema") or _parse(doc)
    return {"schema": schema, "settings": settings, "tail": tail}


def _data_set_type(elem: ET.Element) -> str:
    """Тип набора данных из xsi:type ('DataSetQuery' → 'Query' и т.п.)."""
    raw = elem.get(f"{{{XSI}}}type") or ""
    return raw.removeprefix("DataSet") if raw.startswith("DataSet") else raw


def data_sets(schema: ET.Element) -> list:
    """Список наборов данных (источников): имя, тип, число полей, наличие запроса."""
    out = []
    for ds in schema:
        if _local(ds.tag) != "dataSet":
            continue
        out.append({
            "name": _child_text(ds, "name"),
            "type": _data_set_type(ds),
            "fields_count": sum(1 for f in ds if _local(f.tag) == "field"),
            "has_query": _child(ds, "query") is not None,
        })
    return out


def parameters(schema: ET.Element) -> list:
    """Список параметров схемы: имя и заголовок."""
    out = []
    for p in schema:
        if _local(p.tag) != "parameter":
            continue
        out.append({
            "name": _child_text(p, "name"),
            "title": _localized(_child(p, "title")),
        })
    return out


def variants(schema: ET.Element) -> list:
    """Список вариантов настроек: имя и представление."""
    out = []
    for v in schema:
        if _local(v.tag) != "settingsVariant":
            continue
        out.append({
            "name": _child_text(v, "name"),
            "presentation": _localized(_child(v, "presentation")),
        })
    return out


def overview(schema: ET.Element) -> dict:
    """Уровень 1: обзор схемы (наборы, параметры, варианты)."""
    return {
        "data_sets": data_sets(schema),
        "parameters": parameters(schema),
        "variants": variants(schema),
    }


def dataset_detail(schema: ET.Element, name: str) -> dict:
    """Уровень 2: запрос и список полей конкретного набора данных."""
    target = None
    for ds in schema:
        if _local(ds.tag) == "dataSet" and _child_text(ds, "name") == name:
            target = ds
            break
    if target is None:
        raise ValueError(f"Набор данных '{name}' не найден")
    fields = []
    for f in target:
        if _local(f.tag) != "field":
            continue
        fields.append({
            "data_path": _child_text(f, "dataPath"),
            "field": _child_text(f, "field"),
            "title": _localized(_child(f, "title")),
        })
    query_el = _child(target, "query")
    return {
        "name": name,
        "type": _data_set_type(target),
        "query": (query_el.text or "") if query_el is not None else None,
        "fields": fields,
    }


# --- Экспорт/импорт через XML (штатный формат платформы) ---

def _register_prefixes():
    """Зарегистрировать канонические префиксы (кроме default) для сериализации."""
    for uri, pfx in _PREFIX_BY_URI.items():
        if pfx:
            ET.register_namespace(pfx, uri)


def _normalize_namespaces(text: str) -> str:
    """Заменить автогенерированные префиксы (`d4p1`, `d5p1`, ...) на канонические.

    В `.bin` префиксы генерируются локально (`xmlns:d5p1="...data/core"`), и в
    значениях `xsi:type="d5p1:LocalStringType"` они остаются «висячими» после
    пересборки через ElementTree. Здесь по маппингу префикс→URI заменяем их на
    фиксированные (`v8:`, `dcsset:` и т.п.), чтобы итоговый XML был валидным.
    """
    prefix_to_uri = dict(_NS_DECL_RE.findall(text))
    repl = {}
    for pfx, uri in prefix_to_uri.items():
        canon = _PREFIX_BY_URI.get(uri)
        if canon and canon != pfx:
            repl[pfx] = canon
    if not repl:
        return text
    out = text
    for pfx, canon in repl.items():
        out = out.replace(f"xmlns:{pfx}=", f"xmlns:{canon}=")
    for pfx, canon in sorted(repl.items(), key=lambda kv: -len(kv[0])):
        out = re.sub(rf"(?<![:\w.-]){re.escape(pfx)}:", canon + ":", out)
    return out


def _doc_text(doc: bytes) -> str:
    """Текст XML-документа без BOM (с объявления `<?xml`)."""
    text = doc.decode("utf-8-sig", "replace")
    return text[text.find(_PLAIN_XML.decode()):]


def export_xml(data: bytes) -> str:
    """Собрать `.bin` СКД в единый XML в штатном формате платформы.

    Результат: `<DataCompositionSchema>` (ns=schema), где настройки каждого
    варианта вложены в `<settingsVariant>` как `<dcsset:settings>`, а оформление
    из последнего `<SchemaFile>` (template/groupTemplate) добавлено в конец.
    """
    docs = split_documents(data)
    if not docs:
        raise ValueError("В файле нет XML-документов — это не макет СКД")

    schema = _child(ET.fromstring(_normalize_namespaces(_doc_text(docs[0]))),
                    "dataCompositionSchema")
    if schema is None:
        schema = ET.fromstring(_normalize_namespaces(_doc_text(docs[0])))

    settings_elems = []
    tail_children = []
    for doc in docs[1:]:
        root = ET.fromstring(_normalize_namespaces(_doc_text(doc)))
        rt = _local(root.tag)
        if rt == "Settings":
            settings_elems.append(root)
        elif rt == "SchemaFile":
            tail = _child(root, "dataCompositionSchema") or root
            tail_children = [c for c in tail]

    # Переименовать корень в DataCompositionSchema.
    schema.tag = f"{{{NS_SCHEMA}}}DataCompositionSchema"

    # Вложить настройки вариантов.
    svs = [c for c in schema if _local(c.tag) == "settingsVariant"]
    for i, sv in enumerate(svs):
        if i < len(settings_elems):
            se = settings_elems[i]
            se.tag = f"{{{NS_SETTINGS}}}settings"
            sv.append(se)

    # Добавить оформление (template/groupTemplate) в конец схемы.
    for c in tail_children:
        schema.append(c)

    _register_prefixes()
    ET.register_namespace("", NS_SCHEMA)
    body = ET.tostring(schema, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body


def _wrap_schema_file(inner: str) -> str:
    """Обернуть `<dataCompositionSchema ...>...</dataCompositionSchema>` в `<SchemaFile>`."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<SchemaFile xmlns="" xmlns:xs="{XSD}" xmlns:xsi="{XSI}">\n'
        f"{inner}\n"
        "</SchemaFile>"
    )


def build_bin(xml_text: str) -> bytes:
    """Собрать `.bin` СКД из XML в штатном формате платформы (обратная export_xml).

    Результат = бинарный префикс + doc[0] (`<SchemaFile>` с основной схемой) +
    по одному `<Settings>`-документу на вариант + последний `<SchemaFile>` с
    оформлением (template/groupTemplate) или пустой.
    """
    root = ET.fromstring(xml_text)
    _register_prefixes()

    # Основная схема (переименовать корень обратно в dataCompositionSchema).
    root.tag = f"{{{NS_SCHEMA}}}dataCompositionSchema"

    # Вынести настройки вариантов и оформление.
    settings_elems = []
    tail_children = []
    for sv in [c for c in root if _local(c.tag) == "settingsVariant"]:
        for c in list(sv):
            if _local(c.tag) == "settings":
                settings_elems.append(c)
                sv.remove(c)
    for c in list(root):
        if _local(c.tag) in _TAIL_ELEMENTS:
            tail_children.append(c)
            root.remove(c)

    # doc[0]: SchemaFile + dataCompositionSchema.
    ET.register_namespace("", NS_SCHEMA)
    schema_inner = ET.tostring(root, encoding="unicode")
    docs = [_BOM + _wrap_schema_file(schema_inner).encode("utf-8")]

    # Settings-документы.
    for se in settings_elems:
        se.tag = f"{{{NS_SETTINGS}}}Settings"
        ET.register_namespace("", NS_SETTINGS)
        body = ET.tostring(se, encoding="unicode")
        docs.append(_BOM + ('<?xml version="1.0" encoding="UTF-8"?>\n' + body).encode("utf-8"))

    # Tail: SchemaFile с оформлением (или пустой dataCompositionSchema).
    ET.register_namespace("", NS_SCHEMA)
    if tail_children:
        tail_el = ET.Element(f"{{{NS_SCHEMA}}}dataCompositionSchema")
        for c in tail_children:
            tail_el.append(c)
        tail_inner = ET.tostring(tail_el, encoding="unicode")
    else:
        tail_inner = f'<dataCompositionSchema xmlns="{NS_SCHEMA}"/>'
    docs.append(_BOM + _wrap_schema_file(tail_inner).encode("utf-8"))

    # Бинарный префикс: заголовок + K + размеры всех документов, кроме последнего.
    k = len(settings_elems)
    prefix = struct.pack("<I", 0) + struct.pack("<I", k)
    for doc in docs[:-1]:
        prefix += struct.pack("<Q", len(doc))
    return prefix + b"".join(docs)
