"""Recipe factory for apc_or_3.

Mirrors workspace/projects/syringe/util.py: the recipes (and their IK
config) are built here in one place; the notebook just drives them.

Calibration is per *recipe* — `recipe.calibrate()` stores one raw/corrected
point per clb anchor in core.json under that recipe's calibration_name:

    <component>_<left_approach>_<base_distance>_<rail_step>_<rail_span>

so we calibrate the exact production motion. One object, one recipe: each
component gets its own recipe class with its own params and calibration_name.

IK values below are the (old) calibration params carried over for now — tune
per component once the new rig is measured.
"""

from recipes.apc_base import ApcBase
from recipes.disc_holder_seven import DiscHolderSeven
from recipes.disc_holder_seven_output import DiscHolderSevenOutput


def create_recipes(workspace, core, speed_factor=1):
    return {
        "apc_base": ApcBase(
            workspace, core, workspace.components["apc_base_1"],
            base_distance=5, rail_step=5, rail_span=10, left_approach=True,
            speed_factor=speed_factor,
        ),
        "disc_holder_seven": DiscHolderSeven(
            workspace, core, workspace.components["disc_holder_seven_1"],
            base_distance=150, rail_step=5, rail_span=10, left_approach=False,
            speed_factor=speed_factor,
        ),
        "disc_holder_seven_output": DiscHolderSevenOutput(
            workspace, core, workspace.components["disc_holder_seven_output_1"],
            base_distance=150, rail_step=5, rail_span=10, left_approach=True,
            speed_factor=speed_factor,
        ),
    }
