class States:
    def __init__(self, rcp, rt, **kwargs):
        self.rcp = rcp
        self.rt  = rt
        self.batch_size = kwargs.get("batch_size", 1)

        # Tube/cap layout
        self.tube_list = [f"{r}{c}" for r in "AB" for c in range(1, 6)]
        self.cap_list  = [f"{r}{c}" for r in "CD" for c in range(1, 6)]
        self.tip_list  = [f"{r}{c}" for r in "ABCDEFGH" for c in range(1, 13)]

        # Pipetting
        self.vol = kwargs.get("vol", 400)
        self.immerse_depth = kwargs.get("immerse_depth", 20)
        self.shake_travel = kwargs.get("shake_travel", 7)

        # Mechanical
        self.falcon_rack_gravity_offset = 3
        self.cap_offset = [0, 0, 111 - 2, 0, 0, 0]
        self.cap_gravity_offset = 1
        self.decapper_tool_tcp_z_offset = -1
        self.printer_gravity_offset = 4

        # Inspection
        self.inspection_frq = kwargs.get("inspection_frq", 4)
        self.inspection_rot = kwargs.get("inspection_rot", 90)

        # Printing
        self.print_job = kwargs.get("print_job", True)
        self.dry_run_count = kwargs.get("dry_run_count", 1)

    def pipetted(self, i):
        """Pick tip, aspirate/dispense between tube pairs, eject tip."""
        rcp = self.rcp
        tip_index = self.tip_list[i % len(self.tip_list)]

        rcp["tip_rack"].pick_tip(tip_index)

        tube_a = self.tube_list[i % len(self.tube_list)]
        tube_b = self.tube_list[len(self.tube_list) - 1 - (i % len(self.tube_list))]

        # Aspirate from tube_a, dispense into tube_b
        rcp["falcon_pipette"].immerse(anchor=tube_a, depth=self.immerse_depth)
        rcp["falcon_pipette"].aspirate(vol=self.vol)
        rcp["falcon_pipette"].retract(anchor=tube_a)

        rcp["falcon_pipette"].immerse(anchor=tube_b, depth=self.immerse_depth)
        rcp["falcon_pipette"].dispense(vol=self.vol)
        rcp["falcon_pipette"].retract(anchor=tube_b)

        rcp["waste_bin"].eject_tip(shake_travel=self.shake_travel)

    def capped(self, i):
        """Place tube in decapper, cap it, pick up capped tube."""
        rcp = self.rcp
        tube = self.tube_list[i % len(self.tube_list)]
        cap = self.cap_list[i % len(self.cap_list)]

        rcp["falcon_rack"].pick(tube)
        rcp["decapper"].place()

        rcp["falcon_rack"].pick(cap)
        rcp["decapper"].cap(exit=False)
        rcp["decapper"].pick(
            approach=False,
            tool_tcp_z_offset=self.decapper_tool_tcp_z_offset,
        )

    def printed(self, i):
        """Place tube in printer, print or dry-run, pick up."""
        rcp = self.rcp

        rcp["printer"].place(exit=False, gravity_offset=self.printer_gravity_offset)

        if self.print_job:
            rcp["printer"].print_label(
                "D-1783", code_type="code128", autorun=True, verify=True,
            )
        else:
            rcp["printer"].dry_run_spin(count=self.dry_run_count)

        rcp["printer"].pick(approach=False)

    def inspected(self, i):
        """Present to inspector, rotate, place back in rack."""
        rcp = self.rcp
        tube = self.tube_list[i % len(self.tube_list)]

        rcp["inspector"].present(approach=False)
        for _ in range(self.inspection_frq):
            rcp["inspector"].rotate(rotation=self.inspection_rot)

        rcp["falcon_rack"].place(
            tube,
            gravity_offset=self.falcon_rack_gravity_offset,
            soft_approach=True,
        )

    def decapped(self, i):
        """Pick tube, decap, return cap and tube to rack."""
        rcp = self.rcp
        tube = self.tube_list[i % len(self.tube_list)]
        cap = self.cap_list[i % len(self.cap_list)]

        rcp["falcon_rack"].pick(tube)
        rcp["decapper"].place(exit=False)
        rcp["decapper"].decap(approach=False)

        # Place cap back
        rcp["falcon_rack"].place(
            cap,
            offset=self.cap_offset,
            soft_approach=True,
            gravity_offset=self.cap_gravity_offset,
        )

        # Pick decapped tube from decapper
        rcp["decapper"].pick(tool_tcp_z_offset=self.decapper_tool_tcp_z_offset)

        # Place tube back in rack
        rcp["falcon_rack"].place(
            tube,
            gravity_offset=self.falcon_rack_gravity_offset,
            soft_approach=True,
        )

    def make(self):
        return {
            "pipetted": self.pipetted,
            "capped": self.capped,
            "printed": self.printed,
            "inspected": self.inspected,
            "decapped": self.decapped,
        }
