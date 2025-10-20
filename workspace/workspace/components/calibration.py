# workspace/components/calibration.py

import json
from pathlib import Path
import numpy as np

class Calibration:
    def __init__(self, core):

        self.core = core
        self.name = core.name
        self.file_path = Path.cwd() / f"{self.name}.json"

        if self.file_path.exists():
            # Load existing calibration file
            with open(self.file_path, "r") as f:
                self.calibration_data = json.load(f)
        else:
            # File does not exist — just start empty, no file creation
            self.calibration_data = {}

    def add_point(self, name, raw_values, corrected_values, threshold=1e-3):
        """
        Adds or updates a calibration point for a given name.
        Replaces an existing point if raw_values are close.
        """
        # Ensure sub-dictionary exists
        if name not in self.calibration_data:
            self.calibration_data[name] = []

        # Convert to numpy for distance check
        raw_arr = np.array(raw_values, dtype=float)

        # Look for existing close point
        updated = False
        for i, entry in enumerate(self.calibration_data[name]):
            if np.linalg.norm(np.array(entry["raw"]) - raw_arr) < threshold:
                self.calibration_data[name][i] = {"raw": raw_values, "corrected": corrected_values}
                updated = True
                break

        # Add new point if none found
        if not updated:
            self.calibration_data[name].append({"raw": raw_values, "corrected": corrected_values})
            self._save()

    def clear_point(self, name, raw_values, threshold=1e-3):
        """Removes one point (if close) from a given name."""
        if name not in self.calibration_data:
            return
        raw_arr = np.array(raw_values, dtype=float)
        self.calibration_data[name] = [
            p for p in self.calibration_data[name]
            if np.linalg.norm(np.array(p["raw"]) - raw_arr) >= threshold
        ]
        self._save()

    def clear_name(self, name):
        """Removes all calibration points under a given name."""
        if name in self.calibration_data:
            del self.calibration_data[name]
            self._save()

    def clear_all(self):
        """Clears all calibration data."""
        self.calibration_data = {}
        self._save()

    def _save(self):
        """Writes the whole calibration_data to file."""
        with open(self.file_path, "w") as f:
            json.dump(self.calibration_data, f, indent=2, separators=(',', ': '))

    def interpolate_corrected(self, name, raw_values, threshold=1e-3):
        """
        Inverse-distance interpolation of correction for a given name.
        Interpolate errors first, then add to raw_values.
        """
        pts = self.calibration_data.get(name, [])
        if not pts:
            return list(float(x) for x in raw_values)  # no data → no correction

        q = np.array(raw_values, dtype=float)

        # Build arrays of raw points and their error vectors (corrected - raw)
        raw_mat = np.array([p["raw"] for p in pts], dtype=float)
        err_mat = np.array([np.array(p["corrected"], dtype=float) - np.array(p["raw"], dtype=float) for p in pts])

        # Distances to query
        d = np.linalg.norm(raw_mat - q, axis=1)

        # If an exact/near match exists, return that corrected directly
        i_min = np.argmin(d)
        if d[i_min] < threshold:
            return list(float(x) for x in np.array(pts[i_min]["corrected"]))

        # Inverse-distance weights
        w = 1.0 / (np.power(d, 2))
        w /= w.sum()

        # Interpolate error, then add to raw
        interp_err = (w[:, None] * err_mat).sum(axis=0)
        corrected = q + interp_err
        return list(float(x) for x in corrected)
 
    def record_point(self, name, raw_values, msg, threshold=1e-3):
        """
        Show msg, wait for Enter, read corrected joints from core's API,
        and record (raw -> corrected) under a name inferred from the core.
        """
        print(msg)
        input("Press Enter to record...")

        # Read current joints from core API
        corrected_values = self.core.robot_api.joint()

        self.add_point(name, raw_values, corrected_values, threshold=threshold)
        print(f"Recorded calibration point for '{name}':")
        print(f"  Raw:       {raw_values}")
        print(f"  Corrected: {corrected_values}")
