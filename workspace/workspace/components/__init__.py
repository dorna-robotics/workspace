# workspace/components/__init__.py
"""
Auto-import all component modules in this package (recursively)
so their @register decorators run and they get added to the factory.
"""

import pkgutil
import importlib

# Walk over all submodules and subpackages under this package
for finder, module_name, ispkg in pkgutil.walk_packages(__path__, prefix=__name__ + "."):
    importlib.import_module(module_name)
