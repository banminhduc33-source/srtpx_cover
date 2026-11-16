"""Mọi module trong core/ không được import PySide6 (để test độc lập)."""
import ast
from pathlib import Path

CORE = Path(__file__).parent.parent / "srtpx_cover" / "core"


def test_core_does_not_import_qt():
    files = list(CORE.glob("*.py"))
    assert files
    for f in files:
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for n in names:
                assert not n.startswith(("PySide6", "PyQt", "shiboken")), f"{f.name} import {n}"
