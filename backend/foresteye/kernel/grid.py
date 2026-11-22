"""规则网格：按网格中心点判定有效单元（对应论文 3.2 节的覆盖单元定义）。"""
from __future__ import annotations

from collections import deque

import numpy as np

from . import geo


class Grid:
    """有效单元集合。ix/iy 为整数行列号，(x,y) 为单元中心点（UTM）。"""

    def __init__(self, x, y, ix, iy, res, bbox, nx, ny, candidates):
        self.x = x
        self.y = y
        self.ix = ix
        self.iy = iy
        self.res = res
        self.bbox = bbox
        self.nx = nx
        self.ny = ny
        self.candidates = candidates
        self._index = {(int(a), int(b)): k for k, (a, b) in enumerate(zip(ix, iy))}

    def __len__(self):
        return int(len(self.x))

    @property
    def area_m2(self):
        return float(len(self) * self.res * self.res)

    @property
    def xy(self):
        return np.stack([self.x, self.y], axis=-1)

    def index_of(self, a, b):
        return self._index.get((int(a), int(b)))

    def neighbors8(self, idx):
        a, b = int(self.ix[idx]), int(self.iy[idx])
        out = []
        for da in (-1, 0, 1):
            for db in (-1, 0, 1):
                if da == 0 and db == 0:
                    continue
                j = self._index.get((a + da, b + db))
                if j is not None:
                    out.append(j)
        return out

    def components(self, mask):
        """mask: bool 数组，返回 8 邻接连通分量（下标列表的列表）。"""
        seen = np.zeros(len(self), dtype=bool)
        comps = []
        for start in range(len(self)):
            if not mask[start] or seen[start]:
                continue
            comp = []
            dq = deque([start])
            seen[start] = True
            while dq:
                cur = dq.popleft()
                comp.append(cur)
                for nb in self.neighbors8(cur):
                    if mask[nb] and not seen[nb]:
                        seen[nb] = True
                        dq.append(nb)
            comps.append(comp)
        return comps

    def to_rle(self, labels):
        """半开区间行游程编码：[[iy, ix0, ix1, label], ...]，用于快照存储与前端着色。"""
        runs = []
        order = np.lexsort((self.ix, self.iy))
        cur_y, start_x, prev_x, cur_lab = None, None, None, None
        for k in order:
            y, x, lab = int(self.iy[k]), int(self.ix[k]), int(labels[k])
            if y != cur_y or lab != cur_lab or (prev_x is not None and x != prev_x + 1):
                if cur_y is not None:
                    runs.append([cur_y, start_x, prev_x, cur_lab])
                cur_y, start_x, cur_lab = y, x, lab
            prev_x = x
        if cur_y is not None:
            runs.append([cur_y, start_x, prev_x, cur_lab])
        return runs

    def summary(self):
        minx, miny, maxx, maxy = self.bbox
        return {
            "res_m": self.res,
            "cells": int(len(self)),
            "candidates": int(self.candidates),
            "keep_ratio": round(len(self) / max(1, self.candidates), 4),
            "nx": int(self.nx),
            "ny": int(self.ny),
            "bbox": [round(v, 1) for v in self.bbox],
            "area_km2": round(self.area_m2 / 1e6, 3),
            "grid_mb": round(len(self) * 8 / 1048576, 2),
        }


def build_grid(region, res=100.0):
    minx, miny, maxx, maxy = region.bbox
    res = float(res)
    x0 = np.floor(minx / res) * res
    y0 = np.floor(miny / res) * res
    nx = int(np.ceil((maxx - x0) / res)) + 1
    ny = int(np.ceil((maxy - y0) / res)) + 1
    ix_all, iy_all = np.meshgrid(np.arange(nx), np.arange(ny))
    cx = x0 + (ix_all.ravel() + 0.5) * res
    cy = y0 + (iy_all.ravel() + 0.5) * res
    inside = geo.points_in_region(cx, cy, region.outer, region.holes)
    ix = ix_all.ravel()[inside].astype(np.int32)
    iy = iy_all.ravel()[inside].astype(np.int32)
    x = cx[inside]
    y = cy[inside]
    order = np.lexsort((ix, iy))
    return Grid(x[order], y[order], ix[order], iy[order], res,
                (x0, y0, x0 + nx * res, y0 + ny * res), nx, ny, int(nx * ny))
