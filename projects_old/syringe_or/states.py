class States:
    def __init__(self, rcp, rt, **kwargs):
        self.rcp = rcp
        self.rt  = rt
        self.batch_size = kwargs.get("batch_size", 1)

        # Tube/cap layout
        self.tube_list = [f"{r}{c}" for r in "ABCDEF" for c in range(1, 9)]
        self.cap_list  = [f"{r}{c}" for r in "ABCDEF" for c in range(1, 9)]

        # Config
        self.run_per_rack = kwargs.get("run_per_rack", 1)
        self.hotel_levels = kwargs.get("hotel_levels", 4)
        self.tube_rack_gravity_offset = 4
        self.cap_holder_gravity_offset = -15
        self.cap_holder_tool_tcp_z_offset = 1
        self.decapper_tool_tcp_z_offset = -3

        # Track hotel level across batch items
        self._level = 0
        self._cap_index = 0

    def hotel_picked(self, i):
        """Pick SBS plate from hotel and place in adapter."""
        rcp = self.rcp
        rcp["hotel"].pick(self._level)
        rcp["sbs_adapter"].place()

    def caps_fed(self, i):
        """Pick caps from feeder and place in cap holder."""
        rcp = self.rcp
        for idx in range(self._cap_index, self._cap_index + self.run_per_rack):
            rcp["feeder"].above(anchor="plate_center")
            rcp["feeder"].present_cap(rcp.get("inspector"))
            rcp["feeder"].pick(approach=False)
            rcp["cap_holder"].place(
                self.cap_list[idx],
                gravity_offset=self.cap_holder_gravity_offset,
            )

    def capped_dispensed(self, i):
        """Cap tubes with dispensing for each tube in this rack."""
        rcp = self.rcp
        for idx in range(self._cap_index, self._cap_index + self.run_per_rack):
            # Pick tube
            rcp["sbs_plate"].pick(self.tube_list[idx])
            # Place in decapper
            rcp["decapper"].place()
            # Pick cap
            rcp["cap_holder"].pick(
                self.cap_list[idx],
                tool_tcp_z_offset=self.cap_holder_tool_tcp_z_offset,
            )
            # Dispense
            rcp["dispense_arm"].down()
            rcp["dispense_arm"].dispense()
            rcp["dispense_arm"].up()
            # Cap
            rcp["decapper"].cap(exit=False)
            # Pick capped tube
            rcp["decapper"].pick(
                approach=False,
                tool_tcp_z_offset=self.decapper_tool_tcp_z_offset,
            )
            # Return to plate
            rcp["sbs_plate"].place(
                self.tube_list[idx],
                gravity_offset=self.tube_rack_gravity_offset,
            )

    def hotel_placed(self, i):
        """Pick plate from adapter and return to hotel."""
        rcp = self.rcp
        rcp["sbs_adapter"].pick(anchor="place")
        rcp["hotel"].place(self._level)

        # Advance level and cap index
        self._level = (self._level + 1) % self.hotel_levels
        if self._level == 0:
            self._cap_index += self.run_per_rack

    def make(self):
        return {
            "hotel_picked": self.hotel_picked,
            "caps_fed": self.caps_fed,
            "capped_dispensed": self.capped_dispensed,
            "hotel_placed": self.hotel_placed,
        }
