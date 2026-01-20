import serial
import serial.tools.list_ports
import time
from typing import Optional


class Keyto:
    def __init__(
        self,
        port: Optional[str] = None,
        baud: int = 38400,
    ):
        self.port = port
        self.baud = baud

        self.ser: Optional[serial.Serial] = None

        # calibration
        self.liquid_steps_per_ul = 100  # 10000 steps = 100 µL

    # ==================================================
    # Connection lifecycle
    # ==================================================

    def is_connected(self) -> bool:
        return self.ser is not None and self.ser.is_open

    def connect(self) -> bool:
        """
        Connect strategy:
        1) Already connected → True
        2) Try self.port
        3) Auto-scan ports
        4) Fail cleanly
        On success, initialize() is called exactly once.
        """
        if self.is_connected():
            return True

        # 1) Preferred port
        if self.port and self._open_and_handshake(self.port):
            return self.initialize()

        # 2) Auto-connect
        if self._auto_connect():
            return self.initialize()

        # 3) Fail
        return False

    def close(self) -> None:
        if self.ser:
            try:
                self.ser.close()
            finally:
                self.ser = None

    # ==================================================
    # Internal connection helpers
    # ==================================================

    def _open_and_handshake(self, port: str) -> bool:
        try:
            self.ser = serial.Serial(port, self.baud, timeout=2)
            self.port = port
            return self._handshake()
        except serial.SerialException:
            self.ser = None
            return False

    def _auto_connect(self) -> bool:
        for p in serial.tools.list_ports.comports():
            if self._probe_port(p.device):
                return self._open_and_handshake(p.device)
        return False

    def _probe_port(self, port: str) -> bool:
        try:
            with serial.Serial(port, self.baud, timeout=0.5) as ser:
                ser.write(b"1>?\r")
                time.sleep(0.1)
                resp = ser.readline().decode("ascii", errors="ignore").strip()
                return "1<" in resp
        except serial.SerialException:
            return False

    def _handshake(self) -> bool:
        resp = self._send("1>?")
        return "1<" in resp

    # ==================================================
    # Low-level I/O
    # ==================================================

    def _send(self, cmd: str) -> str:
        if not self.is_connected():
            return ""

        try:
            self.ser.write(f"{cmd}\r".encode("ascii"))
            time.sleep(0.15)
            return self.ser.readline().decode("ascii", errors="ignore").strip()
        except serial.SerialException:
            self.close()
            return ""

    def _wait_until_idle(self, timeout: float = 15.0) -> bool:
        start = time.time()
        while time.time() - start < timeout:
            if "1<0" in self._send("1>?"):
                return True
            time.sleep(0.3)
        return False

    # ==================================================
    # Public device API
    # ==================================================

    def has_tip(self) -> bool:
        resp = self._send("1>Rr3")
        return "12:1" in resp


    def initialize(self, speed: int = 16000) -> bool:
        """
        Mode 1 = Home only
        """
        res = self._send(f"1>It{speed},100,1")
        if "1<2" in res:
            return self._wait_until_idle()
        return False


    def eject_tip(self, speed: int = 64000) -> bool:
        """
        Mode 0 = Home + Eject
        """
        res = self._send(f"1>It{speed},100,0")
        if "1<2" in res:
            return self._wait_until_idle()
        return False


    def aspirate(self, volume_ul: float, speed: int = 200) -> bool:
        steps = int(volume_ul * self.liquid_steps_per_ul)
        res = self._send(f"1>Ia{steps},{speed},10")
        return "1<2" in res and self._wait_until_idle()


    def dispense(
        self,
        volume_ul: float,
        speed: int = 500,
        blowout: bool = False,
    ) -> bool:
        steps = int(volume_ul * self.liquid_steps_per_ul)
        stop_spd = 500 if blowout else 10
        res = self._send(f"1>Da{steps},0,{speed},{stop_spd}")
        return "1<2" in res and self._wait_until_idle()
