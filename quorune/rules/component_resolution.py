from __future__ import annotations

import ast
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from functools import cache, lru_cache
from pathlib import Path


@lru_cache(maxsize=1024)
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


_RESOLVE_COMPONENT: ContextVar[Callable[[str], bool] | None] = ContextVar(
    "resolve_component", default=None,
)


def _component_resolver() -> Callable[[str], bool]:
    package_root = Path(__file__).resolve().parents[1]

    @cache
    def module_location(parts: tuple[str, ...]) -> Path | None:
        relative = Path(*parts)
        module_path = package_root / relative.with_suffix(".py")
        if module_path.is_file():
            return module_path
        package_path = package_root / relative / "__init__.py"
        return package_path if package_path.is_file() else None

    @cache
    def exports(module_path: Path) -> frozenset[str] | None:
        try:
            return _module_exports(module_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError):
            return None

    def resolve(component: str) -> bool:
        prefix = "quorune."
        if not component.startswith(prefix):
            return False
        parts = tuple(component.removeprefix(prefix).split("."))
        for length in range(len(parts), 0, -1):
            module_path = module_location(parts[:length])
            if module_path is None:
                continue
            if length == len(parts):
                return True
            exported = exports(module_path)
            return exported is not None and parts[length] in exported
        return False

    return resolve


@contextmanager
def component_resolution_snapshot():
    """Read each module once per registry validation and refresh next time."""
    token = _RESOLVE_COMPONENT.set(_component_resolver())
    try:
        yield
    finally:
        _RESOLVE_COMPONENT.reset(token)


def implementation_component_resolves(component: str) -> bool:
    """Resolve a package component without importing runtime game modules."""
    resolve = _RESOLVE_COMPONENT.get() or _component_resolver()
    return resolve(component)
