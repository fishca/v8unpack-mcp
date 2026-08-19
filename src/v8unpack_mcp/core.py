"""Ядро: полный цикл unpack → чтение/правка → repack → cleanup.

Единственная точка распаковки — `unpack` (в отдельный временный каталог, без лимита
размера). Все остальные инструменты принимают `dir_path` — каталог, созданный `unpack`,
и не выполняют неявной распаковки.
"""

import base64
import contextlib
import io
import json
import os
import re
import shutil
import tempfile
import uuid as uuid_mod

from v8unpack import extract, build
from v8unpack import helper as v8helper
from v8unpack.organizer_file import OrganizerFile

# Файлы 1С, которые умеем читать.
SUPPORTED_EXTENSIONS = {".cf", ".cfe", ".epf", ".erf"}

# Имя raw-слоя (снапшот stage1) внутри каталога unpack.
RAW_DIR = ".v8unpack_raw"

# Префикс временных каталогов unpack — по нему cleanup(all=True) находит свои каталоги.
_UNPACK_PREFIX = "v8unpack_unpack_"


# --- Серийный пул: отключаем multiprocessing, чтобы не ломать stdio-протокол MCP ---

class _SerialPool:
    def imap_unordered(self, method, list_args, chunksize=1):
        for args in list_args:
            yield method(args)

    def close(self):
        pass

    def join(self):
        pass


_SERIAL_POOL = _SerialPool()
_original_get_pool = v8helper.get_pool


def _serial_get_pool(*, pool=None, processes=None):
    if pool is not None:
        return pool
    return _SERIAL_POOL


# Подменяем get_pool, чтобы v8unpack.extract()/build() работали в одном процессе
# (multiprocessing на Windows спавнит дочерние процессы, которые переоткрывают
# main-модуль и ломают stdio-транспорт MCP).
v8helper.get_pool = _serial_get_pool


def _list_descent_dir_skip_raw(cls, src_dir, path, descent):
    """Обход организованного дерева, пропуская raw-слой (.v8unpack_raw)."""
    return [e for e in os.listdir(os.path.join(src_dir, path)) if e != RAW_DIR]


# Пропускаем .v8unpack_raw при pack/unpack организованного дерева, иначе OrganizerFile.pack
# спотыкается о brace-файлы raw-слоя, когда организованное дерево отредактировано.
OrganizerFile.list_descent_dir = classmethod(_list_descent_dir_skip_raw)


def _extract_silent(path: str, out_dir: str, options: dict = None) -> None:
    """Распаковка с подавлением stdout/stderr (v8unpack печатает прогресс)."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        extract(path, out_dir, options=options)


def _build_silent(in_dir: str, out_file: str, options: dict = None) -> None:
    """Сборка с подавлением stdout/stderr (v8unpack печатает прогресс)."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        build(in_dir, out_file, options=options)


def _require_dir(dir_path: str) -> str:
    """Проверить, что каталог (результат unpack) существует, и вернуть абсолютный путь."""
    dir_path = os.path.abspath(dir_path)
    if not os.path.isdir(dir_path):
        raise FileNotFoundError(f"Каталог не найден: {dir_path}")
    return dir_path


# --- Инструменты ---

def list_objects(dir_path: str) -> dict:
    """Дерево объектов ВНУТРИ распакованного контейнера: {вид_объекта: [имена]}.

    Для .cf/.cfe — вложенная структура `Вид/Имя/...` (имя объекта без метаданных).
    Для .epf/.erf — «плоская»: корневой объект в `@files`, формы в каталоге `Form`.

    Лёгкий обзор «что внутри» (только имена). Полные метаданные объекта (uuid, синоним,
    формы, макеты, модули) — `get_metadata`.
    """
    root = _require_dir(dir_path)
    result = {}
    for entry in sorted(os.listdir(root)):
        if entry.startswith("."):
            continue
        full = os.path.join(root, entry)
        if os.path.isdir(full):
            result[entry] = sorted(os.listdir(full))
        elif os.path.isfile(full):
            result.setdefault("@files", []).append(entry)
    return result


def _file_info(path: str) -> dict:
    path = os.path.abspath(path)
    st = os.stat(path)
    return {
        "path": path,
        "name": os.path.basename(path),
        "ext": os.path.splitext(path)[1].lower().lstrip("."),
        "size": st.st_size,
    }


