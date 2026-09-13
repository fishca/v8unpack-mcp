"""Тесты декомпилятора: детект обфусцированных модулей.

Запуск: python tests/test_decompiler.py (или pytest tests/)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from v8unpack_mcp import bytecode as bc
from v8unpack_mcp import decompiler

TESTDATA = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "testdata"))


def _load(path):
    return bc.parse_image(bc._parse_brace_file(path))


def test_obfuscated_image_refused():
    """Обфусцированный образ (общие регионы методов) — отказ с сообщением про ВМ."""
    image = _load(os.path.join(TESTDATA, "vm_image_samples", "decomp.image"))
    assert decompiler.is_obfuscated(image)
    try:
        decompiler.decompile_image(image)
    except ValueError as err:
        assert str(err).startswith("Модуль обфусцирован")
        assert "виртуальную машину" in str(err)
    else:
        raise AssertionError("ожидались ValueError для обфусцированного образа")


def test_plain_image_decompiles():
    """Штатный защищённый образ (0 общих адресов) — декомпилируется."""
    image = _load(os.path.join(TESTDATA, "vm_image_samples", "hello.image"))
    assert decompiler.shared_region_count(image) == 0
    text = decompiler.decompile_image(image)
    assert "Функция Арифметика" in text


if __name__ == "__main__":
    test_obfuscated_image_refused()
    test_plain_image_decompiles()
    print("OK")
