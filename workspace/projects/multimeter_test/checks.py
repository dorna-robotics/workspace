"""multimeter_test sensor/vision checks.

No checks for this project — the multimeter reads are unconditional.
The framework still loads this module by name from ``launch.yaml``;
exposing an empty ``Checks`` class keeps the contract intact.

Framework reference: ../../../docs/bt-framework-guide.md §13
"""


class Checks:
    def __init__(self, rcp, rt, **kwargs):
        self.rcp = rcp
        self.rt  = rt
