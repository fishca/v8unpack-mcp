"""Гранулярная organized-сборка: пообъектный индекс и перенос нетронутых объектов из raw-слоя.

Сценарии (см. v8unpack.object_index, Decoder.encode_include):
- unpack с keep_raw строит .v8unpack_raw/objects.json (папки объектов + корень);
- repack без правок собирает по быстрому пути (raw целиком);
- правка одного объекта: нетронутые объекты переносятся из raw (в т.ч. зашифрованный
  модуль TNSoft побайтово), изменённый кодируется из organized; результат
  контентно равен полной organized-сборке того же дерева.

Запуск: python tests/test_granular.py (или pytest tests/test_granular.py)
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from v8unpack import object_index
from v8unpack.v8unpack import build as v8_build
from v8unpack_mcp import core

TESTDATA = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "testdata"))
EPF = os.path.join(TESTDATA, "ФормированиеЗаказовРегламентное_300762_(27).epf")
CFE = os.path.join(TESTDATA, "TNSoft.ru ПомощникПродаж_4_3_4.cfe")


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _tree_hashes(root, skip_raw=True):
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        if skip_raw:
            dirnames[:] = [d for d in sorted(dirnames) if d != ".v8unpack_raw"]
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            out[os.path.relpath(full, root).replace("\\", "/")] = _sha(full)
    return out


def _full_organized_build(src_dir, out_file):
    # Полная organized-сборка (без use_raw) — эталон для сравнения с гранулярной.
    v8_build(src_dir, out_file, options={})


def test_unpack_builds_object_index():
    d = core.unpack(CFE)["dir"]
    try:
        idx_path = os.path.join(d, ".v8unpack_raw", object_index.INDEX_FILE)
        assert os.path.isfile(idx_path)
        idx = json.load(open(idx_path, encoding="utf-8"))
        folders = idx["folders"]
        assert "CommonModule/ТН_НоменклатураСервер" in folders
        meta = folders["CommonModule/ТН_НоменклатураСервер"]
        assert meta["fp"] and meta["files"]
        assert any(f.endswith(".0") for f in meta["files"])  # код-контейнер модуля
        assert "root" in idx and idx["root"]["fp"]
    finally:
        core.cleanup(d)


def test_analyze_all_unchanged_and_after_edit():
    d = core.unpack(CFE)["dir"]
    try:
        raw_dir = os.path.join(d, ".v8unpack_raw")
        res = object_index.analyze(d, raw_dir)
        assert res is not None
        skip_map, all_unchanged = res
        assert all_unchanged and len(skip_map) == len(json.load(
            open(os.path.join(raw_dir, object_index.INDEX_FILE), encoding="utf-8"))["folders"])

        target = os.path.join(d, "CommonModule", "ПодборТоваровКлиент", "CommonModule.obj.bsl")
        with open(target, "a", encoding="utf-8", newline="") as f:
            f.write("// gran-test\r\n")
        skip_map, all_unchanged = object_index.analyze(d, raw_dir)
        assert not all_unchanged
        assert "CommonModule/ПодборТоваровКлиент" not in skip_map
        assert "CommonModule/ТН_НоменклатураСервер" in skip_map
    finally:
        core.cleanup(d)


def test_noedit_repack_fast_path_roundtrip():
    d = core.unpack(EPF)["dir"]
    try:
        out = tempfile.mktemp(suffix=".epf")
        core.repack(d, out)
        try:
            chk = core.unpack(out)["dir"]
            try:
                assert _tree_hashes(chk) == _tree_hashes(d)
            finally:
                core.cleanup(chk)
        finally:
            os.unlink(out)
    finally:
        core.cleanup(d)


def test_edit_one_object_merged_equals_full_organized():
    d = core.unpack(CFE)["dir"]
    tmp = tempfile.mkdtemp()
    try:
        target = os.path.join(d, "CommonModule", "ПодборТоваровКлиент", "CommonModule.obj.bsl")
        with open(target, "a", encoding="utf-8", newline="") as f:
            f.write("// gran-test\r\n")

        merged = os.path.join(tmp, "m.cfe")
        full = os.path.join(tmp, "f.cfe")
        core.repack(d, merged)  # use_raw=True -> гранулярный путь
        _full_organized_build(d, full)

        d_m, d_f = core.unpack(merged)["dir"], core.unpack(full)["dir"]
        try:
            hm, hf = _tree_hashes(d_m), _tree_hashes(d_f)
            # единственное допустимое расхождение — служебный sha1 в configinfo
            # (в заголовках блоков контейнера временная метка, байтовая
            # детерминированность двух сборок невозможна)
            diff = {k for k in set(hm) & set(hf) if hm[k] != hf[k]} | set(hm) ^ set(hf)
            assert diff <= {"ConfigurationExtension.json"}, sorted(diff)[:5]
            assert "// gran-test" in open(os.path.join(
                d_m, "CommonModule", "ПодборТоваровКлиент", "CommonModule.obj.bsl"),
                encoding="utf-8-sig").read()
            # зашифрованный модуль перенесён из raw без изменений
            enc = os.path.join(d_m, "CommonModule", "ТН_НоменклатураСервер", "CommonModule.obj.bin")
            assert _sha(enc) == _sha(os.path.join(
                d, "CommonModule", "ТН_НоменклатураСервер", "CommonModule.obj.bin"))
        finally:
            core.cleanup(d_m)
            core.cleanup(d_f)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        core.cleanup(d)


def test_raw_files_not_leaked_into_organized_pack():
    # .v8unpack_raw не должен попадать в stage3 организованной сборки
    d = core.unpack(EPF)["dir"]
    tmp = tempfile.mkdtemp()
    try:
        target = os.path.join(d, "ExternalDataProcessor.obj.bsl")
        with open(target, "a", encoding="utf-8", newline="") as f:
            f.write("// gran\r\n")
        out = os.path.join(tmp, "o.epf")
        core.repack(d, out)
        chk = core.unpack(out)["dir"]
        try:
            names = core.list_objects(chk)
            assert ".v8unpack_raw" not in names
        finally:
            core.cleanup(chk)
            shutil.rmtree(tmp, ignore_errors=True)
    finally:
        core.cleanup(d)


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"{name}: OK")
