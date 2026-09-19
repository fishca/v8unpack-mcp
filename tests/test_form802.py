"""Детект управляемых форм 802: get_form_element_decoder не должен разбирать их элементы.

Сигнатура: form[0][0] = [mark, [inner_version, ...]], у УФ-802 inner_version 25-28
(8.2), у УФ-803+ — 35-59. FormElements4 понимает только раскладку 803+, поэтому
для 802 декодер не выдаётся вовсе: элементы остаются сырым блоком form в JSON
(сборка/разбор идут без FormElements-этапа).

Запуск: python tests/test_form802.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from v8unpack.MetaDataObject.CatalogForm import CatalogForm  # noqa: E402
from v8unpack.MetaDataObject.Form.FormCore import OF  # noqa: E402
from v8unpack.MetaDataObject.Form.FormElements27.FormElements27 import FormElements27  # noqa: E402
from v8unpack.MetaDataObject.Form.FormElements4.FormElements4 import FormElements4  # noqa: E402
from v8unpack.MetaDataObject.Form.Form9 import Form9  # noqa: E402


def make_form(form_kind, inner_version):
    form = Form9(meta_obj_class=CatalogForm, obj_version="9", options={})
    form.header = {
        "Тип формы": form_kind,
        "form": [[[ "2", [inner_version, "0"] ]]],
    }
    # decode/encode работают через FormCore.form — свойство над header['form']
    return form


def test_managed_802_not_decoded():
    for inner in ("25", "26", "27", "28"):
        form = make_form("1", inner)
        decoder = form.get_form_element_decoder()
        assert decoder is None, f"inner={inner}: декодер должен отсутствовать"
        assert "не разбираются" in form.header["Версия элементов формы"]


def test_managed_803_decoded():
    for inner in ("35", "37", "42", "48", "49", "50", "55", "59"):
        form = make_form("1", inner)
        assert form.get_form_element_decoder() is FormElements4, f"inner={inner}"


def test_ordinary_802_untouched():
    # обычные формы 802 (Тип 0, mark 27) по-прежнему разбираются FormElements27
    form = make_form("0", "16")
    form.header["form"] = [[["27", ["16", "0"]]]]
    assert form.get_form_element_decoder() is FormElements27


def test_empty_form():
    form = Form9(meta_obj_class=CatalogForm, obj_version="9", options={})
    form.header = {"Тип формы": "1", "form": [[]]}
    assert form.get_form_element_decoder() is None
    assert form.header["Версия элементов формы"] == ""


def run():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"OK {name}")
    print("Все тесты пройдены")


if __name__ == "__main__":
    run()
