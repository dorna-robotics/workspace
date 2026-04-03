class States:
    def __init__(self, rcp, rt, batch_size, **kwargs):
        self.rcp = rcp
        self.rt  = rt
        self.batch_size = batch_size

        # Config
        self.tube_list = [f"{r}{c}" for r in "AB" for c in range(1, 6)]
        self.cap_list  = [f"{r}{c}" for r in "CD" for c in range(1, 6)]
        self.tip_list  = [f"{r}{c}" for r in "ABCDEFGH" for c in range(1, 13)]

        self.vol = 400
        self.immerse_depth = 20
        self.shake_travel = 7
        self.falcon_rack_gravity_offset = 3
        self.cap_offset = [0, 0, 111 - 2, 0, 0, 0]
        self.cap_gravity_offset = 1
        self.decapper_tool_tcp_z_offset = -1
        self.printer_gravity_offset = 4
        self.inspection_frq = 4
        self.inspection_rot = 90

    def pipetted(self, i):
        """Aspirate and dispense for item i."""
        rcp = self.rcp
        tip_index = self.tip_list[i]

        rcp["tip_rack"].pick_tip(tip_index)

        tube_index_a = self.tube_list[i]
        tube_index_b = self.tube_list[len(self.tube_list) - (i + 1)]

        rcp["falcon_pipette"].immerse(anchor=tube_index_a, depth=self.immerse_depth)
        rcp["falcon_pipette"].aspirate(vol=self.vol)
        rcp["falcon_pipette"].retract(anchor=tube_index_a)

        rcp["falcon_pipette"].immerse(anchor=tube_index_b, depth=self.immerse_depth)
        rcp["falcon_pipette"].dispense(vol=self.vol)
        rcp["falcon_pipette"].retract(anchor=tube_index_b)

        rcp["waste_bin"].eject_tip(shake_travel=self.shake_travel)

    def capped(self, i):
        """Cap tube i."""
        rcp = self.rcp
        tube_index = self.tube_list[i]
        cap_index = self.cap_list[i]

        rcp["falcon_rack"].pick(tube_index)
        rcp["decapper"].place()
        rcp["falcon_rack"].pick(cap_index)
        rcp["decapper"].cap(exit=False)
        rcp["decapper"].pick(approach=False, tool_tcp_z_offset=self.decapper_tool_tcp_z_offset)

    def printed(self, i):
        """Print label on tube i."""
        rcp = self.rcp
        rcp["printer"].place(exit=False, gravity_offset=self.printer_gravity_offset)
        rcp["printer"].print_label("D-1783", code_type="code128", autorun=True, verify=True)
        rcp["printer"].pick(approach=False)

    def inspected(self, i):
        """Inspect tube i and place back."""
        rcp = self.rcp
        tube_index = self.tube_list[i]

        rcp["inspector"].present(approach=False)
        for _ in range(self.inspection_frq):
            rcp["inspector"].rotate(rotation=self.inspection_rot)

        rcp["falcon_rack"].place(
            tube_index,
            gravity_offset=self.falcon_rack_gravity_offset,
            soft_approach=True,
        )

    def decapped(self, i):
        """Decap tube i and return to rack."""
        rcp = self.rcp
        tube_index = self.tube_list[i]
        cap_index = self.cap_list[i]

        rcp["falcon_rack"].pick(tube_index)
        rcp["decapper"].place(exit=False)
        rcp["decapper"].decap(approach=False)

        rcp["falcon_rack"].place(
            cap_index,
            offset=self.cap_offset,
            soft_approach=True,
            gravity_offset=self.cap_gravity_offset,
        )

        rcp["decapper"].pick(tool_tcp_z_offset=self.decapper_tool_tcp_z_offset)

        rcp["falcon_rack"].place(
            tube_index,
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