def _meta_kind_dir(root: str) -> str:
    """Вид распакованного каталога по маркерным .json файлам корня."""
    if os.path.isfile(os.path.join(root, "ConfigurationExtension.json")):
        return "extension"
    if os.path.isfile(os.path.join(root, "Configuration.json")):
        return "configuration"
    if os.path.isfile(os.path.join(root, "ExternalDataProcessor.json")):
        return "external_data_processor"
    if os.path.isfile(os.path.join(root, "ExternalReport.json")):
        return "external_report"
    return "unknown"


def _object_forms(obj_dir: str) -> list:
    """Формы объекта: подкаталоги, чьё имя заканчивается на Form (или Forms/Form).

    Возвращает [{dir: имя_каталога_форм, forms: [имена_форм]}].
    """
    forms = []
    for entry in sorted(os.listdir(obj_dir)):
        full = os.path.join(obj_dir, entry)
        if not os.path.isdir(full):
            continue
        if entry.endswith("Form") or entry.lower() in ("forms", "form"):
            names = []
            for s in sorted(os.listdir(full)):
                sfull = os.path.join(full, s)
                if os.path.isdir(sfull):
                    names.append(s)
                elif s.endswith(".json") and not s.endswith(".id.json"):
                    names.append(os.path.splitext(s)[0])
            if names:
                forms.append({"dir": entry, "forms": names})
    return forms


def _object_templates(obj_dir: str) -> list:
    """Макеты объекта: подкаталоги Template(s) и подкаталоги с 'Template' в имени.

    Возвращает [{dir: имя_каталога, items: [имена_макетов]}].
    """
    templates = []
    for entry in sorted(os.listdir(obj_dir)):
        full = os.path.join(obj_dir, entry)
        if not os.path.isdir(full):
            continue
        if "Template" in entry or entry.lower() in ("template", "templates"):
            items = []
            for s in sorted(os.listdir(full)):
                if os.path.isdir(os.path.join(full, s)):
                    items.append(s)
                elif os.path.isfile(os.path.join(full, s)) and not s.endswith(".id.json"):
                    items.append(s)
            if items:
                templates.append({"dir": entry, "items": items})
    return templates


