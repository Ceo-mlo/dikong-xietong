"""方案模型：把「子区划分 + 停靠点 + 航向角」评估成一份可比较的方案（论文 3.6 节目标与 5.3 节指标）。

单个方案 = 网格单元的标签向量 + 每个子区的停靠点 + 每个子区的航向角。
评估是增量的：改一个子区只重算该子区，车辆路书与全局指标增量合成。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from . import sortie as sortie_mod

CAR_SPEED = 11.0          # m/s，纸 5.1 表（39.6 km/h）
MAX_CELLS_PER_SORTIE = 469  # 19 872 m / (105 m × 0.4) 量级的容量参考值
PREP = sortie_mod.PREP


@dataclass
class Prepared:
    """一次求解里所有作业共享的只读准备物（S0 产物）。"""

    region: object
    grid: object
    graph: object
    docks: np.ndarray          # 停靠点对应的路网节点下标
    dock_xy: np.ndarray        # 停靠点 UTM 坐标 (n, 2)
    dist_base: np.ndarray      # 基地 → 各停靠点最短路 (m)
    base_node: int
    _rows: dict = field(default_factory=dict)
    area_km2: float = 0.0

    def __post_init__(self):
        if not self.area_km2:
            self.area_km2 = self.grid.area_m2 / 1e6

    # ---- 路网距离（惰性 Dijkstra，行缓存） ----
    def row(self, dock_pos):
        key = int(dock_pos)
        hit = self._rows.get(key)
        if hit is None:
            hit = self.graph.dijkstra(int(self.docks[key]))
            self._rows[key] = hit
        return hit

    def dock_to_dock(self, i, j):
        if i == j:
            return 0.0
        return float(self.row(int(i))[int(self.docks[int(j)])])

    def base_to_dock(self, i):
        return float(self.dist_base[int(i)])

    def summary(self):
        return {
            "cells": len(self.grid),
            "area_km2": round(self.area_km2, 3),
            "docks": int(len(self.docks)),
            "graph": self.graph.summary(),
        }


def cells_xy(grid, idx):
    return np.column_stack((grid.x[idx], grid.y[idx]))


class Solution:
    """一个候选方案（对应论文的 Solution：子区列表 + 停靠点 + 航向角）。"""

    def __init__(self, prepared: Prepared, labels, dock_pos, yaw, tag=""):
        self.p = prepared
        self.grid = prepared.grid
        self.labels = np.asarray(labels, dtype=np.int64)
        self.dock_pos = [int(v) for v in dock_pos]
        self.yaw = [float(v) for v in yaw]
        self.tag = tag
        self.cands = None          # 每个子区的候选停靠点 [(pos, 距离), ...]，供 op1 使用
        self.sub_cache = {}
        self._covered = None
        self._cells = {}
        self._metrics = None
        self._totals = None

    # ---- 基本结构 ----
    @property
    def n_sub(self):
        return len(self.dock_pos)

    def cells_of(self, lab):
        hit = self._cells.get(lab)
        if hit is None:
            hit = np.flatnonzero(self.labels == lab)
            self._cells[lab] = hit
        return hit

    def centroid_of(self, lab):
        idx = self.cells_of(lab)
        if len(idx) == 0:
            return None
        return (float(self.grid.x[idx].mean()), float(self.grid.y[idx].mean()))

    # ---- 单子区评估 ----
    def eval_sub(self, lab):
        hit = self.sub_cache.get(lab)
        if hit is not None:
            return hit
        idx = self.cells_of(lab)
        if len(idx) == 0:
            empty = {
                "label": int(lab),
                "cells": 0,
                "coverage": 0,
                "cells_in_sub": 0,
                "t_uav_s": PREP,
                "flight_m": 0.0,
                "empty_leg_m": 0.0,
                "path": [],
                "dock": None,
                "yaw_deg": float(self.yaw[lab] % 180.0),
                "exhausted": False,
                "photos": 0,
            }
            self.sub_cache[lab] = empty
            return empty
        dock_pos = self.dock_pos[lab]
        res = sortie_mod.simulate_sortie(
            cells_xy(self.grid, idx), self.p.dock_xy[dock_pos], self.yaw[lab]
        )
        res = dict(res)
        res["label"] = int(lab)
        res["cells_in_sub"] = int(len(idx))
        res["dock"] = int(dock_pos)
        res["dock_xy"] = [round(float(self.p.dock_xy[dock_pos][0]), 1),
                          round(float(self.p.dock_xy[dock_pos][1]), 1)]
        self.sub_cache[lab] = res
        self._covered = None
        return res

    def invalidate(self, lab=None):
        if lab is None:
            self.sub_cache.clear()
        else:
            self.sub_cache.pop(lab, None)
        self._covered = None
        self._metrics = None
        self._totals = None

    def set_dock(self, lab, pos):
        if self.dock_pos[lab] != pos:
            self.dock_pos[lab] = int(pos)
            self.invalidate(lab)

    def set_yaw(self, lab, deg):
        yaw = float(deg) % 180.0
        if abs(self.yaw[lab] - yaw) > 1e-9:
            self.yaw[lab] = yaw
            self.invalidate(lab)

    def move_cells(self, idx, dst):
        """把 idx 中的单元整体改判给 dst 子区（op2 的块迁移/单元回退共用）。"""
        arr = np.asarray(idx, dtype=np.int64)
        if len(arr) == 0:
            return set()
        touched = {int(v) for v in np.unique(self.labels[arr])}
        self.labels[arr] = int(dst)
        touched.add(int(dst))
        self._cells.clear()
        self._covered = None
        for t in touched:
            self.sub_cache.pop(t, None)
        return touched

    # ---- 覆盖 ----
    def covered_mask(self):
        if self._covered is None:
            cov = np.zeros(len(self.grid), dtype=bool)
            for lab in range(self.n_sub):
                if not (self.labels == lab).any():
                    continue
                r = self.eval_sub(lab)
                m = r.get("covered_mask")
                if m is not None and len(m):
                    cov[np.flatnonzero(self.cells_of(lab))[m]] = True
            self._covered = cov
        return self._covered

    # ---- 车辆路书 ----
    def route(self):
        used = []
        seen = set()
        for lab in range(self.n_sub):
            if len(self.cells_of(lab)) == 0:
                continue
            d = self.dock_pos[lab]
            if d in seen:
                continue
            seen.add(d)
            used.append(d)
        if not used:
            return {"order": [], "length_m": 0.0, "legs": 0,
                    "base_legs": [0.0, 0.0]}
        order = self._tsp(used)
        first, last = order[0], order[-1]
        base_in = self.p.base_to_dock(first)
        base_out = self.p.base_to_dock(last)
        legs = [base_in]
        for a, b in zip(order, order[1:]):
            legs.append(self.p.dock_to_dock(a, b))
        legs.append(base_out)
        total = sum(legs)
        return {"order": order, "length_m": round(total, 1),
                "legs": len(order), "leg_lengths": [round(v, 1) for v in legs]}

    def _tsp(self, used):
        n = len(used)
        if n <= 2:
            return list(used)
        # 最近邻构造（起点取离基地最近者）
        start = min(range(n), key=lambda i: self.p.base_to_dock(used[i]))
        order = [start]
        free = set(range(n)) - {start}
        while free:
            cur = used[order[-1]]
            nxt = min(free, key=lambda j: self.p.dock_to_dock(cur, used[j]))
            order.append(nxt)
            free.discard(nxt)
        # 2-opt 改进（最多 2 轮）
        for _ in range(2):
            improved = False
            for i in range(n - 1):
                for j in range(i + 2, n):
                    a, b = used[order[i]], used[order[i + 1]]
                    c, d = used[order[j]], used[order[(j + 1) % n]]
                    if (j + 1) % n == i:
                        continue
                    old = self.p.dock_to_dock(a, b) + self.p.dock_to_dock(c, d)
                    new = self.p.dock_to_dock(a, c) + self.p.dock_to_dock(b, d)
                    if new + 1e-6 < old:
                        order[i + 1:j + 1] = reversed(order[i + 1:j + 1])
                        improved = True
            if not improved:
                break
        return [used[k] for k in order]

    # ---- 汇总 ----
    def totals(self):
        if self._totals is not None:
            return self._totals
        n = 0
        cov_cells = 0
        t_uav = 0.0
        t_fly = 0.0
        t_hover = 0.0
        empty_leg = 0.0
        flight_m = 0.0
        exhausted = 0
        for lab in range(self.n_sub):
            if len(self.cells_of(lab)) == 0:
                continue
            r = self.eval_sub(lab)
            n += 1
            cov_cells += int(r["coverage"])
            t_uav += float(r["t_uav_s"])
            t_fly += float(r["t_fly_s"])
            t_hover += float(r["t_hover_s"])
            empty_leg += float(r.get("empty_leg_m", 0.0))
            flight_m += float(r["flight_m"])
            if r.get("exhausted"):
                exhausted += 1
        route = self.route()
        t_car = route["length_m"] / CAR_SPEED
        t_total = t_uav + t_car
        out = {
            "sorties": n,
            "coverage_cells": cov_cells,
            "t_uav_s": round(t_uav, 1),
            "t_fly_s": round(t_fly, 1),
            "t_hover_s": round(t_hover, 1),
            "t_prep_s": round(PREP * n, 1),
            "t_car_s": round(t_car, 1),
            "t_total_s": round(t_total, 1),
            "route_m": route["length_m"],
            "route_legs": route["legs"],
            "empty_leg_m": round(empty_leg, 1),
            "flight_m": round(flight_m, 1),
            "exhausted_sorties": exhausted,
        }
        self._totals = out
        return out

    def metrics(self):
        tot = self.totals()
        area = self.p.area_km2
        cells = len(self.grid)
        cov = tot["coverage_cells"] / cells if cells else 0.0
        t_total = max(1e-6, tot["t_total_s"])
        n = max(1, tot["sorties"])
        ce = tot["t_total_s"] / (cov * area) if cov > 0 else float("inf")
        rur = (tot["flight_m"] / n) / sortie_mod.MAX_RANGE if n else 0.0
        elr = (tot["empty_leg_m"] / sortie_mod.VEL) / t_total
        ald = tot["route_m"] / max(1, tot["route_legs"])
        out = {
            "coverage": round(cov, 6),
            "t_total_s": tot["t_total_s"],
            "t_uav_s": tot["t_uav_s"],
            "t_car_s": tot["t_car_s"],
            "sorties": tot["sorties"],
            "CE": round(ce, 1),
            "AUT": round(tot["t_uav_s"] / n, 1),
            "RUR": round(rur, 4),
            "ELR": round(elr, 4),
            "ALD": round(ald, 1),
            "MCG": None,
            "area_km2": round(area, 3),
            "cells": cells,
            "coverage_cells": tot["coverage_cells"],
        }
        self._metrics = out
        return out

    # ---- 复制 ----
    def clone(self, tag=None):
        s = Solution(self.p, self.labels.copy(), list(self.dock_pos), list(self.yaw),
                     tag if tag is not None else self.tag)
        s.cands = self.cands
        return s

    # ---- 序列化 ----
    def snapshot(self):
        tot = self.totals()
        m = self.metrics()
        route = self.route()
        subs = []
        for lab in range(self.n_sub):
            idx = self.cells_of(lab)
            if len(idx) == 0:
                continue
            r = self.eval_sub(lab)
            subs.append({
                "label": int(lab),
                "cells": int(len(idx)),
                "cells_in_sub": int(r["cells_in_sub"]),
                "coverage": int(r["coverage"]),
                "dock": int(self.dock_pos[lab]),
                "dock_xy": r.get("dock_xy"),
                "yaw_deg": round(float(self.yaw[lab] % 180.0), 2),
                "t_uav_s": r["t_uav_s"],
                "flight_m": r["flight_m"],
                "photos": r.get("photos", 0),
                "empty_leg_m": r.get("empty_leg_m", 0.0),
                "exhausted": bool(r.get("exhausted")),
                "path": r.get("path", []),
            })
        out = {
            "tag": self.tag,
            "grid": self.grid.summary(),
            "labels_rle": self.grid.to_rle(self.labels),
            "subs": subs,
            "route": route,
            "totals": tot,
            "metrics": m,
        }
        # 目标与指标同时平铺到顶层，方便前端/推荐逻辑直接取用
        out.update({k: m[k] for k in ("coverage", "t_total_s", "t_uav_s", "t_car_s",
                                      "sorties", "CE", "AUT", "RUR", "ELR", "ALD",
                                      "area_km2", "cells", "coverage_cells")})
        return out
