"""Декодирование MXL (MOXCEL) — табличный документ 1С. Только чтение.

MXL-файл = магия ``MOXCEL`` + бинарный заголовок + UTF-8 BOM + brace-текст
(позиционная структура ``{...}``, как у контейнера 1С). Тело разбирается штатным
декодером v8unpack (`JsonContainerDecoder`) в дерево вложенных списков/строк.
"""

import io
import json

from v8unpack.json_container_decoder import JsonContainerDecoder

MAGIC = b"MOXCEL"
_BOM = b"\xef\xbb\xbf"

# MOXCEL (6 байт) + бинарный заголовок (7 байт) + BOM (3 байта) = 16 байт до тела.
_HEADER_LEN = 16


def _body(data: bytes) -> bytes:
    if not data.startswith(MAGIC):
        raise ValueError("Не табличный документ 1С: нет магии 'MOXCEL'")
    idx = data.find(_BOM, len(MAGIC))
    if idx < 0:
        idx = _HEADER_LEN
    else:
        idx += len(_BOM)
    return data[idx:]


def decode(data: bytes):
    """Разобрать MXL в дерево вложенных списков/строк (позиционная структура)."""
    text = _body(data).decode("utf-8")
    return JsonContainerDecoder().decode_file(io.StringIO(text))


def to_text(tree) -> str:
    """Канонический текст дерева — стабильная форма для вывода и сравнения."""
    return json.dumps(tree, ensure_ascii=False, indent=1)


def _walk(node):
    if isinstance(node, list):
        for child in node:
            yield from _walk(child)
    else:
        yield node


def iter_strings(tree):
    """Все строковые значения дерева, без обрамляющих кавычек."""
    for elem in _walk(tree):
        if isinstance(elem, str):
            s = elem
            if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
                s = s[1:-1]
            yield s
