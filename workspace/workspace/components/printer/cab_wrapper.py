from __future__ import annotations

import socket
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional, Tuple

ESC = b"\x1b"


class CodeType(str, Enum):
    CODE128 = "code128"
    QRCODE = "qrcode"
    DATAMATRIX = "datamatrix"


@dataclass
class PrintResult:
    ok: bool
    message: str
    job_id: str = ""
    verified: bool = False
    verify_method: Optional[str] = None
    escs: Optional[str] = None
    esca: Optional[str] = None
    escj: Optional[str] = None
    job_preview: Optional[str] = None
    exception: Optional[str] = None


@dataclass(frozen=True)
class LabelSpec:
    ptype: str = "l1"
    xo: float = 0.0
    yo: float = 0.0
    length_in: float = 1.00
    gap_in: float = 0.12
    width_in: float = 1.50

    @property
    def pitch_in(self) -> float:
        return self.length_in + self.gap_in


@dataclass(frozen=True)
class PrintSettings:
    units: str = "i"
    include_zO: bool = True
    speed: float = 3.937
    heat_offset: int = 0
    transfer_type: str = "T"
    ribbon_saver: str = "R0"
    backfeed: str = "B0"
    include_OP: bool = True
    peeloff_P: bool = True


@dataclass(frozen=True)
class ElementSpec:
    field_name: str
    x: float
    y: float
    rotation: int
    js_symbology: str
    js_params: Tuple[float, ...]


