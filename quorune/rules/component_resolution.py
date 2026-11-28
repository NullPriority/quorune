from __future__ import annotations

import ast
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
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


class _ComponentSources:
    def __init__(self) -> None:
        self.root = Path(__file__).resolve().parents[1]
        self.modules: dict[tuple[str, ...], Path | None] = {}
        self.containing_directories: dict[Path, bool] = {}
        self.exports: dict[Path, frozenset[str] | None] = {}

    def module(self, parts: tuple[str, ...]) -> Path | None:
        if parts not in self.modules:
            relative = self.root.joinpath(*parts)
            module = relative.with_suffix(".py")
            parent = module.parent
            if parent not in self.containing_directories:
                self.containing_directories[parent] = parent.is_dir()
            if not self.containing_directories[parent]:
                self.modules[parts] = None
            elif module.is_file():
                self.modules[parts] = module
            else:
                package = relative / "__init__.py"
                self.modules[parts] = package if package.is_file() else None
        return self.modules[parts]

    def symbols(self, module: Path) -> frozenset[str] | None:
        if module not in self.exports:
            try:
                self.exports[module] = _module_exports(module.read_text(encoding="utf-8"))
            except (OSError, UnicodeError):
                self.exports[module] = None
        return self.exports[module]


_COMPONENT_SOURCES: ContextVar[_ComponentSources | None] = ContextVar(
    "component_sources", default=None
)


@contextmanager
def _component_source_snapshot():
    """Read each implementation module at most once in a registry validation."""
    if _COMPONENT_SOURCES.get() is not None:
        yield
        return
    token = _COMPONENT_SOURCES.set(_ComponentSources())
    try:
        yield
    finally:
        _COMPONENT_SOURCES.reset(token)


component_resolution_snapshot = _component_source_snapshot


def implementation_component_resolves(component: str) -> bool:
    """Resolve a package component without importing runtime game modules."""
    prefix = "quorune."
    if not component.startswith(prefix):
        return False
    parts = tuple(component.removeprefix(prefix).split("."))
    sources = _COMPONENT_SOURCES.get() or _ComponentSources()
    for length in range(len(parts), 0, -1):
        module = sources.module(parts[:length])
        if module is None:
            continue
        if length == len(parts):
            return True
        exported = sources.symbols(module)
        return exported is not None and parts[length] in exported
    return False
