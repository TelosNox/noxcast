"""The architecture's boundaries as tests: a change that crosses one fails here instead of in a review."""
import ast
from pathlib import Path

import pytest

PKG = Path(__file__).parent.parent / "custom_components" / "noxcast"
SHELL = {"__init__", "config_flow", "coordinator", "diagnostics", "energy", "image", "panel", "sensor", "sources", "weather"}


def imports(name: str) -> tuple[set[str], set[str]]:
    """(external top-level packages, internal modules) a module imports, also inside functions."""
    external, internal = set(), set()
    for node in ast.walk(ast.parse((PKG / f"{name}.py").read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            external |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                internal |= {node.module} if node.module else {a.name for a in node.names}
            else:
                external.add(node.module.split(".")[0])
    return external, internal


@pytest.mark.parametrize("name", sorted(p.stem for p in PKG.glob("*.py") if p.stem not in SHELL))
def test_the_core_does_not_know_home_assistant(name):
    assert not imports(name)[0] & {"homeassistant", "voluptuous"}


@pytest.mark.parametrize("name", ["load", "battery", "plan", "flows"])
def test_load_and_planning_see_pv_only_as_a_series(name):
    # they get the PV forecast as a plain series from forecasting.surplus; the model stays out of reach
    assert imports(name)[1] <= {"const"}
