from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid

from workspace.components.factory import register
from components.bk879b_wrapper import BK879B


# BK Precision 879B LCR meter — a stationary bench instrument (no robot
# motion). Same shape as the printer component: the component owns the
# serial device and the simulation flag; the recipe (recipes/lcr_reader.py)
# exposes the clean measurement calls.
#
# Connection is over USB (a mini-USB virtual COM port), not ethernet — so
# the port can be auto-detected: leave `port` unset and the driver scans the
# serial ports for the meter's FTDI / CP210x USB chip (same idea the pipette
# uses). Set `port` explicitly only to pin a specific COM port.
#
# Wire it up in scene/base.j2:
#   lcr_meter_1:
#     type: "lcr_meter"
#     simulation: false        # port omitted -> auto-detect the USB port
#     # port: "/dev/ttyUSB0"   # or pin it explicitly
@register("lcr_meter")
class LCRMeter:
    DEFAULTS = dict(
        anchors={"body": {
            "center": [0, 0, 0, 0, 0, 0],
            "top":    [0, 0, 190, 0, 0, 0],
        }},
        collision_box={"body": [
            # 90 x 90 footprint, 190 mm tall (centered at half height).
            {"pose": [0, -30, 95, 0, 0, 0], "scale": [90, 90, 190]},
        ]},
        # cfg
        port=None,
        simulation=True,
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        prm = deepcopy(self.DEFAULTS)
        merge(prm, cfg)
        merge(prm, kwargs)
        prm.setdefault("type", getattr(self.__class__, "_registered_type", cfg.get("type")))

        self.name = name
        self.workspace = workspace
        self.type = prm["type"]

        self.assembly = {
            k: Solid(
                type=self.type,
                anchors=prm["anchors"][k],
                component=self.name,
                **({"collision_box": cb[k]} if (cb := prm.get("collision_box")) and k in cb else {}),
            )
            for k in prm["anchors"]
        }

        # simulation flag — the recipe reads this to choose real vs. fake.
        self._simulation_mode = prm["simulation"]

        # device — when live, connect now (printer pattern). port may be None,
        # in which case the driver auto-detects the meter's USB serial port.
        self.device_port = prm["port"]
        self.device = None
        if not self._simulation_mode:
            device = BK879B(port=self.device_port)
            if device.connect():                       # None -> scan for FTDI/CP210x
                ok, idn = device.check_connection()
                if ok:
                    device.initialize()
                    self.device = device
                    print(f"✅ {self.name} connected @ {device.port} ({idn})")
                else:
                    print(f"❌ {self.name} no RMT response @ {device.port} "
                          "(press USB on the meter until RMT shows)")
            else:
                where = self.device_port or "auto-detect (no FTDI/CP210x USB serial port found)"
                print(f"❌ {self.name} connection failed @ {where}")

    # change the simulation mode (matches the printer component)
    def simulation(self, mode):
        self._simulation_mode = mode

    # release the serial port / hand the meter back to its front panel
    def close(self):
        if self.device is not None:
            self.device.close()
            self.device = None
            print(f"{self.name} closed")
            return True
        return False
