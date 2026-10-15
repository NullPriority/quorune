from __future__ import annotations

"""Read-only syntax facts scoped to one source analysis or report build."""

import ast
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterator, Mapping


class SyntaxSnapshot:
    """Enumerate an unchanged parsed tree once; callers treat its nodes as read-only."""

    def __init__(self, tree: ast.AST) -> None:
        self.tree = tree
        self._walks: dict[ast.AST, tuple[ast.AST, ...]] = {}
        self._parents: Mapping[ast.AST, ast.AST] | None = None

    def walk(self, node: ast.AST | None = None) -> Iterator[ast.AST]:
        root = self.tree if node is None else node
        if root not in self._walks:
            self._walks[root] = tuple(ast.walk(root))
        return iter(self._walks[root])

    @property
    def parents(self) -> Mapping[ast.AST, ast.AST]:
        if self._parents is None:
            self._parents = MappingProxyType({
                child: parent
                for parent in self.walk()
                for child in ast.iter_child_nodes(parent)
            })
        return self._parents


@dataclass(frozen=True)
class ParsedSource:
    text: str
    syntax: SyntaxSnapshot

    @property
    def tree(self) -> ast.AST:
        return self.syntax.tree


class SourceFiles:
    """Read each source once within a build; a later build creates a fresh scope."""

    def __init__(self) -> None:
        self._sources: dict[tuple[Path, str], ParsedSource] = {}

    def read(self, path: Path, *, filename: str | None = None) -> ParsedSource:
        displayed_name = str(path) if filename is None else filename
        key = (path, displayed_name)
        if key not in self._sources:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text, filename=displayed_name)
            self._sources[key] = ParsedSource(text, SyntaxSnapshot(tree))
        return self._sources[key]


def analysis_syntax(analysis: Any) -> SyntaxSnapshot:
    tree = analysis.tree
    syntax = getattr(analysis, "syntax", None)
    if not isinstance(syntax, SyntaxSnapshot) or syntax.tree is not tree:
        return SyntaxSnapshot(tree)
    return syntax
