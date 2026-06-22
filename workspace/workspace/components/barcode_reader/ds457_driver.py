import time
from dataclasses import dataclass
from typing import Iterable, Optional

import serial


# Zebra / Symbol DS457 over SSI (Simple Serial Interface) on USB CDC.
# Set the scanner's Host Interface to "SSI over USB CDC" in 123Scan; connect()
# applies the rest of the settings (host trigger, packeting, ...) each session,
# so the driver works regardless of how the saved 123Scan profile is configured.

_SSI_TIMEOUT = 1.0

# Quiet gap (s) meaning "no more retransmits coming". Used by _settle_decode()
# only on the ACK/NAK fallback path; connect() disables handshaking, so normally
# dormant.
_SETTLE_QUIET = 0.35

# Pause after a good decode before disarming the scan session. Sending
# STOP_SESSION/SCAN_DISABLE the instant the code lands can race the imager
# finishing its decode-transmit handshake and trip the 4-beep TRANSMIT_ERROR;
# give it a beat to settle first.
_POST_DECODE_SETTLE = 1.0

# --- SSI opcodes ---
_HOST               = 0x04
OP_CMD_ACK          = 0xD0
OP_CMD_NAK          = 0xD1
OP_DECODE_DATA      = 0xF3
OP_EVENT            = 0xF6
OP_PARAM_SEND       = 0xC6
OP_PARAM_REQUEST    = 0xC7
OP_START_SESSION    = 0xE4
OP_STOP_SESSION     = 0xE5
OP_SCAN_ENABLE      = 0xE9
OP_SCAN_DISABLE     = 0xEA
OP_REQUEST_REVISION = 0xA3
OP_REPLY_REVISION   = 0xA4


# --- SSI parameter numbers (the device settings connect() applies) ---
P_BEEP_GOOD_DECODE  = 56     # 1 = beep on a good decode, 0 = silent
P_POWER_MODE        = 128    # 0 = Continuous (don't sleep between decodes)
P_TRIGGER_MODE      = 138    # 8 = Host (decode only when the host starts a session)
P_ACK_NAK           = 159    # 0 = disable ACK/NAK software handshaking
P_DECODE_PACKET_FMT = 238    # 1 = packeted decode data (so we can frame + ID it)


# SSI "Bar Code Type" byte (first byte of DECODE_DATA) -> symbology name.
# Source: Zebra SSI Programmer's Guide, Table 2-38. Same-symbology variants
# (Full ASCII, +2/+5 supplementals, concatenated forms) fold to one name.
CODE_TYPES = {
    0x01: "code39",
    0x02: "codabar",
    0x03: "code128",
    0x04: "discrete_2of5",        # D25
    0x05: "iata",
    0x06: "interleaved_2of5",     # ITF
    0x07: "code93",
    0x08: "upca",
    0x09: "upce",
    0x0A: "ean8",
    0x0B: "ean13",
    0x0C: "code11",
    0x0D: "code49",
    0x0E: "msi",
    0x0F: "gs1_128",
    0x10: "upce1",
    0x11: "pdf417",
    0x12: "code16k",
    0x13: "code39",               # Code 39 Full ASCII
    0x14: "upcd",
    0x15: "trioptic",
    0x16: "bookland",
    0x17: "coupon",
    0x18: "nw7",
    0x19: "isbt128",
    0x1A: "micropdf",
    0x1B: "datamatrix",
    0x1C: "qrcode",
    0x1D: "micropdf",             # Micro PDF CCA
    0x1E: "postnet",
    0x1F: "planet",
    0x20: "code32",
    0x21: "isbt128",              # ISBT-128 Concat
    0x22: "postal_japan",
    0x23: "postal_australia",
    0x24: "postal_dutch",
    0x25: "maxicode",
    0x26: "postbar_ca",
    0x27: "postal_uk",
    0x28: "macro_pdf417",
    0x29: "macro_qr",
    0x2C: "micro_qr",
    0x2D: "aztec",
    0x2E: "aztec_rune",
    0x2F: "french_lottery",
    0x30: "gs1_databar",          # GS1 DataBar-14
    0x31: "gs1_databar_limited",
    0x32: "gs1_databar_expanded",
    0x34: "us_4state",
    0x35: "us_4state_4",
    0x37: "scanlet_webcode",
    0x38: "cuecat",
    0x48: "upca",                 # UPCA + 2
    0x49: "upce",                 # UPCE + 2
    0x4A: "ean8",                 # EAN-8 + 2
    0x4B: "ean13",                # EAN-13 + 2
    0x50: "upce1",                # UPCE1 + 2
    0x5A: "tlc39",
    0x69: "signature",
    0x71: "matrix_2of5",
    0x72: "chinese_2of5",         # C 2 of 5
    0x73: "korean_3of5",
    0x88: "upca",                 # UPCA + 5
    0x89: "upce",                 # UPCE + 5
    0x8A: "ean8",                 # EAN-8 + 5
    0x8B: "ean13",                # EAN-13 + 5
    0x90: "upce1",                # UPCE1 + 5
    0x9A: "macro_micropdf",
    0xA0: "ocrb",
    0xB4: "gs1_databar_coupon",   # RSS (GS1 DataBar) Expanded Coupon
    0xB7: "hanxin",
    0xC1: "gs1_datamatrix",
    0xC2: "gs1_qr",
    0xE0: "rfid_raw",
    0xE1: "rfid_uri",
}
# CC-A / CC-B / CC-C composites (SSI IDs 0x51-0x68) all report as "composite".
CODE_TYPES.update({
    b: "composite" for b in (
        0x51, 0x52, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 0x59,
        0x61, 0x62, 0x63, 0x64, 0x65, 0x66, 0x67, 0x68,
    )
})

