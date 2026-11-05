"""Import the plugin directory as the package ``cash_forecast_plugin``.

Hermes loads a directory plugin as a package so its relative imports resolve;
the tests mirror that. Hermes itself is not needed to run them.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

PACKAGE = "cash_forecast_plugin"
ROOT = Path(__file__).resolve().parents[1]
sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != ROOT]

if PACKAGE not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        PACKAGE, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = module
    spec.loader.exec_module(module)



class _RepoRootIsNotATestPackage:
    """The repo root is the plugin package (its __init__.py uses relative imports), so pytest
    would try to import it standalone. Collect the root as a plain directory instead."""

    @pytest.hookimpl(tryfirst=True)
    def pytest_collect_directory(self, path, parent):
        if Path(path).resolve() == ROOT:
            return pytest.Dir.from_parent(parent, path=path)
        return None


def pytest_configure(config):
    if not config.pluginmanager.has_plugin("cash-forecast-repo-root"):
        config.pluginmanager.register(_RepoRootIsNotATestPackage(), "cash-forecast-repo-root")
