"""Regras de arquitetura verificadas no código-fonte, para não se perderem com o tempo."""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).parents[2] / "src" / "fincore"
ORM_METHODS = {"filter", "get", "get_or_none", "create", "exists", "all", "bulk_create"}


def _modules(folder: str) -> list[Path]:
    return sorted((SRC / folder).rglob("*.py"))


def _imported_roots(tree: ast.AST) -> set[str]:
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module)
    return roots


@pytest.mark.parametrize("path", _modules("domain"), ids=lambda path: path.name)
def test_domain_does_not_depend_on_framework_orm_or_other_layers(path: Path) -> None:
    imported = _imported_roots(ast.parse(path.read_text()))
    forbidden = {"tortoise", "robyn", "asyncpg", "jinja2"}
    assert not {name.split(".")[0] for name in imported} & forbidden
    assert not [
        name
        for name in imported
        if name.startswith("fincore.") and not name.startswith("fincore.domain")
    ]


@pytest.mark.parametrize(
    "path",
    [SRC / "presentation" / "web.py", *_modules("presentation/routes")],
    ids=lambda path: path.name,
)
def test_presentation_does_not_query_the_orm_directly(path: Path) -> None:
    models = {
        node.name
        for node in ast.parse((SRC / "infrastructure" / "models.py").read_text()).body
        if isinstance(node, ast.ClassDef)
    }
    offending = [
        f"{node.func.value.id}.{node.func.attr} (linha {node.lineno})"
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in models
        and node.func.attr in ORM_METHODS
    ]
    assert offending == [], "use fincore.application.queries ou um caso de uso"