def _object_info(obj_dir: str, type_name: str) -> dict:
    """Метаданные одного объекта по каталогу распакованного дерева."""
    info = {"name": os.path.basename(obj_dir), "meta_type": type_name}
    for fn in sorted(os.listdir(obj_dir)):
        full = os.path.join(obj_dir, fn)
        if not os.path.isfile(full):
            continue
        if fn.endswith(".id.json"):
            info["uuid"] = json.load(open(full, encoding="utf-8")).get("uuid")
        elif fn.endswith(".json") and not fn.endswith(".elem.json") and not fn.endswith(".id.json"):
            try:
                data = json.load(open(full, encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict):
                if data.get("name"):
                    info["name"] = data["name"]
                n2 = data.get("name2")
                if isinstance(n2, dict) and n2.get("ru"):
                    info["synonym"] = n2["ru"]
                if data.get("comment"):
                    info["comment"] = data["comment"]
        elif fn.endswith(".obj.bsl"):
            info["has_module"] = True
        elif fn.endswith(".obj.bin"):
            info["has_encrypted_module"] = True
    forms = _object_forms(obj_dir)
    if forms:
        info["forms"] = forms
    tpl = _object_templates(obj_dir)
    if tpl:
        info["templates"] = tpl
    return info


def _cfg_type_dirs(root: str) -> list:
    """Каталоги-типы верхнего уровня конфигурации (без служебных)."""
    types = []
    for entry in sorted(os.listdir(root)):
        if entry.startswith("."):
            continue
        full = os.path.join(root, entry)
        if os.path.isdir(full):
            types.append(entry)
    return types


def _cfg_object_metadata(root: str, object_path: str) -> dict:
    parts = [p for p in object_path.strip("/").split("/") if p]
    if len(parts) < 2:
        raise ValueError(f"object_path должен быть вида 'Тип/Имя', получено: {object_path!r}")
    type_name, name = parts[0], parts[1]
    obj_dir = os.path.join(root, type_name, name)
    if not os.path.isdir(obj_dir):
        raise ValueError(f"Объект не найден: {object_path}")
    return _object_info(obj_dir, type_name)


def _cfg_object_listing(root: str, limit: int = 500) -> dict:
    """Полный список объектов конфигурации по типам (с именами и uuid)."""
    result = {}
    count = 0
    for type_name in _cfg_type_dirs(root):
        type_dir = os.path.join(root, type_name)
        items = []
        for name in sorted(os.listdir(type_dir)):
            obj_dir = os.path.join(type_dir, name)
            if not os.path.isdir(obj_dir):
                continue
            items.append(_object_info(obj_dir, type_name))
            count += 1
            if count >= limit:
                items.append({"truncated": True, "note": f"лимит {limit} объектов достигнут"})
                break
        if items:
            result[type_name] = items
        if count >= limit:
            break
    return result


def get_metadata(dir_path: str, object_path: str = "", detail: bool = False) -> dict:
    """Метаданные распакованного каталога (конфигурация/расширение/обработка/отчёт).

    Без object_path — сводка: вид, счётчики по типам объектов, общее число.
    С object_path='Тип/Имя' — детальные метаданные одного объекта
    (имя, синоним, uuid, формы, макеты, модули).
    detail=True (без object_path) — полный список объектов по типам.
    """
    root = _require_dir(dir_path)
    kind = _meta_kind_dir(root)
    result = {"dir": root, "kind": kind}

    if kind in ("external_data_processor", "external_report"):
        obj_dir = root
        if object_path:
            parts = [p for p in object_path.strip("/").split("/") if p]
            if len(parts) >= 2:
                cand = os.path.join(root, *parts)
                if os.path.isdir(cand):
                    obj_dir = cand
        result["object"] = _object_info(obj_dir, "ExternalDataProcessor" if kind == "external_data_processor" else "ExternalReport")
        return result

    type_counts = {}
    for t in _cfg_type_dirs(root):
        type_counts[t] = len(os.listdir(os.path.join(root, t)))
    result["total_objects"] = sum(type_counts.values())
    result["type_counts"] = type_counts
    if object_path:
        result["object"] = _cfg_object_metadata(root, object_path)
    elif detail:
        result["objects"] = _cfg_object_listing(root)
    return result


def _find_module_files(obj_dir: str) -> list:
    """Модульные файлы объекта: *.obj.bsl (текст) и *.obj.bin (шифрованный)."""
    modules = []
    for dirpath, _dirnames, filenames in os.walk(obj_dir):
        for fn in filenames:
            if fn.endswith(".obj.bsl") or fn.endswith(".obj.bin"):
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, obj_dir)
                modules.append((rel, full))
    return sorted(modules)


def read_module(dir_path: str, object_path: str = "", module_name: str = ""):
    """Прочитать BSL-модуль объекта из распакованного каталога.

    `object_path` — путь к объекту относительно корня распаковки ("" для корневого
    объекта .epf/.erf, "CommonModule/Имя" для .cf/.cfe и т.п.).

    Без module_name — вернуть список модулей объекта (имя + вид).
    С module_name — вернуть содержимое выбранного модуля (подстрока имени файла).
    """
    root = _require_dir(dir_path)
    obj_dir = os.path.join(root, object_path) if object_path else root
    if not os.path.isdir(obj_dir):
        raise FileNotFoundError(f"Объект {object_path or '<корень>'} не найден")
    modules = _find_module_files(obj_dir)
    if not modules:
        return {"object": object_path or "<корень>", "modules": []}

    if not module_name:
        listing = []
        for rel, full in modules:
            kind = "encrypted" if rel.endswith(".obj.bin") else "bsl"
            listing.append({"name": rel, "kind": kind})
        return {"object": object_path or "<корень>", "modules": listing}

    target = None
    for rel, full in modules:
        if module_name in rel:
            target = (rel, full)
            break
    if target is None:
        raise FileNotFoundError(f"Модуль {module_name} не найден в {object_path or '<корень>'}")

    rel, full = target
    with open(full, "rb") as f:
        data = f.read()
    if rel.endswith(".obj.bin"):
        return {
            "object": object_path or "<корень>",
            "module": rel,
            "kind": "encrypted",
            "encoding": "bin",
            "content": None,
            "note": "Модуль защищён (зашифрован). Исходник без пароля не восстановить.",
        }
    from .textlayers import decode_text
    return {
        "object": object_path or "<корень>",
        "module": rel,
        "kind": "bsl",
        "encoding": "utf-8-sig",
        "content": decode_text(data),
    }


