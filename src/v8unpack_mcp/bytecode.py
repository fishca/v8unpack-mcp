"""Разбор байт-кода (`image`) закрытых модулей 1С.

Файл `image` — это сериализованный «скобкофайл» с пятью секциями:
  [0] "1"      — версия;
  [1] "Cmd"    — поток опкодов (стековая машина 1С), каждый [опкод, аргумент];
  [2] "Const"  — константы (строки/числа/даты и т.д.);
  [3] "Var"    — глобальные переменные модуля;
  [4] "Proc"   — таблица методов (имена, флаги, адреса в потоке опкодов).

Это НЕ дешифровка текста (он зашифрован), а чтение компилированного байт-кода,
который платформа хранит рядом с шифрованным исходником и не шифрует.

Формат стека и таблица опкодов основаны на открытых проектах EvilBeaver/v8asm (MIT)
и 1C-inversion (см. README, раздел «Заимствованные компоненты»).
"""

import os

from v8unpack.json_container_decoder import JsonContainerDecoder


class _SerialPool:
    def imap_unordered(self, method, list_args, chunksize=1):
        for args in list_args:
            yield method(args)

    def close(self):
        pass

    def join(self):
        pass


def _unq(s):
    """Снять кавычки со строкового значения скобкофайла ('"..."' -> ...)."""
    if isinstance(s, str) and len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    return s


def _parse_brace_file(path):
    dec = JsonContainerDecoder(src_dir=os.path.dirname(path), file_name=os.path.basename(path))
    with open(path, "r", encoding="utf-8-sig") as f:
        return dec.decode_file(f)


def parse_image(parsed):
    top = parsed[0]
    result = {"version": top[0], "cmd": [], "const": [], "var": [], "lbl": [], "proc": []}
    # Секции идут после версии и начинаются со своего имени ("Cmd"/"Const"/"Var"/"Lbl"/"Proc").
    # У обычных модулей порядок: Cmd, Const, Var, Proc (5 секций).
    # У модулей ФОРМ добавляется Lbl перед Proc (6 секций), но Var/Lbl могут отсутствовать.
    # Ищем по имени, а не по индексу; отсутствующие секции остаются пустыми списками.
    for sec in top[1:]:
        if isinstance(sec, list) and sec and isinstance(sec[0], str):
            name = _unq(sec[0]).lower()
            if name in ("cmd", "const", "var", "lbl", "proc"):
                result[name] = sec
    return result


def opcodes(image):
    """Список [опкод, аргумент] из секции Cmd."""
    cmd = image["cmd"]
    # заголовок ["Cmd", count, -1]
    start = 3 if (len(cmd) >= 3 and cmd[0] == '"Cmd"') else 0
    return [[int(_unq(o[0])), int(_unq(o[1]))] for o in cmd[start:]]


def constants(image):
    """Список [тип, значение] из секции Const."""
    const = image["const"]
    start = 2 if (len(const) >= 2 and const[0] == '"Const"') else 0
    return [[_unq(c[0]), _unq(c[1])] for c in const[start:] if isinstance(c, list) and len(c) >= 2]


def methods(image):
    """Список методов из секции Proc.

    Каждый метод: {name, flags, params, op_addr, anonymous}.
    Запись метода: [имя, флаги, кол-во_параметров, адрес_в_Cmd, [Var...], [DefPrm...]].
    """
    proc = image["proc"]
    result = []
    for entry in proc:
        if not isinstance(entry, list) or not entry:
            continue
        name = _unq(entry[0])
        if not isinstance(name, str) or not name:
            continue
        # метод имеет числовые флаги и адрес; непустой читаемый флаг (16/17/0/...)
        flags = entry[1] if len(entry) > 1 else None
        if not isinstance(flags, str) or not flags.isdigit():
            continue
        params = int(_unq(entry[2])) if len(entry) > 2 and isinstance(entry[2], str) and entry[2].lstrip("-").isdigit() else 0
        op_addr = None
        for f in entry[3:]:
            if isinstance(f, str) and f.isdigit():
                op_addr = int(f)
                break
        result.append({
            "name": name,
            "flags": int(flags),
            "params": params,
            "op_addr": op_addr,
            "anonymous": name.startswith("x"),
        })
    return result


def read_module_image_from_raw(raw_dir, uuid):
    """Извлечь и разобрать `image` (байт-код) модуля по uuid из raw-слоя каталога unpack.

    raw_dir — каталог `.v8unpack_raw` (снапшот stage1); структура `<uuid>.0/image`.
    """
    for root, dirnames, _filenames in os.walk(raw_dir):
        for d in dirnames:
            if d.startswith(uuid):
                image_path = os.path.join(root, d, "image")
                if os.path.isfile(image_path):
                    return parse_image(_parse_brace_file(image_path))
    raise FileNotFoundError(f"Байт-код модуля {uuid} не найден в raw-слое {raw_dir}")


def module_uuid(extract_root, object_path):
    """uuid объекта из его .id.json в организованном дереве.

    Для корня epf/erf .id.json нет — uuid берём из json метаданных объекта
    (например ExternalDataProcessor.json, поле "uuid")."""
    import json
    import re

    obj_dir = os.path.join(extract_root, object_path) if object_path else extract_root
    for fn in os.listdir(obj_dir):
        if fn.endswith(".id.json"):
            with open(os.path.join(obj_dir, fn), "r", encoding="utf-8") as f:
                return json.load(f)["uuid"]
    for fn in os.listdir(obj_dir):
        if re.fullmatch(r"[^.]+\.json", fn):
            path = os.path.join(obj_dir, fn)
            try:
                with open(path, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and data.get("uuid"):
                return data["uuid"]
    raise FileNotFoundError(f"uuid объекта {object_path or '<корень>'} не найден")
