import numpy as np
import math

# Robot geometry (from test.cpp dorna_ta_length)
DORNA_TA = {
    'a': [0.0, 80.0, 210.0, 0.0, 0.0, 0.0, 0.0],
    'd': [230.018, 0.0, 0.0, 41.80, 175.0, -89.0, 35.0],
    'alpha': [0.0, 2*math.pi, 0.0, 2*math.pi, 2*math.pi, 2*math.pi, 0.0],
    'delta': [0.0, 0.0, 0.0, 2*math.pi, math.pi, math.pi, 0.0],
    'limit_n': [-185.0, -150.0, -160.0, -175.0, -185.0, -180.0],
    'limit_p': [175.0, 210.0, 200.0, 185.0, 175.0, 180.0]
}


def solve_cs_equation(aa, bb, cc, i):
    """Python translation of solve_cs_equation from C++.
    Solves aa + bb*cos(theta) + cc*sin(theta) = 0 for cos(theta) and sin(theta).
    Returns (c1, s1) or raises ValueError if no solution.
    i selects branch (0 or 1) matching original behavior.
    """
    # replicate behavior
    delta = cc * cc * (-aa * aa + bb * bb + cc * cc)
    if delta < 0:
        raise ValueError('no solution: negative delta')
    if bb == 0.0 and cc == 0.0:
        raise ValueError('no solution: bb and cc zero')
    if bb == 0.0:
        s1 = -aa / cc
        if abs(s1) > 1.0:
            raise ValueError('no solution: |s1|>1')
        c1 = math.sqrt(max(0.0, 1.0 - s1 * s1))
        if i == 1:
            c1 = -c1
        return (c1, s1)
    if cc == 0.0:
        c1 = -aa / bb
        if abs(c1) > 1.0:
            raise ValueError('no solution: |c1|>1')
        s1 = math.sqrt(max(0.0, 1.0 - c1 * c1))
        if i == 1:
            s1 = -s1
        return (c1, s1)
    if i == 0:
        c1 = (- aa * bb + math.sqrt(delta)) / (bb * bb + cc * cc)
    else:
        c1 = (- aa * bb - math.sqrt(delta)) / (bb * bb + cc * cc)
    s1 = - (aa + bb * c1) / cc
    return (c1, s1)


def T_i(joint, i, ta=DORNA_TA):
    """Return the 4x4 transform matrix T_i as numpy array, matching C++ T_i behavior.
    joint: angle in radians
    i: index 0..6
    """
    ct = math.cos(joint + ta['delta'][i])
    st = math.sin(joint + ta['delta'][i])
    ca = math.cos(ta['alpha'][i])
    sa = math.sin(ta['alpha'][i])
    ai = ta['a'][i]
    di = ta['d'][i]

    res = np.array([
        [ct, -st * ca, st * sa, ai * ct],
        [st, ct * ca, -ct * sa, ai * st],
        [0.0, sa, ca, di],
        [0.0, 0.0, 0.0, 1.0]
    ], dtype=float)
    return res


def flange_r_base(joints):
    """Compute 4x4 flange pose given joints[6] (list/array of 6 joint angles).
    Mirrors the C++ flange_r_base which multiplies T_i for i=0..6 using joints[i-1].
    Returns a 4x4 numpy ndarray.
    """
    out = np.eye(4, dtype=float)
    for i in range(7):
        if i == 0:
            Ti = T_i(0.0, i)
        else:
            Ti = T_i(joints[i-1], i)
        out = out.dot(Ti)
    return out


def ik_matrix(mat, a2, a3, d1, d4, d5, d6, d7, f13, f23, f33, f14, f24, f34):
    """
    Placeholder for the huge ik_matrix literal translation. The full literal will be
    appended into this file when available. For now this left as a placeholder so
    the rest of the IK pipeline can be implemented and tested.
    """
    raise NotImplementedError('ik_matrix not yet implemented in this file; use the original C++ initializer or request a full programmatic translation')