def read_bytecode(dir_path: str, object_path: str = ""):
    """Разобрать байт-код (`image`) закрытого модуля из raw-слоя каталога unpack.

    Возвращает список методов (имена, флаги, число параметров, адрес в потоке опкодов)
    и счётчики опкодов/констант. НЕ дешифрует исходник — читает компилированный байт-код.
    """
    from . import bytecode
    root = _require_dir(dir_path)
    uuid = bytecode.module_uuid(root, object_path)
    img = bytecode.read_module_image_from_raw(os.path.join(root, RAW_DIR), uuid)
    return {
        "object": object_path or "<корень>",
        "uuid": uuid,
        "opcodes": len(bytecode.opcodes(img)),
        "constants": len(bytecode.constants(img)),
        "methods": bytecode.methods(img),
    }


def _find_matches(text: str, pattern: str, regex: bool, case_sensitive: bool, max_per_file: int):
    flags = 0 if case_sensitive else re.IGNORECASE
    if regex:
        try:
            rx = re.compile(pattern, flags)
        except re.error as err:
            raise ValueError(f"Некорректное регулярное выражение: {err}")
    else:
        rx = re.compile(re.escape(pattern), flags)
    matches = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if rx.search(line):
            matches.append({"line": line_no, "text": line.rstrip()[:500]})
            if len(matches) >= max_per_file:
                break
    return matches


def search_code(dir_path: str, pattern: str, regex: bool = False,
                case_sensitive: bool = False, max_results: int = 200, layers=None):
    """Поиск по текстовым слоям одного распакованного каталога.

    layers — список областей поиска: modules, forms, templates_text, templates_html,
    dcc. Пусто/None → все. Каждое совпадение получает поле "layer".
    Возвращает {"matches": [...], "truncated": bool}.
    """
    if not pattern:
        return {"matches": [], "truncated": False}
    from .textlayers import iter_searchable_files, normalize_layers
    root = _require_dir(dir_path)
    wanted = normalize_layers(layers)
    matches = []
    truncated = False
    for rel, text, layer in iter_searchable_files(root, wanted):
        found = _find_matches(text, pattern, regex, case_sensitive, max_per_file=max_results - len(matches))
        for m in found:
            matches.append({
                "path": rel,
                "layer": layer,
                "line": m["line"],
                "text": m["text"],
            })
            if len(matches) >= max_results:
                truncated = True
                return {"matches": matches, "truncated": truncated}
    return {"matches": matches, "truncated": truncated}


# --- Полный цикл распаковки/сборки в отдельный каталог ---

def unpack(file_path: str) -> dict:
    """Полная распаковка файла 1С (.cf/.cfe/.epf/.erf) в отдельный временный каталог.

    Без лимита размера, для любых объектов. Возвращает путь к каталогу, где агент
    может читать/править файлы:
    - корень — организованное дерево (`Тип/Имя` + .json/.obj.bsl/формы/макеты);
    - `.v8unpack_raw/` — raw-слой (brace-файлы: text/image/help) для read_bytecode/set_help.
    """
    file_path = os.path.abspath(file_path)
    if not os.path.isfile(file_path):
        raise FileNotFoundError(file_path)
    ext = os.path.splitext(file_path)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Неподдерживаемый тип файла: {ext!r} (ожидается .cf/.cfe/.epf/.erf)")
    out_dir = tempfile.mkdtemp(prefix=_UNPACK_PREFIX)
    _extract_silent(file_path, out_dir, options={"keep_raw": True})
    return {
        "status": "ok",
        "dir": out_dir,
        "file": _file_info(file_path),
        "kind": _meta_kind_dir(out_dir),
    }


def repack(dir_path: str, output_path: str) -> dict:
    """Собрать файл 1С из распакованного каталога (результат unpack) в целевой файл.

    dir_path — каталог, возвращённый unpack; output_path — путь к собираемому файлу.
    Сборка использует raw-слой (.v8unpack_raw), если организованное дерево не правилось.
    """
    dir_path = _require_dir(dir_path)
    output_path = os.path.abspath(output_path)
    parent = os.path.dirname(output_path)
    if not os.path.isdir(parent):
        raise FileNotFoundError(f"Каталог назначения не существует: {parent}")
    _build_silent(dir_path, output_path, options={"use_raw": True})
    return {
        "status": "ok",
        "output": output_path,
        "bytes": os.path.getsize(output_path),
    }


