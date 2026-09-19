"""Закрытые модули: поиск и декомпиляция (цикл unpack → find → decompile → repack).

Проверяются: find_closed_modules (TNSoft — закрытый модуль с шифрованным text),
полный цикл на hello.epf (закрытый корневой модуль, только байт-код; эталонная
длина декомпилята — 563 строки), фильтр objects.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from v8unpack_mcp import core

TESTDATA = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "testdata"))
EPF = os.path.join(TESTDATA, "hello.epf")
CFE = os.path.join(TESTDATA, "TNSoft.ru ПомощникПродаж_4_3_4.cfe")


def test_find_closed_modules_tnsoft():
    d = core.unpack(CFE)["dir"]
    try:
        r = core.find_closed_modules(d)
        assert r["total"] == 1
        m = r["modules"][0]
        assert m["object_path"] == "CommonModule/ТН_НоменклатураСервер"
        assert m["module"] == "obj" and m["encoding"] == "bin"
        assert m["has_image"] and m["decompilable"]
        assert m["info"][0][1] == "3"  # вариант защиты {3,3,1,"ключ",1000}
    finally:
        core.cleanup(d)


def test_decompile_full_cycle_hello():
    d = core.unpack(EPF)["dir"]
    tmp = tempfile.mkdtemp()
    try:
        r = core.decompile_modules(d)
        assert r["opened"] == 1 and r["failed"] == 0
        assert r["modules"][0]["lines"] >= 560  # эталон hello: 563 строки

        out = os.path.join(tmp, "hello_open.epf")
        core.repack(d, out)
        chk = core.unpack(out)["dir"]
        try:
            assert core.find_closed_modules(chk, check_obfuscation=False)["total"] == 0
            bsl = os.path.join(chk, "ExternalDataProcessor.obj.bsl")
            text = open(bsl, encoding="utf-8-sig").read()
            assert len(text.splitlines()) >= 560
            assert "Процедура" in text or "Функция" in text
        finally:
            core.cleanup(chk)
    finally:
        core.cleanup(d)


def test_decompile_objects_filter_skips_unlisted():
    d = core.unpack(CFE)["dir"]
    try:
        r = core.decompile_modules(d, objects="CommonModule/НетТакого")
        assert r["opened"] == 0 and r["skipped"] == 0
        # ничего не тронуто: закрытый модуль на месте, header без правок
        assert os.path.isfile(os.path.join(d, "CommonModule", "ТН_НоменклатураСервер",
                                           "CommonModule.obj.bin"))
        h = json.load(open(os.path.join(d, "CommonModule", "ТН_НоменклатураСервер",
                                        "CommonModule.json"), encoding="utf-8"))
        assert h["code_encoding_obj"] == "bin"

        r2 = core.decompile_modules(d, objects="CommonModule/ТН_НоменклатураСервер")
        assert r2["opened"] == 1
    finally:
        core.cleanup(d)


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"{name}: OK")