class Cab:
    def __init__(
        self,
        ip: str = "192.168.100.12",
        port: int = 9100,
        timeout_s: float = 5.0,
        label: Optional[LabelSpec] = None,
        settings: Optional[PrintSettings] = None,
        autorun_delay_s: float = 0.10,
        poll_s: float = 0.6,
        ready_timeout_s: float = 30.0,
        print_timeout_s: float = 60.0,
        dryrun_cycle_s: float = 1.5,  # tune if dryrun count still collapses to 1
    ):
        self.ip = ip
        self.port = int(port)
        self.timeout_s = float(timeout_s)

        self.label = label or LabelSpec()
        self.settings = settings or PrintSettings()

        self.autorun_delay_s = float(autorun_delay_s)
        self.poll_s = max(0.5, float(poll_s))
        self.ready_timeout_s = float(ready_timeout_s)
        self.print_timeout_s = float(print_timeout_s)

        self.dryrun_cycle_s = float(dryrun_cycle_s)

        self.last_result: Optional[PrintResult] = None

        self._elements: Dict[CodeType, ElementSpec] = {
            CodeType.CODE128: ElementSpec("Barcode2", 0.1565, 0.0842, 0, "CODE128", (0.8958, 0.0148)),
            CodeType.QRCODE: ElementSpec("Barcode1", 0.3217, 0.1076, 0, "qrcode+ELL+MODEL2", (0.0394,)),
            CodeType.DATAMATRIX: ElementSpec("Barcode1", 0.4461, 0.2796, 0, "datamatrix", (0.0558,)),
        }


    # -------------------------
    # Configuration methods
    # -------------------------
    def set_label(self, *, width_in: float, length_in: float, gap_in: float, ptype: str = "l1") -> None:
        self.label = LabelSpec(
            ptype=str(ptype),
            xo=self.label.xo,
            yo=self.label.yo,
            length_in=float(length_in),
            gap_in=float(gap_in),
            width_in=float(width_in),
        )

    def set_element(self, code_type: CodeType, element: ElementSpec) -> None:
        self._elements[code_type] = element

    # -------------------------
    # Raw send/query helpers
    # -------------------------
    def _send_raw(self, payload: bytes) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(self.timeout_s)
            s.connect((self.ip, self.port))
            s.sendall(payload)

    def _query_raw(self, payload: bytes, read_timeout_s: float = 2.0) -> Optional[str]:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(self.timeout_s)
                s.connect((self.ip, self.port))
                s.sendall(payload)
                s.settimeout(float(read_timeout_s))
                data = s.recv(256)
            if not data:
                return None
            return data.decode("ascii", errors="ignore").strip()
        except Exception:
            return None

    # -------------------------
    # Atomic comm checks
    # -------------------------
    def is_reachable(self) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(self.timeout_s)
                s.connect((self.ip, self.port))
            self.last_result = PrintResult(True, "is_reachable=True (TCP connect OK)")
            return True
        except Exception as e:
            self.last_result = PrintResult(False, "is_reachable=False (TCP connect failed)", exception=repr(e))
            return False

    def get_escs(self) -> Optional[str]:
        return self._query_raw(ESC + b"s")

    def get_esca(self) -> Optional[str]:
        return self._query_raw(ESC + b"a")

    def get_escj(self) -> Optional[str]:
        return self._query_raw(ESC + b"j")

    def can_query(self) -> bool:
        r = self.get_escs()
        if r is None:
            self.last_result = PrintResult(False, "can_query=False (no ESCs reply)")
            return False
        self.last_result = PrintResult(True, f"can_query=True (ESCs='{r}')", escs=r)
        return True

    # -------------------------
    # Parsing helpers
    # -------------------------
    def _escs_active(self, escs: Optional[str]) -> Optional[bool]:
        if not escs:
            return None
        z = escs[-1]
        if z == "Y":
            return True
        if z == "N":
            return False
        return None

    def _escs_online(self, escs: Optional[str]) -> Optional[bool]:
        if not escs:
            return None
        x = escs[0]
        if x == "Y":
            return True
        if x == "N":
            return False
        return None

    def _esca_idle(self, esca: Optional[str]) -> Optional[bool]:
        if not esca:
            return None
        return esca.startswith("I")

    # -------------------------
    # Waiting / gating
    # -------------------------
    def wait_ready(self, timeout_s: Optional[float] = None) -> bool:
        tout = self.ready_timeout_s if timeout_s is None else float(timeout_s)
        t0 = time.time()
        last_s = None
        last_a = None

        while time.time() - t0 < tout:
            last_s = self.get_escs()
            time.sleep(self.poll_s)
            last_a = self.get_esca()

            online = self._escs_online(last_s)
            active = self._escs_active(last_s)
            idle = self._esca_idle(last_a)

            if online is True and active is False and idle is True:
                self.last_result = PrintResult(
                    True,
                    "wait_ready=True (online, inactive, idle)",
                    verified=True,
                    verify_method="ESCs+ESCa",
                    escs=last_s,
                    esca=last_a,
                )
                return True

            time.sleep(self.poll_s)

        self.last_result = PrintResult(False, "wait_ready=False (timeout)", escs=last_s, esca=last_a)
        return False

    def wait_print_done(self, job_id: str, timeout_s: Optional[float] = None) -> bool:
        tout = self.print_timeout_s if timeout_s is None else float(timeout_s)
        t0 = time.time()

        saw_active = False
        last_s = None
        last_a = None
        last_j = None

        while time.time() - t0 < tout:
            last_s = self.get_escs()
            active = self._escs_active(last_s)
            if active is True:
                saw_active = True

            time.sleep(self.poll_s)

            last_a = self.get_esca()
            time.sleep(self.poll_s)

            last_j = self.get_escj()
            last_j_clean = last_j.strip() if last_j else None

            if saw_active and active is False and last_j_clean == job_id:
                self.last_result = PrintResult(
                    True,
                    "wait_print_done=True (ESCs Y→N and ESCj match)",
                    job_id=job_id,
                    verified=True,
                    verify_method="ESCs+ESCj",
                    escs=last_s,
                    esca=last_a,
                    escj=last_j_clean,
                )
                return True

        self.last_result = PrintResult(
            False,
            "wait_print_done=False (timeout/no confirmation)",
            job_id=job_id,
            verified=False,
            escs=last_s,
            esca=last_a,
            escj=(last_j.strip() if last_j else None),
        )
        return False

    # -------------------------
    # JScript build
    # -------------------------
    def _render_job(self, job_id: str, element: ElementSpec, data: str) -> str:
        ls = self.label
        ps = self.settings

        lines = []
        lines.append(f"m {ps.units}")
        if ps.include_zO:
            lines.append("zO")
        lines.append("J")
        lines.append(f"j {job_id}")
        lines.append(f"S {ls.ptype};{ls.xo},{ls.yo},{ls.length_in},{ls.pitch_in},{ls.width_in}")
        lines.append(f"H {ps.speed},{ps.heat_offset},{ps.transfer_type},{ps.ribbon_saver},{ps.backfeed}")
        if ps.include_OP:
            lines.append("O P")
        if ps.peeloff_P:
            lines.append("P")

        params = ",".join(str(p) for p in element.js_params)
        if params:
            b_line = f"B:{element.field_name};{element.x},{element.y},{element.rotation},{element.js_symbology},{params};{data}"
        else:
            b_line = f"B:{element.field_name};{element.x},{element.y},{element.rotation},{element.js_symbology};{data}"
        lines.append(b_line)

        lines.append("A 1")
        return "\r\n".join(lines) + "\r\n"

    # -------------------------
    # Atomic actions
    # -------------------------
    def dry_run_spin(self, count: int = 1) -> bool:
        count = max(1, int(count))

        if not self.wait_ready():
            return False

        for i in range(count):
            try:
                self._send_raw(ESC + b"b")
            except Exception as e:
                self.last_result = PrintResult(False, "dry_run_spin=False (ESCb send failed)", exception=repr(e))
                return False

            if i < count:
                time.sleep(self.dryrun_cycle_s)

        if not self.wait_ready():
            return False

        self.last_result = PrintResult(
            True,
            f"dry_run_spin=True x{count} (dryrun_cycle_s={self.dryrun_cycle_s:g})",
            verified=True,
            verify_method="TIMED+ESCs/ESCa-ready",
            escs=self.get_escs(),
            esca=self.get_esca(),
        )
        return True

    def print_one(self, code_type: CodeType, data: str, *, autorun: bool = True, verify: bool = True) -> bool:
        element = self._elements.get(code_type)
        if element is None:
            self.last_result = PrintResult(False, f"print_one=False (unknown CodeType {code_type})")
            return False

        if not self.wait_ready():
            return False

        job_id = uuid.uuid4().hex[:12]
        job_text = self._render_job(job_id, element, data)

        try:
            self._send_raw(job_text.encode("ascii", errors="strict"))
        except Exception as e:
            self.last_result = PrintResult(False, "print_one=False (job send failed)", job_id=job_id, job_preview=job_text, exception=repr(e))
            return False

        if autorun:
            time.sleep(self.autorun_delay_s)
            try:
                self._send_raw(ESC + b"g")
            except Exception as e:
                self.last_result = PrintResult(False, "print_one=False (ESCg failed)", job_id=job_id, job_preview=job_text, exception=repr(e))
                return False

        if not verify:
            self.last_result = PrintResult(True, "print_one=True (sent, verify=False)", job_id=job_id, verified=False, job_preview=job_text)
            return True

        ok = self.wait_print_done(job_id)

        if self.last_result is not None:
            self.last_result.job_preview = job_text

        return ok

    def print_qr(self, data: str, *, autorun: bool = True, verify: bool = True) -> bool:
        return self.print_one(CodeType.QRCODE, data, autorun=autorun, verify=verify)

    def print_code128(self, data: str, *, autorun: bool = True, verify: bool = True) -> bool:
        return self.print_one(CodeType.CODE128, data, autorun=autorun, verify=verify)

    def print_datamatrix(self, data: str, *, autorun: bool = True, verify: bool = True) -> bool:
        return self.print_one(CodeType.DATAMATRIX, data, autorun=autorun, verify=verify)

### Example usage
if __name__ == "__main__":
    p = Cab(ip="192.168.254.128", dryrun_cycle_s=1.5)
    p.set_label(width_in=1.50, length_in=1.00, gap_in=0.12, ptype="l1")

    print("1) real print")
    #print(p.print_qr("TUBE_000123", autorun=True, verify=True), p.last_result)

    # dry run
    #faking the print
    p.dry_run_spin(count=1)