"""Make the Home-Assistant-free modules importable without executing the package __init__."""
import importlib
import pathlib
import sys
import types

import pytest

PKG = pathlib.Path(__file__).parents[1] / "custom_components" / "noxcast"

if "noxcast" not in sys.modules:
    _pkg = types.ModuleType("noxcast")
    _pkg.__path__ = [str(PKG)]
    sys.modules["noxcast"] = _pkg


def load(name: str):
    return importlib.import_module(f"noxcast.{name}")


def pytest_addoption(parser):
    parser.addoption("--update-snapshots", action="store_true",
                     help="write tests/snapshots/*.json from this run instead of comparing (see tests/snapshot.py)")


@pytest.fixture(autouse=True, scope="session")
def _sun_paths_kept():
    """Sun paths kept over the whole session: the tests share synthetic hours, which saves about a fifth of the
    run. The core is loaded twice (as noxcast.* here and as custom_components.noxcast.* by Home Assistant)."""
    with load("model").sun_paths_kept(), importlib.import_module("custom_components.noxcast.model").sun_paths_kept():
        yield
