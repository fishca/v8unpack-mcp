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


def test_get_module_structure():
    d = _unpack(CFE)
    try:
        s = core.get_module_structure(d, "CommonModule/ПодборТоваровСервер", "CommonModule")
        assert s["kind"] == "bsl"
        assert s["total_lines"] > 0
        assert len(s["procedures"]) >= 1
        for p in s["procedures"]:
            assert p["name"] and p["kind"] in ("Процедура", "Функция")
            assert p["start_line"] >= 1 and p["end_line"] >= p["start_line"]
            assert isinstance(p["params"], list) and "docstring" in p
        assert any(p["kind"] == "Функция" for p in s["procedures"])
        assert any(p["params"] for p in s["procedures"])
    finally:
        core.cleanup(d)


def test_read_module_ranges():
    d = _unpack(CFE)
    try:
        r = core.read_module(d, "CommonModule/ПодборТоваровСервер", "CommonModule",
                             ranges=["1-5", "10-12"])
        assert "ranges" in r and "content" not in r
        assert len(r["ranges"]) == 2
        assert r["total_returned_lines"] == 8
        seg = r["ranges"][0]
        assert seg["start_line"] == 1 and seg["end_line"] == 5 and len(seg["lines"]) == 5
    finally:
        core.cleanup(d)


def test_read_module_ranges_limit():
    lines = ["x"] * 500
    try:
        core._apply_ranges(lines, ["1-300", "301-500"])
    except ValueError:
        return
    raise AssertionError("ожидался ValueError при превышении лимита 400 строк")


def test_read_module_ranges_out_of_bounds():
    d = _unpack(CFE)
    try:
        try:
            core.read_module(d, "CommonModule/ПодборТоваровСервер", "CommonModule",
                             ranges=["1-9999"])
        except ValueError:
            return
        raise AssertionError("ожидался ValueError при диапазоне вне границ модуля")
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
    # СКД лежит в Template.bin (бинарный префикс + XML). scope='all' ищет по всему XML.
    d = _unpack(CFE)
    try:
        res = core.search_code(d, "dataCompositionSchema", max_results=10, scope="all")
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


def _wait_done(job_id, timeout=120):
    """Опрашивать job_status до status=done/error или таймаута."""
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = core.job_status(job_id)
        if st["status"] in ("done", "error"):
            return st
        time.sleep(0.05)
    raise TimeoutError(f"Задание {job_id} не завершилось за {timeout}с")


def test_unpack_async_returns_job_and_done():
    s = core.start_unpack(EPF)
    assert s["status"] == "running"
    assert s["job_id"]
    st = _wait_done(s["job_id"])
    assert st["status"] == "done"
    assert st["error"] is None
    assert st["result"]["status"] == "ok"
    assert os.path.isdir(st["result"]["dir"])
    try:
        assert "ExternalDataProcessor.obj.bsl" in os.listdir(st["result"]["dir"])
    finally:
        core.cleanup(st["result"]["dir"])


def test_unpack_async_missing_file_raises():
    try:
        core.start_unpack(os.path.join(TESTDATA, "нет_такого.epf"))
        assert False, "должно быть FileNotFoundError"
    except FileNotFoundError:
        pass


def test_unpack_async_bad_extension():
    bad = os.path.join(tempfile.gettempdir(), "v8unpack_mcp_not_1c.txt")
    with open(bad, "w", encoding="utf-8") as f:
        f.write("x")
    try:
        try:
            core.start_unpack(bad)
            assert False, "должно быть ValueError"
        except ValueError:
            pass
    finally:
        if os.path.isfile(bad):
            os.remove(bad)


def test_repack_async_roundtrip():
    d = _unpack(EPF)
    out = os.path.join(tempfile.gettempdir(), "v8unpack_mcp_async_roundtrip.epf")
    try:
        s = core.start_repack(d, out)
        assert s["status"] == "running"
        st = _wait_done(s["job_id"])
        assert st["status"] == "done"
        assert st["error"] is None
        assert st["result"]["status"] == "ok"
        assert os.path.isfile(out) and os.path.getsize(out) > 0
    finally:
        shutil.rmtree(d, ignore_errors=True)
        if os.path.isfile(out):
            os.remove(out)


def test_job_status_unknown_job():
    try:
        core.job_status("job-999999")
        assert False, "должно быть KeyError"
    except KeyError:
        pass


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


def test_get_help_check_and_get():
    d = _unpack(EPF)
    try:
        chk = core.get_help(d, "", "check")
        assert chk["exists"] is True
        assert chk["meta_type"] == "ExternalDataProcessor"
        assert chk["uuid"] and chk["bytes"] > 0
        got = core.get_help(d, "", "get")
        assert got["exists"] is True
        assert got["languages"] == ["ru"]
        assert got["text"] and "<html>" in got["text"]
    finally:
        core.cleanup(d)


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
