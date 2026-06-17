import time
from dataclasses import dataclass
from typing import Optional

import serial


# Zebra / Symbol DS457 over SSI (Simple Serial Interface) on USB CDC.
# Set the scanner's Host Interface to "SSI over USB CDC" in 123Scan (Trigger

_SSI_TIMEOUT = 1.0

# --- SSI opcodes ---
_HOST               = 0x04
OP_CMD_ACK          = 0xD0
OP_CMD_NAK          = 0xD1
OP_DECODE_DATA      = 0xF3
OP_EVENT            = 0xF6
OP_PARAM_SEND       = 0xC6
OP_START_SESSION    = 0xE4
OP_STOP_SESSION     = 0xE5
OP_SCAN_ENABLE      = 0xE9
OP_SCAN_DISABLE     = 0xEA
OP_REQUEST_REVISION = 0xA3
OP_REPLY_REVISION   = 0xA4


@dataclass
class Scan:
    """Result of a scan.

    ``status``:
      - "ok"            a barcode was decoded; ``data`` holds it
      - "timeout"       nothing scanned within the time limit
      - "disconnected"  the serial port vanished / link error
      - "nak"           the scanner rejected the request
    """

    status: str
    data: str

    @property
    def connected(self) -> bool:
        return self.status != "disconnected"

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def __str__(self) -> str:
        return repr(self.data) if self.status == "ok" else self.status.upper()