# --- Справочная информация (help) ---

# help_file_number по типу метаданных (как в MetaDataObject/*.py v8unpack).
# None — тип не поддерживает справку (например CommonModule).
_HELP_FILE_NUMBER = {
    "DataProcessor": 1,
    "ExternalDataProcessor": 1,
    "Report": 1,
    "ExternalReport": 1,
    "Catalog": 1,
    "Document": 1,
    "CommonCommand": 1,
    "ChartOfCalculationTypes": 1,
    "BusinessProcess": 5,
    "Task": 5,
    "ChartOfCharacteristicType": 5,
    "ChartOfAccounts": 5,
    "AccountingRegister": 5,
}


def _help_file_number(meta_type: str):
    return _HELP_FILE_NUMBER.get(meta_type)


def _resolve_help_target(root: str, object_path: str):
    """Найти {uuid, тип} объекта по распакованному (organized) дереву.

    object_path="" — корневой ExternalDataProcessor/ExternalReport (внешняя обработка/отчёт).
    object_path="Тип/Имя" — объект конфигурации/расширения.
    """
    if not object_path:
        for tname in ("ExternalDataProcessor", "ExternalReport"):
            jf = os.path.join(root, f"{tname}.json")
            if os.path.isfile(jf):
                data = json.load(open(jf, encoding="utf-8"))
                return data.get("uuid"), tname
        raise ValueError("Не найден объект внешней обработки/отчёта (ExternalDataProcessor/ExternalReport)")

    parts = [p for p in object_path.strip("/").split("/") if p]
    if len(parts) < 2:
        raise ValueError(f"object_path должен быть вида 'Тип/Имя' или пустым, получено: {object_path!r}")
    obj_dir = os.path.join(root, *parts)
    if not os.path.isdir(obj_dir):
        raise ValueError(f"Объект не найден по пути: {object_path}")
    jsons = [f for f in os.listdir(obj_dir) if f.endswith(".json") and not f.endswith(".id.json")]
    if not jsons:
        raise ValueError(f"Не найден json объекта по пути: {object_path}")
    data = json.load(open(os.path.join(obj_dir, jsons[0]), encoding="utf-8"))
    meta_type = os.path.splitext(jsons[0])[0]
    return data.get("uuid"), meta_type


def set_help(dir_path: str, object_path: str = "", help_html: str = "",
             overwrite: bool = False) -> dict:
    """Записать справочную информацию (help) объекта в raw-слой каталога unpack.

    Сборку НЕ выполняет — это делает repack. Пишет base64-HTML в
    `<uuid>.<help_file_number>` внутри `.v8unpack_raw/0/` и перегенерирует `versions`
    (свежие uuid), чтобы конфигуратор увидел изменение.

    - object_path: пусто — для внешней обработки/отчёта (сам объект);
      'Тип/Имя' — для объекта конфигурации/расширения.
    - overwrite: false (по умолчанию) — существующую справку не перезаписываем
      (вернёт {ok: false, skipped: true}); true — заменить.
    """
    root = _require_dir(dir_path)
    if not help_html:
        raise ValueError("help_html обязателен")

    obj_uuid, meta_type = _resolve_help_target(root, object_path)
    help_num = _help_file_number(meta_type)
    if help_num is None:
        raise ValueError(f"Тип '{meta_type}' не поддерживает справочную информацию (object_path={object_path or '(корень)'})")

    d0 = os.path.join(root, RAW_DIR, "0")
    if not os.path.isdir(d0):
        raise FileNotFoundError(f"В каталоге нет raw-слоя {RAW_DIR}/0 — unpack должен выполняться с keep_raw")

    help_file = f"{obj_uuid}.{help_num}"
    help_file_path = os.path.join(d0, help_file)
    existed = os.path.isfile(help_file_path)
    if existed and not overwrite:
        return {
            "ok": False,
            "skipped": True,
            "message": "Справка для этого объекта уже существует, пропущено (overwrite=false)",
            "object_path": object_path or "(корень)",
            "meta_type": meta_type,
            "uuid": obj_uuid,
            "help_file": help_file,
        }

    b64 = base64.b64encode(help_html.encode("utf-8")).decode()
    help_data = [["5", "1", '"ru"', ["#base64:" + b64], "0"]]
    v8helper.brace_file_write(help_data, d0, help_file)

    entries = sorted(os.listdir(d0))
    parts = ["{1", str(len(entries) + 1), '""', str(uuid_mod.uuid4())]
    for name in entries:
        parts.append(f'"{name}"')
        parts.append(str(uuid_mod.uuid4()))
    with open(os.path.join(d0, "versions"), "w", encoding="utf-8") as f:
        f.write(",".join(parts) + "}")

    return {
        "ok": True,
        "overwritten": existed,
        "object_path": object_path or "(корень)",
        "meta_type": meta_type,
        "uuid": obj_uuid,
        "help_file": help_file,
    }


