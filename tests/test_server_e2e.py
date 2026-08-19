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
                {"dir_path": d, "pattern": "dataCompositionSchema", "max_results": 5},
            )
            data = json.loads(result.content[0].text)
            assert len(data["matches"]) > 0
            print("search_code OK:", len(data["matches"]), "совпадений (СКД XML)")

            result = await session.call_tool("cleanup", {"dir_path": d})
            data = json.loads(result.content[0].text)
            assert data["count"] == 1
            print("cleanup OK")


if __name__ == "__main__":
    asyncio.run(main())
    print("E2E OK")
