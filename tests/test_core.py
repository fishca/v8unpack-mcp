"""Смоук-тесты ядра v8unpack_mcp на файлах из ../testdata.

Запуск: python tests/test_core.py (или pytest tests/)
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from v8unpack_mcp import core
from v8unpack_mcp import diffing

TESTDATA = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "testdata"))
EPF = os.path.join(TESTDATA, "ФормированиеЗаказовРегламентное_300762_(27).epf")
CFE = os.path.join(TESTDATA, "TNSoft.ru ПомощникПродаж_4_3_4.cfe")


def _unpack(path):
    return core.unpack(path)["dir"]


def test_list_objects_excludes_internal():
    d = _unpack(EPF)
    try:
        objs = core.list_objects(d)
        assert ".v8unpack_raw" not in objs
        assert "ExternalDataProcessor.obj.bsl" in objs["@files"]
    finally:
        core.cleanup(d)


def test_unpack_dir_contains_object_module():
    d = _unpack(EPF)
    try:
        assert os.path.isdir(d)
        assert "ExternalDataProcessor.obj.bsl" in os.listdir(d)
        assert ".v8unpack_raw" in os.listdir(d)
    finally:
        core.cleanup(d)


def test_read_module_encrypted():
    d = _unpack(CFE)
    try:
        r = core.read_module(d, "CommonModule/ТН_НоменклатураСервер")
        kinds = {m["kind"] for m in r["modules"]}
        assert "encrypted" in kinds
    finally:
        core.cleanup(d)


def test_read_module_plain():
    d = _unpack(CFE)
    try:
        r = core.read_module(d, "CommonModule/ПодборТоваровСервер", "CommonModule")
        assert r["kind"] == "bsl"
        assert "Функция" in r["content"] or "Процедура" in r["content"]
    finally:
        core.cleanup(d)


def test_search_code_bsl():
    d = _unpack(CFE)
    try:
        res = core.search_code(d, "Функция", max_results=10)
        assert len(res["matches"]) > 0
        assert all("line" in m and "text" in m for m in res["matches"])
    finally:
        core.cleanup(d)


def test_search_code_skd_xml():
    # СКД лежит в Template.bin (бинарный префикс + XML) — текст запроса должен искаться.
    d = _unpack(CFE)
    try:
        res = core.search_code(d, "dataCompositionSchema", max_results=10)
        assert len(res["matches"]) > 0
        assert any("Template.bin" in m["path"] for m in res["matches"])
    finally:
        core.cleanup(d)


def test_read_bytecode():
    d = _unpack(CFE)
    try:
        r = core.read_bytecode(d, "CommonModule/ТН_НоменклатураСервер")
        assert r["opcodes"] > 0
        assert r["constants"] > 0
        assert len(r["methods"]) > 0
        assert any(not m["anonymous"] for m in r["methods"])
    finally:
        core.cleanup(d)


def test_unpack_returns_dir_with_raw():
    r = core.unpack(EPF)
    try:
        assert r["status"] == "ok"
        assert os.path.isdir(r["dir"])
        assert r["kind"] == "external_data_processor"
        assert "ExternalDataProcessor.obj.bsl" in os.listdir(r["dir"])
        assert os.path.isdir(os.path.join(r["dir"], ".v8unpack_raw"))
    finally:
        shutil.rmtree(r["dir"], ignore_errors=True)


def test_unpack_repack_roundtrip():
    r = core.unpack(EPF)
    out = os.path.join(tempfile.gettempdir(), "v8unpack_mcp_roundtrip.epf")
    try:
        r2 = core.repack(r["dir"], out)
        assert r2["status"] == "ok"
        assert r2["output"] == out
        assert os.path.isfile(out) and os.path.getsize(out) > 0
        d2 = _unpack(out)
        try:
            assert "ExternalDataProcessor.obj.bsl" in os.listdir(d2)
        finally:
            core.cleanup(d2)
    finally:
        shutil.rmtree(r["dir"], ignore_errors=True)
        if os.path.isfile(out):
            os.remove(out)


def test_set_help_edit_and_repack():
    d = _unpack(EPF)
    out = os.path.join(tempfile.gettempdir(), "v8unpack_mcp_help.epf")
    try:
        res = core.set_help(d, "", "<html><body>тест</body></html>", overwrite=True)
        assert res["ok"] is True
        assert res["meta_type"] == "ExternalDataProcessor"
        r2 = core.repack(d, out)
        assert r2["status"] == "ok"
        assert os.path.isfile(out) and os.path.getsize(out) > 0
    finally:
        shutil.rmtree(d, ignore_errors=True)
        if os.path.isfile(out):
            os.remove(out)


def _cfe_table_mxls(d):
    """Табличные макеты DataProcessor/ТН_ЗагрузкаТоваровИзВнешнихФайлов: {имя: путь .mxl}."""
    obj = os.path.join(d, "DataProcessor", "ТН_ЗагрузкаТоваровИзВнешнихФайлов")
    tdir = os.path.join(obj, "Template")
    result = {}
    for name in sorted(os.listdir(tdir)):
        mxl = os.path.join(tdir, name, "Template.mxl")
        if os.path.isfile(mxl):
            result[name] = mxl
    return result


def test_import_template_replace_roundtrip():
    d = _unpack(CFE)
    out = os.path.join(tempfile.gettempdir(), "v8unpack_mcp_import_replace.cfe")
    src = os.path.join(tempfile.gettempdir(), "v8unpack_mcp_src.mxl")
    try:
        mxls = _cfe_table_mxls(d)
        assert "Макет" in mxls
        with open(mxls["Макет"], "rb") as f:
            orig = f.read()
        modified = orig + b"\x00\x01\x02"
        with open(src, "wb") as f:
            f.write(modified)
        res = core.import_template(d, "DataProcessor/ТН_ЗагрузкаТоваровИзВнешнихФайлов",
                                   src, "Макет")
        assert res["ok"] is True
        assert res["template_name"] == "Макет"
        assert res["uuid"]
        # организованный Template.mxl намеренно не менялся (raw-слой)
        with open(mxls["Макет"], "rb") as f:
            assert f.read() == orig
        r2 = core.repack(d, out)
        assert r2["status"] == "ok"
        d2 = _unpack(out)
        try:
            with open(_cfe_table_mxls(d2)["Макет"], "rb") as f:
                assert f.read() == modified
        finally:
            core.cleanup(d2)
    finally:
        core.cleanup(d)
        if os.path.isfile(src):
            os.remove(src)
        if os.path.isfile(out):
            os.remove(out)


def test_import_template_rejects_non_moxcel():
    d = _unpack(CFE)
    src = os.path.join(tempfile.gettempdir(), "v8unpack_mcp_not_moxcel.mxl")
    try:
        with open(src, "wb") as f:
            f.write(b"not a moxcel file")
        try:
            core.import_template(d, "DataProcessor/ТН_ЗагрузкаТоваровИзВнешнихФайлов",
                                 src, "Макет")
        except ValueError:
            return
        raise AssertionError("ожидался ValueError на не-MOXCEL файле")
    finally:
        core.cleanup(d)
        if os.path.isfile(src):
            os.remove(src)


def test_read_template():
    d = _unpack(CFE)
    try:
        r = core.read_template(d, "DataProcessor/ТН_ЗагрузкаТоваровИзВнешнихФайлов", "Макет")
        assert r["template_name"] == "Макет"
        assert r["bytes"] > 0
        assert r["top_level_elements"] > 0
        assert "Штрихкод" in r["strings"] or "Артикул" in r["strings"]
        assert r["structure"].startswith("[")
    finally:
        core.cleanup(d)


def test_diff_mxl_structural():
    a = _unpack(CFE)
    b = _unpack(CFE)
    try:
        mxl_b = _cfe_table_mxls(b)["Макет"]
        with open(mxl_b, "rb") as f:
            data = f.read()
        assert b'"N",1000' in data
        with open(mxl_b, "wb") as f:
            f.write(data.replace(b'"N",1000', b'"N",2000'))
        rep = diffing.diff(a, b)
        found = None
        for obj in rep["objects"]:
            if obj.get("diff"):
                for fc in obj["diff"]["files"]:
                    if fc["file"].endswith("Template.mxl"):
                        found = fc
        assert found is not None, "ожидалось структурное изменение Template.mxl"
        assert found["status"] == "changed"
        lines = found["diff"].splitlines()
        assert any(l.startswith("-") and "1000" in l for l in lines)
        assert any(l.startswith("+") and "2000" in l for l in lines)
    finally:
        core.cleanup(a)
        core.cleanup(b)


def test_unpack_missing_file():
    try:
        core.unpack(os.path.join(TESTDATA, "нет_такого.epf"))
    except FileNotFoundError:
        return
    raise AssertionError("ожидался FileNotFoundError")


def test_repack_missing_dir():
    try:
        core.repack(os.path.join(TESTDATA, "нет_такого_каталога"), "out.epf")
    except FileNotFoundError:
        return
    raise AssertionError("ожидался FileNotFoundError")


def test_cleanup():
    r = core.unpack(EPF)
    d = r["dir"]
    assert os.path.isdir(d)
    res = core.cleanup(d)
    assert res["count"] == 1
    assert not os.path.isdir(d)


def test_cleanup_rejects_non_unpack_dir():
    try:
        core.cleanup(TESTDATA)
    except ValueError:
        return
    raise AssertionError("ожидался ValueError")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"OK {name}")
    print("Все тесты пройдены")
