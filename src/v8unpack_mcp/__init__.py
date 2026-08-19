"""v8unpack-mcp — чтение и поиск по бинарным файлам 1С без EDT."""

__version__ = "0.2.0"

from .core import (unpack, repack, list_objects, get_metadata,
                   read_module, read_bytecode, search_code, set_help, cleanup)
from .diffing import diff

__all__ = [
    "unpack",
    "repack",
    "list_objects",
    "get_metadata",
    "read_module",
    "read_bytecode",
    "search_code",
    "set_help",
    "diff",
    "cleanup",
    "__version__",
]
