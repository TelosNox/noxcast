"""Make the Home-Assistant-free modules importable without executing the package __init__."""
import importlib
import pathlib
import sys
import types

PKG = pathlib.Path(__file__).parents[1] / "custom_components" / "noxcast"

if "noxcast" not in sys.modules:
    _pkg = types.ModuleType("noxcast")
    _pkg.__path__ = [str(PKG)]
    sys.modules["noxcast"] = _pkg


def load(name: str):
    return importlib.import_module(f"noxcast.{name}")
