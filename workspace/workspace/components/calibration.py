import json
from pathlib import Path
import numpy as np
import dorna2.pose as pose


class Calibration:
    def __init__(self, name: str):
        """
        name: calibration file name will be <name>.json in Path.cwd()

        calibration_data format:
          {
            "recipe_name": [ {"raw":[...],"corrected":[...]}, ... ]
          }

        Pose format (len=6):
          [x, y, z, a, b, c] where [a,b,c] is rotation vector in degrees (axis * angle_deg)
        """
        self.name = str(name)
        self.file_path = Path.cwd() / f"{self.name}.json"

        if self.file_path.exists():
            with open(self.file_path, "r") as f:
                self.calibration_data = json.load(f)
        else:
            self.calibration_data = {}

    def _as_pose6(self, values, name="values"):
        v = np.array(values, dtype=float)
        if v.size != 6:
            raise ValueError(f"{name} must be length 6, got {v.size}")
        return v

    def _save(self):
        with open(self.file_path, "w") as f:
            json.dump(self.calibration_data, f, indent=2, separators=(",", ": "))

    def add_point(self, raw_values, corrected_values, threshold=1e-3, dict_name="default"):
        """
        Adds or updates a calibration point in calibration_data[dict_name].
        Replaces an existing point if raw_values are close (6D Euclidean norm).
        """
        dict_name = str(dict_name)

        raw_arr = self._as_pose6(raw_values, "raw_values")
        corr_arr = self._as_pose6(corrected_values, "corrected_values")

        if dict_name not in self.calibration_data:
            self.calibration_data[dict_name] = []

        updated = False
        for i, entry in enumerate(self.calibration_data[dict_name]):
            entry_raw = np.array(entry["raw"], dtype=float)
            if np.linalg.norm(entry_raw - raw_arr) < threshold:
                self.calibration_data[dict_name][i] = {
                    "raw": raw_arr.tolist(),
                    "corrected": corr_arr.tolist(),
                }
                updated = True
                break

        if not updated:
            self.calibration_data[dict_name].append({
                "raw": raw_arr.tolist(),
                "corrected": corr_arr.tolist(),
            })

        self._save()

    def clear_point(self, raw_values, threshold=1e-3, dict_name="default"):
        """Removes one point (if close) in calibration_data[dict_name] using 6D Euclidean norm."""
        dict_name = str(dict_name)

        if dict_name not in self.calibration_data or not self.calibration_data[dict_name]:
            return

        raw_arr = self._as_pose6(raw_values, "raw_values")

        kept = []
        for p in self.calibration_data[dict_name]:
            p_raw = np.array(p["raw"], dtype=float)
            if np.linalg.norm(p_raw - raw_arr) >= threshold:
                kept.append(p)

        self.calibration_data[dict_name] = kept
        self._save()

    def clear_all(self, dict_name=None):
        """
        If dict_name is provided, clears only that dict.
        If dict_name is None, clears all dictionaries.
        """
        if dict_name is None:
            self.calibration_data = {}
        else:
            dict_name = str(dict_name)
            self.calibration_data[dict_name] = []
        self._save()

    # -------------------- pose helpers (quaternion) --------------------

    def _abc_to_quat(self, abc_deg):
        R = pose.abc_to_rmat(abc_deg)
        return pose.rmat_to_quat(R)

    def _quat_to_abc(self, quat):
        R = pose.quat_to_rmat(quat)
        return pose.rmat_to_abc(R)

    def _quat_conj(self, q):
        q = np.array(q, dtype=float)
        return [-float(q[0]), -float(q[1]), -float(q[2]), float(q[3])]

    def _quat_normalize(self, q):
        q = np.array(q, dtype=float)
        n = float(np.linalg.norm(q))
        if n < 1e-12:
            return [0.0, 0.0, 0.0, 1.0]
        q = q / n
        return [float(q[0]), float(q[1]), float(q[2]), float(q[3])]

    def _slerp_shortest(self, q1, q2, t):
        # ensure shortest arc (q and -q represent same rotation)
        if pose.quat_dot(q1, q2) < 0.0:
            q2 = [-q2[0], -q2[1], -q2[2], -q2[3]]
        return pose.quat_slerp(q1, q2, t)

    def _blend_quats_weighted(self, quats, weights):
        """
        Weighted blend on unit quaternions using repeated SLERP:
          q = slerp(q, qi, wi/(w_sum+wi))
        """
        idx = int(np.argmax(weights))
        q = self._quat_normalize(quats[idx])
        w_sum = float(weights[idx])

        for i, wi in enumerate(weights):
            wi = float(wi)
            if i == idx or wi <= 0.0:
                continue
            alpha = wi / (w_sum + wi)
            q = self._slerp_shortest(q, self._quat_normalize(quats[i]), alpha)
            q = self._quat_normalize(q)
            w_sum += wi

        return q

    # -------------------- interpolation --------------------

    def interpolate(self, raw_values, threshold=1e-3, power=2.0, dict_name="default"):
        """
        Inverse-distance interpolation in XYZ, plus quaternion/SLERP for orientation.

        Input/Output pose: [x,y,z,a,b,c] where [a,b,c] is rotation vector in degrees.

        Method:
          - weights based on distance in XYZ between query raw pose and stored raw poses
          - position correction uses weighted average of (corr_xyz - raw_xyz)
          - orientation correction uses weighted blend of delta quaternions:
                dq_i = q_corr_i * inv(q_raw_i)
            then apply:
                q_out = dq_blend * q_query
        """
        dict_name = str(dict_name)

        raw_in = self._as_pose6(raw_values, "raw_values")

        if dict_name not in self.calibration_data or not self.calibration_data[dict_name]:
            return [float(x) for x in raw_in]

        raw_mat = np.array([p["raw"] for p in self.calibration_data[dict_name]], dtype=float)
        corr_mat = np.array([p["corrected"] for p in self.calibration_data[dict_name]], dtype=float)

        # distances in XYZ
        q_xyz = raw_in[:3]
        d = np.linalg.norm(raw_mat[:, :3] - q_xyz[None, :], axis=1)

        i_min = int(np.argmin(d))
        if d[i_min] < threshold:
            out = corr_mat[i_min].copy()
            return [float(x) for x in out]

        w = 1.0 / np.power(d, power)
        w_sum = float(w.sum())
        if w_sum <= 0.0 or not np.isfinite(w_sum):
            return [float(x) for x in raw_in]

        w = w / w_sum

        # --- position: apply weighted correction in xyz ---
        err_xyz = corr_mat[:, :3] - raw_mat[:, :3]
        interp_err_xyz = (w[:, None] * err_xyz).sum(axis=0)
        out_xyz = q_xyz + interp_err_xyz

        # --- orientation: blend delta quaternions with SLERP ---
        q_query = self._abc_to_quat(raw_in[3:6].tolist())
        q_query = self._quat_normalize(q_query)

        deltas = []
        for i in range(raw_mat.shape[0]):
            q_raw_i = self._abc_to_quat(raw_mat[i, 3:6].tolist())
            q_corr_i = self._abc_to_quat(corr_mat[i, 3:6].tolist())
            q_raw_i = self._quat_normalize(q_raw_i)
            q_corr_i = self._quat_normalize(q_corr_i)

            # dq = q_corr * inv(q_raw) ; for unit quat, inv = conj
            dq = pose.quat_mul(q_corr_i, self._quat_conj(q_raw_i))
            dq = self._quat_normalize(dq)
            deltas.append(dq)

        dq_blend = self._blend_quats_weighted(deltas, w.tolist())
        q_out = pose.quat_mul(dq_blend, q_query)
        q_out = self._quat_normalize(q_out)

        out_abc = self._quat_to_abc(q_out)

        out = np.array([out_xyz[0], out_xyz[1], out_xyz[2], out_abc[0], out_abc[1], out_abc[2]], dtype=float)

        #return [float(x) for x in out]
        return [float(x) for x in out[0:3]]+raw_values[3:6]
