"""Тесты чтения схем компоновки данных (СКД) — read_dcs.

Запуск: python tests/test_dcs.py (или pytest tests/)
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from v8unpack_mcp import core
from v8unpack_mcp import dcs

TESTDATA = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "testdata"))
ERF = os.path.join(TESTDATA, "ОстаткиТМЦВЭксплуатацииУТ.erf")

DATASET = "НаборДанных"
VARIANT = "ОстаткиТМЦВЭксплуатации"


def _unpack():
    return core.unpack(ERF)["dir"]


def test_read_dcs_overview():
    d = _unpack()
    try:
        r = core.read_dcs(d)
        assert r["template_name"]
        assert r["bytes"] > 0
        ov = r["overview"]
        assert len(ov["data_sets"]) == 1
        ds = ov["data_sets"][0]
        assert ds["name"] == DATASET
        assert ds["type"] == "Query"
        assert ds["has_query"] is True
        names = [p["name"] for p in ov["parameters"]]
        assert "Период" in names
        assert len(ov["variants"]) == 1
        assert ov["variants"][0]["name"] == VARIANT
        assert r["variants_count"] == 1
    finally:
        core.cleanup(d)


def test_read_dcs_dataset():
    d = _unpack()
    try:
        r = core.read_dcs(d, data_set=DATASET)
        detail = r["data_set"]
        assert detail["name"] == DATASET
        assert "ВЫБРАТЬ" in detail["query"]
        assert "ПартииТМЦВЭксплуатации" in detail["query"]
        fields = [f["field"] for f in detail["fields"]]
        assert "Номенклатура" in fields
        assert "Количество" in fields
    finally:
        core.cleanup(d)


def test_read_dcs_variant():
    d = _unpack()
    try:
        r = core.read_dcs(d, variant=VARIANT)
        assert r["variant"] == VARIANT
        xml = r["variant_xml"]
        assert xml.startswith("<?xml")
        assert "<Settings" in xml
        assert "<selection>" in xml
        assert "<filter>" in xml
        assert "<dataParameters>" in xml
        assert "<order>" in xml
        assert "<outputParameters>" in xml
        assert "<item" in xml
    finally:
        core.cleanup(d)


def test_read_dcs_unknown_variant():
    d = _unpack()
    try:
        try:
            core.read_dcs(d, variant="НетТакогоВарианта")
        except ValueError:
            return
        raise AssertionError("ожидался ValueError при неизвестном варианте")
    finally:
        core.cleanup(d)


def test_dcs_split_documents():
    # Читаем реальный .bin через распаковку и проверяем низкоуровневый разбор.
    d = _unpack()
    try:
        bin_path = None
        for dirpath, _dirnames, filenames in os.walk(d):
            for fn in filenames:
                if fn == "Template.bin":
                    bin_path = os.path.join(dirpath, fn)
        assert bin_path is not None
        with open(bin_path, "rb") as f:
            data = f.read()
        docs = dcs.split_documents(data)
        assert len(docs) == 3
        parsed = dcs.load(data)
        assert parsed["tail"] is not None
        assert len(parsed["settings"]) == 1
    finally:
        core.cleanup(d)


def test_export_dcs_roundtrip():
    # .bin -> XML -> .bin: структура (наборы/варианты/настройки) не должна потеряться.
    d = _unpack()
    try:
        r = core.export_dcs(d)
        xml = r["xml"]
        assert xml.startswith("<?xml")
        assert "<DataCompositionSchema" in xml
        assert "dcsset" in xml
        assert "<settings>" in xml or "settings>" in xml
        assert "<query>" in xml

        bin2 = dcs.build_bin(xml)
        parsed = dcs.load(bin2)
        ov = dcs.overview(parsed["schema"])
        assert [ds["name"] for ds in ov["data_sets"]] == [DATASET]
        assert [v["name"] for v in ov["variants"]] == [VARIANT]
        assert len(parsed["settings"]) == 1
        assert "ВЫБРАТЬ" in dcs.dataset_detail(parsed["schema"], DATASET)["query"]
        assert "<selection>" in parsed["settings"][0]
    finally:
        core.cleanup(d)


def test_export_dcs_to_file():
    d = _unpack()
    out = os.path.join(tempfile.gettempdir(), "v8unpack_dcs_export.xml")
    try:
        r = core.export_dcs(d, output_path=out)
        assert r["output"] == out
        assert os.path.isfile(out) and os.path.getsize(out) > 0
        with open(out, encoding="utf-8") as f:
            assert "<DataCompositionSchema" in f.read()
    finally:
        core.cleanup(d)
        if os.path.isfile(out):
            os.remove(out)


def test_import_dcs_platform_xml_roundtrip():
    # Загрузка штатного XML платформы -> repack -> read_dcs даёт тот же запрос и поля.
    xml_path = os.path.join(TESTDATA, "ОстаткиТМЦВЭксплуатацииУТ.xml")
    d = _unpack()
    out = os.path.join(tempfile.gettempdir(), "v8unpack_dcs_import.erf")
    try:
        res = core.import_dcs(d, template_path=xml_path)
        assert res["ok"] is True
        assert res["bytes"] > 0
        r2 = core.repack(d, out)
        assert r2["status"] == "ok"
        d2 = core.unpack(out)["dir"]
        try:
            detail = core.read_dcs(d2, data_set=DATASET)["data_set"]
            assert "ПартииТМЦВЭксплуатации" in detail["query"]
            fields = [f["field"] for f in detail["fields"]]
            assert "Номенклатура" in fields and "Количество" in fields
        finally:
            core.cleanup(d2)
    finally:
        core.cleanup(d)
        if os.path.isfile(out):
            os.remove(out)


def test_search_code_dcs_query_scope():
    # scope='query' (по умолчанию) ищет только в тексте запроса СКД.
    d = _unpack()
    try:
        res = core.search_code(d, "ПартииТМЦВЭксплуатации", layers=["dcc"])
        assert len(res["matches"]) > 0
        assert all("Template.bin" in m["path"] for m in res["matches"])
        # Тег <query> и служебные имена XML в scope='query' не находятся.
        res2 = core.search_code(d, "dataCompositionSchema", layers=["dcc"], scope="query")
        assert len(res2["matches"]) == 0
        # ... но находятся при scope='all'.
        res3 = core.search_code(d, "dataCompositionSchema", layers=["dcc"], scope="all")
        assert len(res3["matches"]) > 0
    finally:
        core.cleanup(d)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"OK {name}")
    print("Все тесты пройдены")