class DS457:
    """Zebra/Symbol DS457 barcode scanner over SSI on a USB-CDC serial port.

    ``beep`` (set once here) controls whether the scanner beeps on a good read.
    """

    def __init__(self, port: str, baud: int = 9600, beep: bool = False):
        self.port = port
        self.baud = int(baud)
        self.beep = beep
        self.ser: Optional[serial.Serial] = None

    # ==================================================
    # Connection
    # ==================================================

    def is_connected(self) -> bool:
        return self.ser is not None and self.ser.is_open

    def connect(self) -> bool:
        """Open the port AND confirm the scanner actually answers over SSI.
        Returns False if the port won't open or the scanner doesn't respond."""
        if self.is_connected():
            return True
        try:
            self.ser = serial.Serial(self.port, self.baud, timeout=_SSI_TIMEOUT)
        except (serial.SerialException, OSError):
            self.ser = None
            return False
        try:
            self.ser.write(b"\x00")          # nudge awake
            time.sleep(0.05)
            self.ser.reset_input_buffer()
        except (serial.SerialException, OSError):
            self.close()
            return False
        if not self._alive():                # scanner must actually respond
            self.close()
            return False
        # Configure the decoder:
        #   238 = 1  -> send decode data as SSI packets (so we can frame it)
        #    56      -> beep after good decode, on/off per the `beep` setting
        self._set_param(238, 1)
        self._set_param(56, 1 if self.beep else 0)
        time.sleep(0.1)
        self._resync()
        return True

    def close(self) -> None:
        if self.ser is not None:
            try:
                self.ser.close()
            finally:
                self.ser = None

    def _alive(self) -> bool:
        """Round-trip an SSI request to confirm the scanner is really there."""
        for _ in range(2):
            self._resync()
            if not self._write(OP_REQUEST_REVISION):
                return False
            pkt = self._read_packet(_SSI_TIMEOUT)
            if pkt is not None and pkt[0] == OP_REPLY_REVISION:
                return True
        return False

    # ==================================================
    # Scanning  (host-triggered, one-shot)
    # ==================================================

    def scan_disable(self) -> None:
        """Turn scanning OFF. Fire-and-forget; scan_enable() re-enables.
        (scan_enable already calls this when done, so you rarely need it.)"""
        self._write(OP_STOP_SESSION)
        self._write(OP_SCAN_DISABLE)

    def scan_enable(self, timeout: float = 10.0) -> Scan:
        """Capture one barcode now: enable + trigger, wait up to ``timeout``
        seconds, then auto-disable (whether it scanned or not). Just call this
        each time you want a scan."""
        if not self.is_connected():
            return Scan("disconnected", "")
        self.ser.reset_input_buffer()
        self._command(OP_SCAN_ENABLE)
        result = self._capture(timeout)
        self.scan_disable()                  # leave it off when done
        return result

    def _capture(self, timeout: float) -> Scan:
        """START_SESSION, wait for DECODE_DATA, ACK it."""
        if not self._write(OP_START_SESSION):
            return Scan("disconnected", "")
        deadline = time.time() + timeout
        result: Optional[Scan] = None
        while time.time() < deadline:
            pkt = self._read_packet(deadline - time.time())   # full remaining time
            if not self.is_connected():
                return Scan("disconnected", "")
            if pkt is None:
                continue
            opcode, _status, data = pkt
            if opcode == OP_DECODE_DATA:
                self._ack()                                   # host must ACK decode data
                result = Scan("ok", data[1:].decode("ascii", errors="ignore"))
                break
            if opcode == OP_CMD_NAK:
                result = Scan("nak", "")
                break
            if opcode == OP_EVENT:
                self._ack()

        if not self.is_connected():
            return Scan("disconnected", "")
        self._resync()
        return result or Scan("timeout", "")

    # ==================================================
    # SSI packet layer (internal)
    # ==================================================

    @staticmethod
    def _checksum(body: bytes) -> bytes:
        cks = (0x10000 - (sum(body) & 0xFFFF)) & 0xFFFF
        return bytes([(cks >> 8) & 0xFF, cks & 0xFF])

    def _frame(self, opcode: int, data: bytes = b"") -> bytes:
        body = bytes([4 + len(data), opcode, _HOST, 0x00]) + data
        return body + self._checksum(body)

    def _write(self, opcode: int, data: bytes = b"") -> bool:
        if self.ser is None:
            return False
        try:
            self.ser.write(self._frame(opcode, data))
            self.ser.flush()        # push to the wire now — decoder waits on our ACK
            return True
        except (serial.SerialException, OSError):
            self.close()
            return False

    def _read_exact(self, n: int, deadline: float) -> Optional[bytes]:
        buf = bytearray()
        while len(buf) < n:
            if time.time() >= deadline:
                return None
            try:
                chunk = self.ser.read(n - len(buf))
            except (serial.SerialException, OSError):
                self.close()
                return None
            if chunk:
                buf += chunk
        return bytes(buf)

    def _read_packet(self, timeout: float) -> Optional[tuple]:
        """Read one SSI packet → (opcode, status, data). ``timeout`` bounds only
        the wait for the packet's first byte; the rest is read uninterrupted so
        a short slice can't cut a packet in half (which would desync)."""
        if self.ser is None:
            return None
        first = self._read_exact(1, time.time() + timeout)
        if not first:
            return None
        length = first[0]
        if length < 4:                                   # not a valid SSI header
            self._resync()
            return None
        rest = self._read_exact(length + 1, time.time() + max(_SSI_TIMEOUT, 1.0))
        if rest is None:
            self._resync()
            return None
        packet = first + rest
        body, cks = packet[:length], packet[length:length + 2]
        if self._checksum(body) != cks:
            self._resync()
            return None
        return body[1], body[3], bytes(body[4:length])   # (opcode, status, data)

    def _resync(self) -> None:
        if self.ser is None:
            return
        try:
            self.ser.reset_input_buffer()
        except (serial.SerialException, OSError):
            self.close()

    def _ack(self) -> None:
        self._write(OP_CMD_ACK)

    def _command(self, opcode: int, data: bytes = b"") -> bool:
        """Send a host command and wait for the decoder's CMD_ACK."""
        if not self._write(opcode, data):
            return False
        pkt = self._read_packet(_SSI_TIMEOUT)
        return pkt is not None and pkt[0] == OP_CMD_ACK

    def _set_param(self, param: int, value: int) -> bool:
        """Set a single-byte parameter (temporary). Internal — used by connect()."""
        return self._command(OP_PARAM_SEND, bytes([0xFF, param & 0xFF, value & 0xFF]))