# --- Макеты (табличные документы MXL) ---

def _find_template_dir(obj_dir: str) -> str:
    """Каталог макетов объекта (Template или Templates)."""
    for name in ("Template", "Templates"):
        p = os.path.join(obj_dir, name)
        if os.path.isdir(p):
            return p
    for entry in sorted(os.listdir(obj_dir)):
        p = os.path.join(obj_dir, entry)
        if os.path.isdir(p) and "Template" in entry:
            return p
    raise ValueError(f"Каталог макетов (Template) не найден в {obj_dir}")


def _template_items(template_dir: str) -> list:
    """Табличные макеты (type='table') в каталоге макетов: [{name, dir, mxl, uuid, type}].

    Табличный макет определяется наличием Template.mxl (СКД и прочие хранятся как
    Template.bin / Template.json и сюда не попадают).
    """
    items = []
    for entry in sorted(os.listdir(template_dir)):
        p = os.path.join(template_dir, entry)
        if not os.path.isdir(p):
            continue
        mxl = os.path.join(p, "Template.mxl")
        if not os.path.isfile(mxl):
            continue
        ttype = None
        tjson = os.path.join(p, "Template.json")
        if os.path.isfile(tjson):
            try:
                ttype = json.load(open(tjson, encoding="utf-8")).get("type")
            except (OSError, ValueError):
                pass
        if ttype and ttype != "table":
            continue
        uuid = None
        idjson = os.path.join(p, "Template.id.json")
        if os.path.isfile(idjson):
            try:
                uuid = json.load(open(idjson, encoding="utf-8")).get("uuid")
            except (OSError, ValueError):
                pass
        items.append({"name": entry, "dir": p, "mxl": mxl, "uuid": uuid, "type": ttype or "table"})
    return items


def import_template(dir_path: str, object_path: str = "", template_path: str = "",
                    template_name: str = None) -> dict:
    """Импортировать готовый .mxl (табличный документ) — замена содержимого существующего макета.

    Пишет в raw-слой (.v8unpack_raw/0/<uuid>.0), НЕ трогая организованное дерево,
    поэтому repack идёт без полной пересборки и без потерь.

    template_name — имя макета; пусто — если табличный макет ровно один.
    object_path: '' — корневой объект .epf/.erf; 'Тип/Имя' — объект конфигурации/расширения.
    Сборку НЕ выполняет — это делает repack.
    """
    root = _require_dir(dir_path)
    if not template_path:
        raise ValueError("template_path обязателен (путь к файлу .mxl)")
    template_path = os.path.abspath(template_path)
    if not os.path.isfile(template_path):
        raise FileNotFoundError(template_path)

    with open(template_path, "rb") as f:
        src_bytes = f.read()
    if not src_bytes.startswith(b"MOXCEL"):
        raise ValueError(f"Файл не является табличным документом 1С (нет магии 'MOXCEL'): {template_path}")

    obj_dir = os.path.join(root, object_path) if object_path else root
    if not os.path.isdir(obj_dir):
        raise FileNotFoundError(f"Объект {object_path or '<корень>'} не найден")
    tdir = _find_template_dir(obj_dir)
    items = _template_items(tdir)

    target = None
    if template_name:
        for it in items:
            if it["name"] == template_name:
                target = it
                break
        if target is None:
            raise ValueError(f"Макет '{template_name}' не найден; табличные макеты: {[i['name'] for i in items]}")
    else:
        if len(items) == 1:
            target = items[0]
        elif not items:
            raise ValueError("В объекте нет табличных макетов для замены")
        else:
            raise ValueError(f"Табличных макетов несколько — укажите template_name: {[i['name'] for i in items]}")

    if not target["uuid"]:
        raise ValueError(f"У макета '{target['name']}' не определён uuid (нет Template.id.json)")
    d0 = os.path.join(root, RAW_DIR, "0")
    if not os.path.isdir(d0):
        raise FileNotFoundError(f"В каталоге нет raw-слоя {RAW_DIR}/0 — unpack должен выполняться с keep_raw")
    raw_file = os.path.join(d0, f"{target['uuid']}.0")
    if not os.path.isfile(raw_file):
        raise FileNotFoundError(f"raw-файл содержимого макета не найден: {raw_file}")
    with open(raw_file, "wb") as f:
        f.write(src_bytes)
    return {
        "ok": True,
        "object_path": object_path or "<корень>",
        "template_name": target["name"],
        "uuid": target["uuid"],
        "raw_file": raw_file,
        "bytes": len(src_bytes),
        "note": "Записано в raw-слой (.v8unpack_raw/0/<uuid>.0); repack возьмёт его без полной пересборки. Организованный Template.mxl намеренно не менялся.",
    }


