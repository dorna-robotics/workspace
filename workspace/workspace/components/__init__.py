# workspace/components/__init__.py
"""
Auto-import all component modules in this package so their
@register decorators run and they get added to the factory.
"""

import pkgutil
import importlib

# Dynamically import every submodule in this package
for loader, module_name, is_pkg in pkgutil.iter_modules(__path__):
    importlib.import_module(f"{__name__}.{module_name}")
