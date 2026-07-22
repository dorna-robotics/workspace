from copy import deepcopy
from mergedeep import merge
from workspace.recipes.recipe import Recipe
from dorna2 import pose as dorna_pose


class Printer(Recipe):
    DEFAULTS = dict(
        # ref joint
        target_anchor="place",
        base_distance = 100,
        rail_step=20, #10
        rail_span=5, # 5 
    )

    def __init__(self, workspace, core, component, **kwargs):
        # prm
        prm = deepcopy(Recipe.DEFAULTS) # default
        merge(prm, self.DEFAULTS) # self
        merge(prm, kwargs) # kwargs

        super().__init__(
            workspace=workspace,
            core=core,
            component=component,
            **prm
        )
        

    def pick(self, anchor="place", solid_name="body", approach=True, exit=True, attachment=True, trigger_io=True, padding=30, gap=2, **kwargs):
        """Pick a printed item off the printer. Thin override, padding=30 mm."""
        return super().pick(anchor=anchor, solid_name=solid_name, approach=approach, exit=exit, attachment=attachment, trigger_io=trigger_io, padding=padding, gap=gap, **kwargs)



    def place(self, anchor="place", solid_name="body", approach=True, exit=True, attachment=True, trigger_io=True, padding=30, gap=2, load_anchor="center", gravity_offset=2, **kwargs):
        """Place a tube on the printer pad, computing the XY offset from the tube radius.

        Reads the held solid's component size to compute the lateral offset
        via ``printer._place_offset(radius)`` so the tube sits correctly under
        the print head. Uses ``gravity_offset=2`` for release clearance.
        """
        # tool
        tool = self.core.current_tool()

        # item in tool
        solid_in_tool = self.solid_attached_to_tool(tool)

        # place offset based on the radius of the tube
        offset= self.component._place_offset(
            self.workspace.components[solid_in_tool.component].size[0]/2
        )

        # place
        return super().place(anchor=anchor, solid_name=solid_name, offset=offset, approach=approach, exit=exit, attachment=attachment, trigger_io=trigger_io, padding=padding, gap=gap, load_anchor=load_anchor, gravity_offset=gravity_offset, **kwargs)
    

    # Device ops are atomic operations on the component (component-guide
    # §7) and the component is sim-agnostic (the station owns the one
    # sim/real branch), so these are pure pass-throughs — no branching on
    # ``_simulation_mode`` here. ``sim_return`` rides along per call
    # (device-guide §17).

    def dry_run_spin(self, count=1, sim_return=True):
        """Cycle the applicator ``count`` times without printing (bool)."""
        return self.component.dry_run_spin(count=count, sim_return=sim_return)

    def print_label(self, data, code_type="code128", autorun=True, verify=True,
                    sim_return=True):
        """Print ``data`` as a label with encoding ``code_type`` (bool).

        Args:
            data: Text to encode.
            code_type: Barcode/QR encoding family (``code128``, ``qrcode``,
                ``datamatrix``).
            autorun: If True, advance the label automatically after printing.
            verify: If True, block until the printer confirms the job id
                finished (``ESC s`` Y→N plus an ``ESC j`` match).
            sim_return: Value returned in simulation — shaped like the
                real return (a ``bool``).
        """
        return self.component.print_label(
            data, code_type=code_type, autorun=autorun, verify=verify,
            sim_return=sim_return,
        )