def _pick_template(items: list, template_name: str, what: str) -> dict:
    """Выбрать один табличный макет по имени (или единственный)."""
    if template_name:
        for it in items:
            if it["name"] == template_name:
                return it
        raise ValueError(f"Макет '{template_name}' не найден; табличные макеты: {[i['name'] for i in items]}")
    if len(items) == 1:
        return items[0]
    if not items:
        raise ValueError(f"Нет табличных макетов для {what}")
    raise ValueError(f"Табличных макетов несколько — укажите template_name: {[i['name'] for i in items]}")


def _read_template_bytes(root: str, target: dict) -> bytes:
    """Содержимое макета: из raw-слоя (актуально после import_template), иначе organized."""
    if target.get("uuid"):
        raw = os.path.join(root, RAW_DIR, "0", f"{target['uuid']}.0")
        if os.path.isfile(raw):
            with open(raw, "rb") as f:
                return f.read()
    with open(target["mxl"], "rb") as f:
        return f.read()


def read_template(dir_path: str, object_path: str = "", template_name: str = None) -> dict:
    """Прочитать табличный документ (MXL) макета в структурированном виде.

    Декодирует MOXCEL в дерево (позиционная структура) и возвращает канонический
    текст (`structure`) + список текстовых значений (`strings`). Только чтение.
    """
    from . import mxl
    root = _require_dir(dir_path)
    obj_dir = os.path.join(root, object_path) if object_path else root
    if not os.path.isdir(obj_dir):
        raise FileNotFoundError(f"Объект {object_path or '<корень>'} не найден")
    tdir = _find_template_dir(obj_dir)
    items = _template_items(tdir)
    target = _pick_template(items, template_name, "чтения")

    data = _read_template_bytes(root, target)
    tree = mxl.decode(data)
    strings = list(dict.fromkeys(mxl.iter_strings(tree)))
    return {
        "object_path": object_path or "<корень>",
        "template_name": target["name"],
        "uuid": target["uuid"],
        "bytes": len(data),
        "top_level_elements": len(tree) if isinstance(tree, list) else 1,
        "strings": strings[:300],
        "strings_count": len(strings),
        "structure": mxl.to_text(tree),
    }


# --- Очистка ---

def cleanup(dir_path: str = None, all: bool = False) -> dict:
    """Удалить временный каталог unpack.

    dir_path — удалить один каталог (должен быть создан unpack, т.е. иметь префикс
    'v8unpack_unpack_'); all=true — удалить ВСЕ каталоги unpack в temp по префиксу.
    """
    removed = []
    if dir_path is not None:
        target = os.path.abspath(dir_path)
        base = os.path.basename(os.path.normpath(target))
        if base.startswith(_UNPACK_PREFIX) and os.path.isdir(target):
            shutil.rmtree(target, ignore_errors=True)
            removed.append(target)
        else:
            raise ValueError(f"Отказано: {target!r} не является временным каталогом unpack (префикс '{_UNPACK_PREFIX}')")
    elif all:
        tmp = tempfile.gettempdir()
        for name in os.listdir(tmp):
            if name.startswith(_UNPACK_PREFIX):
                p = os.path.join(tmp, name)
                if os.path.isdir(p):
                    shutil.rmtree(p, ignore_errors=True)
                    removed.append(p)
    else:
        raise ValueError("Укажите dir_path или all=true")
    return {"status": "ok", "removed": removed, "count": len(removed)}
