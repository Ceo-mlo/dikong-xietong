"""单架次航线仿真：牛耕式条带 + 空洞避让 + 能量/航程约束（论文 3.2、4.1.3 节）。

与论文一致的关键口径：
- 旁向间隔 250 m（条带间距）、航向间隔 105 m（拍照步长）、悬停 1 s/点、起降准备 300 s、速度 15 m/s；
- 可用航程 19 872 m（277 200 J × 0.8 安全系数折算），返航必须留有余量；
- 单元被覆盖 = 单元所在条带的「已飞区间」在 x 方向上覆盖该单元中心。

条带内不再从最左飞到最右，而是按单元聚成若干连续区间（空洞自动跳过），
区间之间只做沿条带的转场飞行。这样更贴近真实作业，也避免了空洞带来的无效航程。
"""
from __future__ import annotations

import math

import numpy as np

SWATH = 250.0
PHOTO_STEP = 105.0
VEL = 15.0
HOVER = 1.0
PREP = 300.0
MAX_RANGE = 19872.0
CELL_RES = 100.0


def _intervals(xs, res):
    """把一条带内的 x 坐标聚成连续区间（间隙 > 1.5 倍网格视为空洞）。"""
    if len(xs) == 0:
        return []
    xs = np.sort(np.asarray(xs, dtype=float))
    tol = 1.5 * res
    out = []
    lo = hi = float(xs[0])
    for v in xs[1:]:
        v = float(v)
        if v - hi <= tol:
            hi = v
        else:
            out.append((lo, hi))
            lo = hi = v
    out.append((lo, hi))
    return out


def simulate_sortie(cells_xy, dock_xy, yaw_deg, max_range=MAX_RANGE, cell_res=CELL_RES):
    cells_xy = np.asarray(cells_xy, dtype=float)
    dock = np.asarray(dock_xy, dtype=float)
    m = len(cells_xy)
    if m == 0:
        return _empty_result(yaw_deg, dock)

    yaw = math.radians(yaw_deg)
    c, s = math.cos(yaw), math.sin(yaw)
    cx = float(cells_xy[:, 0].mean())
    cy = float(cells_xy[:, 1].mean())
    dx = cells_xy[:, 0] - cx
    dy = cells_xy[:, 1] - cy
    xr = c * dx + s * dy
    yr = -s * dx + c * dy
    dock_r = np.array([c * (dock[0] - cx) + s * (dock[1] - cy),
                       -s * (dock[0] - cx) + c * (dock[1] - cy)])

    ymin = float(yr.min())
    band = np.floor((yr - ymin) / SWATH).astype(np.int64)
    nb = int(band.max()) + 1
    order = np.argsort(band, kind="stable")
    band_sorted = band[order]
    x_sorted = xr[order]
    bounds = np.searchsorted(band_sorted, np.arange(nb + 1))

    flight = 0.0
    photos = 0
    bands_flown = 0
    flown = []            # [(band, lo, hi)]
    prev = dock_r
    prev_left = True
    exhausted = False
    path_pts = [dock.copy()]
    first_start = None
    last_end = None

    for k in range(nb):
        a, b = int(bounds[k]), int(bounds[k + 1])
        if b <= a:
            continue
        yb = ymin + (k + 0.5) * SWATH
        ivs = _intervals(x_sorted[a:b], cell_res)
        if not ivs:
            continue
        if bands_flown == 0:
            left = abs(prev[0] - ivs[0][0]) <= abs(prev[0] - ivs[-1][1])
        else:
            left = not prev_left
        seq = ivs if left else list(reversed(ivs))
        stop_band = False
        for lo_x, hi_x in seq:
            start_x, end_x = (lo_x, hi_x) if left else (hi_x, lo_x)
            leg = math.hypot(start_x - prev[0], yb - prev[1])
            length = abs(end_x - start_x)
            if flight + leg + length >= max_range - 1.0:
                # 二分本区间可飞长度：剩余航程要留足返航距离
                remain = max_range - flight - leg
                sign = 1.0 if end_x > start_x else -1.0
                lo_t, hi_t = 0.0, length
                for _ in range(16):
                    mid = 0.5 * (lo_t + hi_t)
                    ex = start_x + sign * mid
                    if mid + math.hypot(ex - dock_r[0], yb - dock_r[1]) <= remain:
                        lo_t = mid
                    else:
                        hi_t = mid
                if lo_t < 1.0:
                    exhausted = True
                    stop_band = True
                    break
                end_x = start_x + sign * lo_t
                length = abs(end_x - start_x)
                exhausted = True
            flight += leg + length
            photos += max(1, int(length // PHOTO_STEP))
            flown.append((k, min(start_x, end_x), max(start_x, end_x)))
            if first_start is None:
                first_start = (start_x, yb)
            last_end = (end_x, yb)
            path_pts.append(_unrot(np.array([start_x, yb]), cx, cy, c, s))
            path_pts.append(_unrot(np.array([end_x, yb]), cx, cy, c, s))
            prev = np.array([end_x, yb])
            prev_left = end_x > start_x
            if exhausted:
                stop_band = True
                break
        if stop_band:
            break
        bands_flown += 1

    flight += math.hypot(prev[0] - dock_r[0], prev[1] - dock_r[1])
    path_pts.append(dock.copy())

    empty_leg = 0.0
    if first_start is not None and last_end is not None:
        empty_leg = math.hypot(first_start[0] - dock_r[0], first_start[1] - dock_r[1])
        empty_leg += math.hypot(last_end[0] - dock_r[0], last_end[1] - dock_r[1])

    covered = np.zeros(m, dtype=bool)
    for k, lo_x, hi_x in flown:
        sel = band == k
        covered |= sel & (xr >= lo_x - 0.5 * cell_res) & (xr <= hi_x + 0.5 * cell_res)

    t_fly = flight / VEL
    t_hover = photos * HOVER
    return {
        "coverage": int(covered.sum()),
        "covered_mask": covered,
        "cells": m,
        "path": [[round(float(p[0]), 1), round(float(p[1]), 1)] for p in path_pts],
        "flight_m": round(flight, 1),
        "empty_leg_m": round(empty_leg, 1),
        "photos": int(photos),
        "t_fly_s": round(t_fly, 1),
        "t_hover_s": round(t_hover, 1),
        "t_prep_s": PREP,
        "t_uav_s": round(t_fly + t_hover + PREP, 1),
        "bands_total": int(nb),
        "bands_flown": int(bands_flown),
        "segments": len(flown),
        "yaw_deg": round(float(yaw_deg % 180.0), 2),
        "exhausted": bool(exhausted),
        "energy_margin_m": round(max_range - flight, 1),
    }


def _unrot(p, cx, cy, c, s):
    return np.array([c * p[0] - s * p[1] + cx, s * p[0] + c * p[1] + cy])


def _empty_result(yaw_deg, dock):
    return {
        "coverage": 0,
        "covered_mask": np.zeros(0, dtype=bool),
        "cells": 0,
        "path": [[float(dock[0]), float(dock[1])]],
        "flight_m": 0.0,
        "empty_leg_m": 0.0,
        "photos": 0,
        "t_fly_s": 0.0,
        "t_hover_s": 0.0,
        "t_prep_s": PREP,
        "t_uav_s": PREP,
        "bands_total": 0,
        "bands_flown": 0,
        "segments": 0,
        "yaw_deg": round(float(yaw_deg % 180.0), 2),
        "exhausted": False,
        "energy_margin_m": MAX_RANGE,
    }
