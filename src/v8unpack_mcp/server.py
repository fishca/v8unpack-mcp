"""MCP-сервер (stdio) для работы с бинарными файлами 1С."""

import json

from mcp.server.mcpserver import MCPServer

from . import __version__
from . import core
from . import diffing

mcp = MCPServer(
    name="v8unpack-mcp",
    title="v8unpack MCP",
    description="Полный цикл работы с бинарными файлами 1С (.cf/.cfe/.epf/.erf) без EDT: "
                "unpack → чтение/правка → repack → cleanup",
    version=__version__,
)


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


@mcp.tool(description="Полная распаковка файла 1С (.cf/.cfe/.epf/.erf) в отдельный временный "
                      "каталог (без лимита размера, любые объекты). Первый шаг любого цикла. "
                      "Возвращает путь к каталогу (организованное дерево + raw-слой .v8unpack_raw)")
def unpack(file_path: str) -> str:
    return _dump(core.unpack(file_path))


@mcp.tool(description="Собрать файл 1С из распакованного каталога (результат unpack) в целевой файл")
def repack(dir_path: str, output_path: str) -> str:
    return _dump(core.repack(dir_path, output_path))


@mcp.tool(description="Список объектов внутри распакованного контейнера: {вид_объекта: [имена]} "
                      "(только имена; полные метаданные — get_metadata)")
def list_objects(dir_path: str) -> str:
    return _dump(core.list_objects(dir_path))


@mcp.tool(description="Метаданные распакованного каталога. Без object_path — сводка "
                      "(вид, счётчики по типам). С object_path='Тип/Имя' — метаданные объекта "
                      "(имя, синоним, uuid, формы, макеты, модули). detail=true — полный список по типам")
def get_metadata(dir_path: str, object_path: str = "", detail: bool = False) -> str:
    return _dump(core.get_metadata(dir_path, object_path, detail))


@mcp.tool(description="Прочитать BSL-модуль объекта из распакованного каталога. "
                      "object_path — путь к объекту ('' для корня epf/erf, 'CommonModule/Имя' для cf/cfe); "
                      "module_name — подстрока имени файла модуля (пусто = список модулей объекта)")
def read_module(dir_path: str, object_path: str = "", module_name: str = "") -> str:
    return _dump(core.read_module(dir_path, object_path, module_name or None))


@mcp.tool(description="Разобрать байт-код закрытого модуля (методы, константы, поток опкодов) "
                      "из raw-слоя каталога unpack")
def read_bytecode(dir_path: str, object_path: str = "") -> str:
    return _dump(core.read_bytecode(dir_path, object_path))


@mcp.tool(description="Поиск подстроки/regex по текстовым слоям распакованного каталога. "
                      "layers: modules, forms, templates_text, templates_html, dcc (пусто = все). "
                      "Каждое совпадение содержит поле layer")
def search_code(dir_path: str, pattern: str, regex: bool = False,
                case_sensitive: bool = False, max_results: int = 200, layers: list = None) -> str:
    return _dump(core.search_code(dir_path, pattern, regex, case_sensitive, max_results, layers))


@mcp.tool(description="Записать справочную информацию (help) объекта в raw-слой каталога unpack "
                      "(сборку делает repack). object_path пуст для внешней обработки/отчёта, "
                      "'Тип/Имя' для конфигурации/расширения. overwrite=false — существующую справку не перезаписывать")
def set_help(dir_path: str, object_path: str = "", help_html: str = "",
             overwrite: bool = False) -> str:
    return _dump(core.set_help(dir_path, object_path, help_html, overwrite))


@mcp.tool(description="Импортировать готовый .mxl (табличный документ) в макет объекта — "
                      "замена содержимого существующего макета (template_name или единственный). "
                      "Пишет в raw-слой; сборку делает repack")
def import_template(dir_path: str, object_path: str = "", template_path: str = "",
                    template_name: str = "") -> str:
    return _dump(core.import_template(dir_path, object_path, template_path, template_name or None))


@mcp.tool(description="Прочитать табличный документ (MXL) макета в структурированном виде. "
                      "Декодирует MOXCEL в дерево: возвращает канонический текст structure и "
                      "список текстовых значений strings. Только чтение")
def read_template(dir_path: str, object_path: str = "", template_name: str = "") -> str:
    return _dump(core.read_template(dir_path, object_path, template_name or None))


@mcp.tool(description="Сравнить два распакованных каталога (результат unpack) пообъектно. "
                      "По содержимому (построчный diff): .mxl (декодируется структурно), .json, "
                      ".obj.bsl, XML форм. По байтам (только факт изменения, помечается binary=true): "
                      "зашифрованные .obj.bin, СКД .bin, картинки. full=false — только факт изменения без диффа")
def diff(dir_a: str, dir_b: str, full: bool = True) -> str:
    return _dump(diffing.diff(dir_a, dir_b, full))


@mcp.tool(description="Удалить временный каталог unpack. dir_path — один каталог; "
                      "all=true — удалить все каталоги unpack (по префиксу в temp)")
def cleanup(dir_path: str = None, all: bool = False) -> str:
    return _dump(core.cleanup(dir_path, all))


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