# Default ``allowed`` for detect() -> no restriction. Pass a subset to filter.
ALL_SYMBOLOGIES = tuple(sorted(set(CODE_TYPES.values())))


def _normalize(name: str) -> str:
    """Loose match key: lowercase, alphanumerics only — "Code 39", "code-39",
    "code39" all collapse to one key."""
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


@dataclass
class Scan:
    """Result of a scan.

    ``status``:
      - "ok"            a barcode was decoded; ``data`` holds it, ``symbology``
                        names its type (e.g. "code39")
      - "timeout"       nothing (allowed) scanned within the time limit
      - "disconnected"  the serial port vanished / link error
      - "nak"           the scanner rejected the request
    """

    status: str
    data: str
    symbology: str = ""

    @property
    def connected(self) -> bool:
        return self.status != "disconnected"

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def __str__(self) -> str:
        if self.status == "ok":
            return f"{self.symbology}: {self.data!r}"
        return self.status.upper()


class DS457:
    """Zebra/Symbol DS457 barcode scanner over SSI on a USB-CDC serial port.

    ``beep`` (set once here) controls whether the scanner beeps on a good read.
    """

    def __init__(self, port: str, baud: int = 9600, beep: bool = False):
        self.port = port
        self.baud = int(baud)
        self.beep = beep
        self.enabled = False
        self.handshake = True          # ACK/NAK; connect() turns it off
        self.ser: Optional[serial.Serial] = None

    # ==================================================
    # Connection
    # ==================================================

    def is_connected(self) -> bool:
        return self.ser is not None and self.ser.is_open

    def connect(self, enabled: bool = False) -> bool:
        """Open the port AND confirm the scanner answers over SSI.

        ``enabled=True`` leaves scanning armed (continuous use); the default
        leaves it off so each ``detect()`` arms it on demand. Returns False if
        the port won't open or the scanner doesn't respond."""
        self.enabled = enabled
        if self.is_connected():
            self.enable() if enabled else self.disable()
            return True
        try:
            # USB-CDC virtual serial port; no host flow control.
            self.ser = serial.Serial(self.port, self.baud, timeout=_SSI_TIMEOUT,
                                     rtscts=False, dsrdtr=False)
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
        # --- device settings: the important ones all live here ---
        # Host trigger so the scanner decodes only when we START_SESSION (one
        # code per detect()) instead of free-running and re-reading a label left
        # in view. Continuous power keeps it awake so it never sleeps mid-session.
        # Packeted decode data lets us frame it and read the symbology. ACK/NAK
        # handshaking off keeps the link simple (see _disable_handshaking).
        self._set_param(P_TRIGGER_MODE, 8)
        self._set_param(P_POWER_MODE, 0)
        self._set_param(P_DECODE_PACKET_FMT, 1)
        self._set_param(P_BEEP_GOOD_DECODE, 1 if self.beep else 0)
        self._disable_handshaking()
        time.sleep(0.1)
        self._resync()
        self.enable() if enabled else self.disable()
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

    def enable(self) -> bool:
        """Arm the scanner so it will decode. ``detect()`` calls this for you;
        use it directly only for continuous scanning."""
        return self._command(OP_SCAN_ENABLE)

    def disable(self) -> None:
        """Turn scanning OFF. Fire-and-forget; ``enable()`` / ``detect()``
        re-arm it."""
        self._write(OP_STOP_SESSION)
        self._write(OP_SCAN_DISABLE)

    def detect(self, allowed: Iterable[str] = ALL_SYMBOLOGIES,
               timeout: float = 10.0) -> Scan:
        """Capture one barcode now, optionally restricted to ``allowed`` types.

        ``allowed`` defaults to ``ALL_SYMBOLOGIES`` (no restriction). Pass a
        subset, e.g. ``["code39", "qrcode"]``, to filter: codes of other types
        are ignored and scanning continues until an allowed one appears or
        ``timeout`` elapses. Names match loosely; an unknown name raises
        ``ValueError``. Restores the post-connect arm state when done."""
        if not self.is_connected():
            return Scan("disconnected", "")
        allow = self._resolve_allowed(allowed)
        self.ser.reset_input_buffer()
        self.enable()
        result = self._capture(timeout, allow)
        if not self.enabled:
            if result.ok:
                time.sleep(_POST_DECODE_SETTLE)   # settle before disarming
            self.disable()                   # leave it as connect() left it
        return result

    def _resolve_allowed(self, allowed: Iterable[str]) -> Optional[set]:
        """Turn the caller's ``allowed`` list into the filter ``_capture`` uses.

        Normalizes every name to its loose-match key and validates it (an
        unknown name is a typo -> ``ValueError``). Returns that set of keys, or
        ``None`` when the list covers everything (the default) so ``_capture``
        can skip filtering entirely."""
        want = {_normalize(a) for a in allowed}
        full = {_normalize(s) for s in ALL_SYMBOLOGIES}
        unknown = want - full
        if unknown:
            raise ValueError(
                f"unknown symbology name(s): {sorted(unknown)}. "
                f"valid names: {', '.join(ALL_SYMBOLOGIES)}"
            )
        return None if want >= full else want

    def _capture(self, timeout: float, allow: Optional[set]) -> Scan:
        """Run one host-triggered scan session and return its ``Scan``.

        Starts a session and reads SSI packets until ``timeout``: on decode
        data it ACKs, resolves the symbology from the type byte, and returns it
        if allowed (``allow is None`` = accept anything) — otherwise it re-arms
        and keeps waiting. Returns ``nak``/``disconnected``/``timeout`` if the
        scanner rejects, the link drops, or nothing allowed shows up."""
        if not self._write(OP_START_SESSION):
            return Scan("disconnected", "")
        deadline = time.time() + timeout
        result: Optional[Scan] = None
        acked = False                                         # ACKed any data/event?
        while time.time() < deadline:
            pkt = self._read_packet(deadline - time.time())   # full remaining time
            if not self.is_connected():
                return Scan("disconnected", "")
            if pkt is None:
                continue
            opcode, _status, data = pkt
            if opcode == OP_DECODE_DATA:
                self._ack()                                   # no-op unless handshaking on
                acked = True
                symbology = CODE_TYPES.get(data[0], "unknown") if data else "unknown"
                text = data[1:].decode("ascii", errors="ignore")
                if allow is None or _normalize(symbology) in allow:
                    result = Scan("ok", text, symbology)
                    break
                self._write(OP_START_SESSION)                 # filtered out — keep looking
                continue
            if opcode == OP_CMD_NAK:
                result = Scan("nak", "")
                break
            if opcode == OP_EVENT:
                self._ack()
                acked = True

        if not self.is_connected():
            return Scan("disconnected", "")
        if acked and self.handshake:
            self._settle_decode()        # ACK retransmits so no transmit-error beep
        else:
            self._resync()
        return result or Scan("timeout", "")

    def _settle_decode(self) -> None:
        """ACK/NAK fallback: drain and re-ACK the imager's retransmits.

        Only runs when handshaking couldn't be disabled. With ACK/NAK on, the
        imager retransmits its decode data if it thinks our ACK was lost and
        declares a transmit error if those go unACKed; flushing right after a
        decode would drop them. Keep reading and re-ACK until the link is quiet
        for longer than the retransmit gap."""
        while True:
            pkt = self._read_packet(_SETTLE_QUIET)
            if pkt is None:                  # quiet past the retry gap — settled
                return
            if pkt[0] in (OP_DECODE_DATA, OP_EVENT):
                self._ack()

    # ==================================================
    # SSI packet layer (internal)
    # ==================================================

    @staticmethod
    def _checksum(body: bytes) -> bytes:
        cks = (0x10000 - (sum(body) & 0xFFFF)) & 0xFFFF
        return bytes([(cks >> 8) & 0xFF, cks & 0xFF])

    def _frame(self, opcode: int, data: bytes = b"", status: int = 0x00) -> bytes:
        body = bytes([4 + len(data), opcode, _HOST, status]) + data
        return body + self._checksum(body)

    def _write(self, opcode: int, data: bytes = b"", status: int = 0x00) -> bool:
        if self.ser is None:
            return False
        try:
            self.ser.write(self._frame(opcode, data, status))
            self.ser.flush()        # push to the wire now, as one frame
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
        if self.handshake:               # off -> imager neither expects nor wants it
            self._write(OP_CMD_ACK)

    def _command(self, opcode: int, data: bytes = b"") -> bool:
        """Send a host command. With ACK/NAK handshaking on, wait for the
        decoder's CMD_ACK; with it off the decoder doesn't ACK, so just send."""
        if not self._write(opcode, data):
            return False
        if not self.handshake:
            time.sleep(0.02)             # let the decoder process it
            return True
        pkt = self._read_packet(_SSI_TIMEOUT)
        return pkt is not None and pkt[0] == OP_CMD_ACK

    def _set_param(self, param: int, value: int) -> bool:
        """Set a single-byte parameter (temporary). Internal — used by connect()."""
        return self._command(OP_PARAM_SEND, bytes([0xFF, param & 0xFF, value & 0xFF]))

    def read_params(self, *params: int) -> dict:
        """Query current device values of single-byte parameters → {param: value}.
        Works regardless of handshaking (a PARAM_REQUEST gets a PARAM_SEND reply,
        not an ACK). For verifying a setting actually took. e.g.
        ``scanner.read_params(159, 155, 56)``."""
        if not self.is_connected():
            return {}
        self._resync()
        if not self._write(OP_PARAM_REQUEST, bytes(p & 0xFF for p in params)):
            return {}
        for _ in range(4):                       # skip any stray packets (e.g. ACKs)
            pkt = self._read_packet(_SSI_TIMEOUT)
            if pkt is None:
                return {}
            if pkt[0] == OP_PARAM_SEND:
                data = pkt[2]                    # [beep_code, param, val, param, val, ...]
                out, i = {}, 1                   # skip the leading beep-code byte
                while i + 1 < len(data):
                    out[data[i]] = data[i + 1]
                    i += 2
                return out
        return {}

    def _disable_handshaking(self) -> None:
        """Disable SSI ACK/NAK handshaking (param 159) and confirm it stuck.

        With ACK/NAK off the imager neither expects nor waits for a host CMD_ACK
        after a decode, which keeps the link simple over USB-CDC. Try a temporary
        write then a permanent one, reading 159 back each time. Set self.handshake
        to what the device actually reports: if it refuses to disable we keep
        ACKing (handshake stays True) rather than go silent while it still expects
        them."""
        for status in (0x00, 0x08):              # temporary change, then permanent
            self._write(OP_PARAM_SEND, bytes([0xFF, P_ACK_NAK, 0]), status)
            time.sleep(0.1)
            self._resync()
            if self.read_params(P_ACK_NAK).get(P_ACK_NAK) == 0:
                self.handshake = False
                return
        self.handshake = True                    # couldn't disable — keep ACKing
