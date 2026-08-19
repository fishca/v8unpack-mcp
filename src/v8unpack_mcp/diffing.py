# -*- coding: utf-8 -*-
"""Сравнение двух распакованных каталогов 1С (результат unpack).

Сравнивает организованные деревья пообъектно: собирает файлы каждого объекта
(кроме служебного .id.json), находит изменённые/добавленные/удалённые и строит
unified diff по изменённым. Неявной распаковки нет — оба каталога должны быть
созданы инструментом unpack.
"""

import difflib
import os

from . import core
from . import mxl

# Максимум строк диффа на файл и максимум файлов на объект.
MAX_DIFF_LINES = 400
MAX_DIFF_FILES = 20


def _collect_files(d):
    """Собрать файлы каталога в {относительный_путь: bytes} (без .id.json и скрытых).

    Табличные документы (.mxl) декодируются в канонический текст (структурный diff),
    остальные файлы сравниваются по байтам.
    """
    result = {}
    if not os.path.isdir(d):
        return result
    for dirpath, dirnames, filenames in os.walk(d):
        dirnames[:] = [x for x in dirnames if not x.startswith(".")]
        for fn in filenames:
            if fn.startswith(".") or fn.endswith(".id.json"):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, d)
            try:
                with open(full, "rb") as f:
                    data = f.read()
            except OSError:
                continue
            if fn.lower().endswith(".mxl"):
                try:
                    data = mxl.to_text(mxl.decode(data)).encode("utf-8")
                except Exception:
                    pass
            result[rel] = data
    return result


def _unified_diff(a_lines, b_lines, label):
    diff = difflib.unified_diff(
        a_lines, b_lines, fromfile=f"{label} (было)", tofile=f"{label} (стало)", lineterm=""
    )
    return "\n".join(diff)


def _is_binary(data: bytes) -> bool:
    """Эвристика: бинарный файл содержит NUL-байт (текст UTF-8 — нет)."""
    return b"\x00" in data


def _diff_from_collected(fa, fb, name):
    """Дифф по уже собранным картам файлов объекта. Возвращает (changes, truncated)."""
    all_files = sorted(set(fa) | set(fb))
    truncated = len(all_files) > MAX_DIFF_FILES
    changes = []
    for rel in all_files[:MAX_DIFF_FILES]:
        a = fa.get(rel)
        b = fb.get(rel)
        if a == b:
            continue
        entry = {"file": rel}
        if a is None:
            entry["status"] = "added"
            entry["size"] = len(b)
        elif b is None:
            entry["status"] = "removed"
            entry["size"] = len(a)
        else:
            entry["status"] = "changed"
            entry["old_bytes"] = len(a)
            entry["new_bytes"] = len(b)
            if _is_binary(a) or _is_binary(b):
                entry["binary"] = True
            else:
                entry["diff"] = _unified_diff(
                    a.decode("utf-8", "replace").splitlines(),
                    b.decode("utf-8", "replace").splitlines(),
                    rel,
                )
                if len(entry["diff"].splitlines()) > MAX_DIFF_LINES:
                    entry["diff"] = "\n".join(entry["diff"].splitlines()[:MAX_DIFF_LINES])
                    entry["diff_truncated"] = True
        changes.append(entry)
    return changes, truncated


def _enumerate_object_dirs(root):
    """Каталоги объектов организованного дерева: {object_path: abs_dir}.

    Для .cf/.cfe — «Тип/Имя» (глубина 2); для .epf/.erf — корень сам объект (путь "").
    """
    result = {}
    if not os.path.isdir(root):
        return result
    for marker in ("ExternalDataProcessor.json", "ExternalReport.json"):
        if os.path.isfile(os.path.join(root, marker)):
            result[""] = root
            return result
    for type_name in sorted(os.listdir(root)):
        if type_name.startswith("."):
            continue
        type_dir = os.path.join(root, type_name)
        if not os.path.isdir(type_dir):
            continue
        for name in sorted(os.listdir(type_dir)):
            obj_dir = os.path.join(type_dir, name)
            if os.path.isdir(obj_dir):
                result[f"{type_name}/{name}"] = obj_dir
    return result


def diff(dir_a: str, dir_b: str, full: bool = True) -> dict:
    """Сравнить два распакованных каталога (результат unpack) пообъектно.

    full=true (по умолчанию) — для изменённых объектов строить unified diff;
    full=false — только факт изменения (быстрее).
    """
    dir_a = os.path.abspath(dir_a)
    dir_b = os.path.abspath(dir_b)
    for d in (dir_a, dir_b):
        if not os.path.isdir(d):
            raise FileNotFoundError(d)

    objs_a = _enumerate_object_dirs(dir_a)
    objs_b = _enumerate_object_dirs(dir_b)

    report = {
        "dir_a": dir_a,
        "dir_b": dir_b,
        "kind_a": core._meta_kind_dir(dir_a),
        "kind_b": core._meta_kind_dir(dir_b),
        "identical": False,
        "summary": {"changed": 0, "added": 0, "removed": 0, "unchanged": 0},
        "objects": [],
    }

    for path in sorted(set(objs_a) | set(objs_b)):
        da = objs_a.get(path)
        db = objs_b.get(path)
        fa = _collect_files(da) if da else {}
        fb = _collect_files(db) if db else {}

        if da is None:
            status = "added"
        elif db is None:
            status = "removed"
        elif fa == fb:
            status = "unchanged"
        else:
            status = "changed"

        entry = {"name": path or "<корень>", "object_path": path, "status": status, "diff": None}
        report["summary"][status] += 1

        if status == "changed" and full:
            changes, truncated = _diff_from_collected(fa, fb, path or "<корень>")
            entry["diff"] = {"files": changes, "truncated": truncated}
        elif status == "changed":
            entry["note"] = "full=false: только факт изменения"
        elif status == "added":
            entry["note"] = "объект целиком добавлен"
        elif status == "removed":
            entry["note"] = "объект целиком удалён"

        report["objects"].append(entry)

    report["identical"] = (
        report["summary"]["changed"] + report["summary"]["added"] + report["summary"]["removed"] == 0
    )
    return report
