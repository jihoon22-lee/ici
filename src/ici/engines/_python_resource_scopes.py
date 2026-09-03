"""Lexical-scope import and binding collection for Python resource rules."""

from __future__ import annotations

import ast


class _ScopeVisitor(ast.NodeVisitor):
    """Base visitor that never leaks bindings across lexical scope boundaries."""

    def __init__(self, root: ast.AST) -> None:
        self.root = root

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node is self.root:
            self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if node is self.root:
            self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if node is self.root:
            self.generic_visit(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        del node

    def visit_ListComp(self, node: ast.ListComp) -> None:
        del node

    def visit_SetComp(self, node: ast.SetComp) -> None:
        del node

    def visit_DictComp(self, node: ast.DictComp) -> None:
        del node

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        del node


class _ImportVisitor(_ScopeVisitor):
    def __init__(self, root: ast.AST) -> None:
        super().__init__(root)
        self.aliases: dict[str, str] = {}

    def visit_Import(self, node: ast.Import) -> None:
        for item in node.names:
            local = item.asname or item.name.split(".", 1)[0]
            self.aliases[local] = item.name if item.asname else local

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            for item in node.names:
                if item.name != "*":
                    self.aliases[item.asname or item.name] = f"{node.module}.{item.name}"


class _BindingVisitor(_ScopeVisitor):
    def __init__(self, root: ast.AST) -> None:
        super().__init__(root)
        self.names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store):
            self.names.add(node.id)

    def visit_arg(self, node: ast.arg) -> None:
        self.names.add(node.arg)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node is not self.root:
            self.names.add(node.name)
        super().visit_FunctionDef(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if node is not self.root:
            self.names.add(node.name)
        super().visit_AsyncFunctionDef(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if node is not self.root:
            self.names.add(node.name)
        super().visit_ClassDef(node)


def collect_import_aliases(scope: ast.AST) -> dict[str, str]:
    """Return imports bound directly in one lexical scope."""

    visitor = _ImportVisitor(scope)
    visitor.visit(scope)
    return visitor.aliases


def collect_scope_imports(scope: ast.AST) -> dict[str, str]:
    """Return resource-related builtins plus imports in one lexical scope."""

    aliases: dict[str, str] = {
        "bytearray": "builtins.bytearray",
        "dict": "builtins.dict",
        "list": "builtins.list",
        "open": "builtins.open",
        "set": "builtins.set",
    }
    aliases.update(collect_import_aliases(scope))
    return aliases


def collect_scope_bindings(scope: ast.AST) -> frozenset[str]:
    """Return names bound by one scope without descending into children."""

    visitor = _BindingVisitor(scope)
    visitor.visit(scope)
    return frozenset(visitor.names)
