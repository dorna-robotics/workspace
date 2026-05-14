"""Orchestrator entry point. The framework handles everything.

Identical across every BT project — copy verbatim. Customisation goes
into ``actions.py``; if you need to override the default protocol
runner, drop a ``workflow.py`` next to this file with a
``run(workspace, core, **kwargs)`` function and ``launcher.main``
will use it automatically.
"""

from workspace.bt.launcher import main


if __name__ == "__main__":
    main(__file__)
