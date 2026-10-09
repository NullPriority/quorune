from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=256)
def _module_exports(source: str) -> frozenset[str] | None:
    """Cache immutable exports by exact decoded source, never file metadata."""

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    exported = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    exported.update(
        target.id
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (
            node.targets if isinstance(node, ast.Assign) else (node.target,)
        )
        if isinstance(target, ast.Name)
    )
    return frozenset(exported)


def implementation_component_resolves(component: str) -> bool:
    """Resolve a package component without importing runtime game modules."""

    prefix = "quorune."
    if not component.startswith(prefix):
        return False
    parts = component.removeprefix(prefix).split(".")
    package_root = Path(__file__).resolve().parents[1]
    for length in range(len(parts), 0, -1):
        relative = Path(*parts[:length])
        module_path = package_root / relative.with_suffix(".py")
        if not module_path.is_file():
            module_path = package_root / relative / "__init__.py"
        if not module_path.is_file():
            continue
        remaining = parts[length:]
        if not remaining:
            return True
        try:
            exported = _module_exports(module_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError):
            return False
        return exported is not None and remaining[0] in exported
    return False