def ik(goal_mat):
    """Python translation of ik(gsl_matrix* goal_mat, int * num_res, double final_res[38][6])
    Input: goal_mat as 4x4 numpy array
    Returns: list of solutions, each a length-6 array of joint angles (radians)
    """
    f11 = float(goal_mat[0,0]); f12 = float(goal_mat[0,1]); f13 = float(goal_mat[0,2]); f14 = float(goal_mat[0,3])
    f21 = float(goal_mat[1,0]); f22 = float(goal_mat[1,1]); f23 = float(goal_mat[1,2]); f24 = float(goal_mat[1,3])
    f31 = float(goal_mat[2,0]); f32 = float(goal_mat[2,1]); f33 = float(goal_mat[2,2]); f34 = float(goal_mat[2,3])

    a2 = DORNA_TA['a'][1]
    a3 = DORNA_TA['a'][2]
    d1 = DORNA_TA['d'][0]
    d4 = -DORNA_TA['d'][3]
    d5 = DORNA_TA['d'][4]
    d6 = DORNA_TA['d'][5]
    d7 = DORNA_TA['d'][6]

    # Build mat (12x12x3)
    mat = np.zeros((12,12,3), dtype=float)
    ik_matrix(mat, a2, a3, d1, d4, d5, d6, d7, f13, f23, f33, f14, f24, f34)

    # matrices A,B,C from mat
    A = mat[:,:,2]
    B = mat[:,:,1]
    C = mat[:,:,0]

    # invert A
    A_inv = np.linalg.inv(A)
    AIB = A_inv.dot(B)
    AIC = A_inv.dot(C)

    # assemble 24x24 matrix M
    M = np.zeros((24,24), dtype=float)
    # top-right: identity
    M[0:12,12:24] = np.eye(12)
    # bottom-left: -AIC
    M[12:24,0:12] = -AIC
    # bottom-right: -AIB
    M[12:24,12:24] = -AIB

    # eigen decomposition (may be complex)
    eigvals, eigvecs = np.linalg.eig(M)

    solutions = []
    # filter real roots
    real_idxs = [i for i,ev in enumerate(eigvals) if abs(ev.imag) < 0.9]

    for idx in real_idxs:
        x3 = eigvals[idx].real
        theta3 = 2.0 * math.atan(x3)

        v = eigvecs[:, idx]
        f5abs = abs(v[1])
        f4abs = abs(v[3])

        if f5abs > 1e-4:
            kval5 = v[0] / v[1]
        else:
            kval5 = v[10] / v[11]
        if f4abs > 1e-4:
            kval4 = v[0] / v[3]
        else:
            kval4 = v[8] / v[11]

        x5 = float(kval5.real)
        x4 = float(kval4.real)

        theta4 = 2.0 * math.atan(x4)
        theta5 = 2.0 * math.atan(x5)

        c3 = math.cos(theta3); s3 = math.sin(theta3)
        c4 = math.cos(theta4); s4 = math.sin(theta4)
        c5 = math.cos(theta5); s5 = math.sin(theta5)

        den_fs = (f14 * f23 - f13 * f24)

        # loop j=0..1 as in C++
        for j in range(2):
            if abs(den_fs) > 1e-5:
                if j == 1:
                    break
                c1 = ((-d4) * f13 + d6 * f13 * c4 + (d7 * f13 - f14) * s4 * s5) / den_fs
                s1 = ((-d4) * f23 + d6 * f23 * c4 + (d7 * f23 - f24) * s4 * s5) / den_fs
            else:
                try:
                    c1, s1 = solve_cs_equation(-d4 + d6 * c4, -(d7 * f23 - f24), -(-d7 * f13 + f14), j)
                except ValueError:
                    break

            theta1 = math.atan2(s1, c1)
            c1 = math.cos(theta1); s1 = math.sin(theta1)

            den = (f33 * f33 + f13 * f13 * c1 * c1 + f23 * f23 * s1 * s1 + f13 * f23 * math.sin(2.0 * theta1))
            c2 = -(((-f33) * (c5 * s3 + c3 * c4 * s5) - (f13 * c1 + f23 * s1) * (c3 * c5 - c4 * s3 * s5)) / den)
            s2 = ((-s3) * (f13 * c1 * c5 + f23 * c5 * s1 + f33 * c4 * s5) + c3 * (f33 * c5 - c4 * (f13 * c1 + f23 * s1) * s5)) / den
            theta2 = math.atan2(s2, c2)

            den2 = (c1 * c1 * (f21 * f21 + f22 * f22) - 2 * c1 * (f11 * f21 + f12 * f22) * s1 + (f11 * f11 + f12 * f12) * s1 * s1)
            c6 = (s1 * (c4 * f12 + c5 * f11 * s4) - c1 * (c4 * f22 + c5 * f21 * s4)) / den2
            s6 = ((-c1) * c4 * f21 + c4 * f11 * s1 + c1 * c5 * f22 * s4 - c5 * f12 * s1 * s4) / den2
            theta6 = math.atan2(s6, c6)

            q = np.array([theta1, theta2, theta3, theta4, theta5, theta6], dtype=float)

            test_fw = flange_r_base(q)
            # build goal_mat numpy from f_ij
            goal = np.array([[f11, f12, f13, f14], [f21, f22, f23, f24], [f31, f32, f33, f34], [0.0, 0.0, 0.0, 1.0]], dtype=float)
            diff = test_fw - goal
            norm = float(np.sum(diff * diff))
            if norm < 0.001:
                solutions.append(q)
            else:
                continue

    return solutions
