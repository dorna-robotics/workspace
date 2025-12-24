# workspace/components/calibration.py

import json
from pathlib import Path
import numpy as np


class Calibration:
    def __init__(self, name: str, axis_mask):
        """
        name: calibration file name will be <name>.json in Path.cwd()
        axis_mask: length-8 iterable of 0/1. 1 => axis participates, 0 => ignored.
        """
        self.name = str(name)
        self.file_path = Path.cwd() / f"{self.name}.json"

        mask = np.array(list(axis_mask), dtype=int).reshape(-1)
        if mask.size != 8:
            raise ValueError(f"axis_mask must be length 8, got {mask.size}")
        if not np.all((mask == 0) | (mask == 1)):
            raise ValueError("axis_mask must contain only 0/1 values")

        self._active = mask.astype(bool)

        if self.file_path.exists():
            with open(self.file_path, "r") as f:
                self.calibration_data = json.load(f)
        else:
            self.calibration_data = []

    def _mask_for_storage(self, values):
        """Force inactive axes to 0 for storing and masked-space computations."""
        v = np.array(values, dtype=float)
        if v.size != 8:
            raise ValueError(f"values must be length 8, got {v.size}")
        v[~self._active] = 0.0
        return v

    def add_point(self, raw_values, corrected_values, threshold=1e-3):
        """
        Adds or updates a calibration point.
        Replaces an existing point if raw_values are close (active axes only).
        Inactive axes are stored as 0 in both raw and corrected.
        """
        raw_arr = self._mask_for_storage(raw_values)
        corr_arr = self._mask_for_storage(corrected_values)

        updated = False
        for i, entry in enumerate(self.calibration_data):
            entry_raw = np.array(entry["raw"], dtype=float)
            # compare only on active axes
            if np.linalg.norm(entry_raw[self._active] - raw_arr[self._active]) < threshold:
                self.calibration_data[i] = {
                    "raw": raw_arr.tolist(),
                    "corrected": corr_arr.tolist(),
                }
                updated = True
                break

        if not updated:
            self.calibration_data.append({
                "raw": raw_arr.tolist(),
                "corrected": corr_arr.tolist(),
            })

        self._save()

    def clear_point(self, raw_values, threshold=1e-3):
        """Removes one point (if close) using active axes only."""
        if not self.calibration_data:
            return

        raw_arr = self._mask_for_storage(raw_values)

        kept = []
        for p in self.calibration_data:
            p_raw = np.array(p["raw"], dtype=float)
            if np.linalg.norm(p_raw[self._active] - raw_arr[self._active]) >= threshold:
                kept.append(p)

        self.calibration_data = kept
        self._save()

    def clear_all(self):
        self.calibration_data = []
        self._save()

    def _save(self):
        with open(self.file_path, "w") as f:
            json.dump(self.calibration_data, f, indent=2, separators=(",", ": "))

    def interpolate(self, raw_values, threshold=1e-3, power=2.0):
        """
        Inverse-distance interpolation on active axes only.
        - Inactive axes in the output are returned exactly equal to raw_values.
        """
        raw_in = np.array(raw_values, dtype=float)
        if raw_in.size != 8:
            raise ValueError(f"raw_values must be length 8, got {raw_in.size}")

        if not self.calibration_data:
            return [float(x) for x in raw_in]

        # Query in masked space (inactive set to 0 for distance/interp)
        q = raw_in.copy()
        q[~self._active] = 0.0

        raw_mat = np.array([p["raw"] for p in self.calibration_data], dtype=float)        # already masked
        corr_mat = np.array([p["corrected"] for p in self.calibration_data], dtype=float) # already masked
        err_mat = corr_mat - raw_mat

        # distances on active dims only
        d = np.linalg.norm(raw_mat[:, self._active] - q[self._active], axis=1)

        i_min = int(np.argmin(d))
        if d[i_min] < threshold:
            out = corr_mat[i_min].copy()
        else:
            w = 1.0 / np.power(d, power)
            w_sum = float(w.sum())
            if w_sum <= 0.0 or not np.isfinite(w_sum):
                out = q.copy()
            else:
                w /= w_sum
                interp_err = (w[:, None] * err_mat).sum(axis=0)
                out = q + interp_err

        # Inactive axes must equal the original raw input
        out[~self._active] = raw_in[~self._active]
        return [float(x) for x in out]