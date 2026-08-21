"""End-to-end тест stdio-сервера через MCP client SDK.

Запуск: python tests/test_server_e2e.py
"""

import asyncio
import json
import os
import sys

from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

TESTDATA = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "testdata"))
CFE = os.path.join(TESTDATA, "TNSoft.ru ПомощникПродаж_4_3_4.cfe")
ERF = os.path.join(TESTDATA, "ОстаткиТМЦВЭксплуатацииУТ.erf")


async def main():
    params = StdioServerParameters(command=sys.executable, args=["-m", "v8unpack_mcp"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print("tools:", names)
            assert {"unpack", "repack", "list_objects", "get_metadata",
                    "read_module", "read_bytecode", "search_code", "set_help",
                    "read_template", "get_help", "import_template", "get_module_structure",
                    "read_dcs", "export_dcs", "import_dcs",
                    "diff", "cleanup"} <= set(names)

            result = await session.call_tool("unpack", {"file_path": CFE})
            data = json.loads(result.content[0].text)
            assert data["status"] == "ok"
            d = data["dir"]
            print("unpack OK:", d)

            result = await session.call_tool("list_objects", {"dir_path": d})
            data = json.loads(result.content[0].text)
            assert "CommonModule" in data
            print("list_objects OK:", list(data)[:5])

            result = await session.call_tool(
                "search_code",
                {"dir_path": d, "pattern": "dataCompositionSchema", "max_results": 5, "scope": "all"},
            )
            data = json.loads(result.content[0].text)
            assert len(data["matches"]) > 0
            print("search_code (scope=all) OK:", len(data["matches"]), "совпадений (СКД XML)")

            result = await session.call_tool("cleanup", {"dir_path": d})
            data = json.loads(result.content[0].text)
            assert data["count"] == 1
            print("cleanup OK")

            # СКД-инструменты на внешнем отчёте.
            result = await session.call_tool("unpack", {"file_path": ERF})
            d2 = json.loads(result.content[0].text)["dir"]

            result = await session.call_tool("read_dcs", {"dir_path": d2})
            data = json.loads(result.content[0].text)
            assert len(data["overview"]["data_sets"]) == 1
            print("read_dcs OK:", data["overview"]["data_sets"][0]["name"])

            result = await session.call_tool(
                "read_dcs", {"dir_path": d2, "data_set": "НаборДанных"}
            )
            data = json.loads(result.content[0].text)
            assert "ВЫБРАТЬ" in data["data_set"]["query"]
            print("read_dcs (data_set) OK: query найден")

            result = await session.call_tool("export_dcs", {"dir_path": d2})
            data = json.loads(result.content[0].text)
            assert "<DataCompositionSchema" in data["xml"]
            print("export_dcs OK: XML выгружен")

            result = await session.call_tool("cleanup", {"dir_path": d2})
            json.loads(result.content[0].text)
            print("cleanup OK")


if __name__ == "__main__":
    asyncio.run(main())
    print("E2E OK")
