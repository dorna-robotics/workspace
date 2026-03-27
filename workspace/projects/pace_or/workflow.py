# pace_or/workflow.py
# Wires steps + checks into the runner. No handler logic lives here.
#
# To add a new step:   edit steps.py
# To add a new check:  edit checks.py + protocol.yaml
# To change positions: edit 2_params/params.yaml
# To change sequence:  edit 3_protocol/protocol.yaml

from pathlib import Path
from workspace.ortools.workflow import BaseWorkflow
from steps import make_steps
from checks import (
    source_tube_present,
    tube_in_working_rack,
    shaker_slot_empty,
    tube_on_shaker,
    cap_holder_empty,
    cap_in_holder,
    tube_in_2ml_rack,
)

_BASE_DIR = Path(__file__).parent


class Workflow(BaseWorkflow):

    def __init__(self, workspace, core, n_items=4, horizon=None):
        super().__init__(workspace, core, _BASE_DIR, n_items=n_items, horizon=horizon)

    def _register_all(self):
        steps = make_steps(self.rcp, self.cfg, self.rt, self.n)

        for name, fn in steps.items():
            if isinstance(fn, tuple):
                self.runner.register(name, fn[0], cleanup=fn[1])
            else:
                self.runner.register(name, fn)

        rc = self.runner.register_check
        rc("source_tube_present",  source_tube_present)
        rc("tube_in_working_rack", tube_in_working_rack)
        rc("shaker_slot_empty",    shaker_slot_empty)
        rc("tube_on_shaker",       tube_on_shaker)
        rc("cap_holder_empty",     cap_holder_empty)
        rc("cap_in_holder",        cap_in_holder)
        rc("tube_in_2ml_rack",     tube_in_2ml_rack)


def workflow_fn(*, workspace, core):
    # n_items=4 hardcoded; replace with camera.count_tubes() when ready
    wf = Workflow(workspace, core, n_items=4, horizon=None)
    wf.run()
