"""标定脚本：装配真实数据、生成网格、测单次架次仿真耗时。"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "backend"))

import numpy as np

from foresteye.kernel import geo, grid as gridmod, region as regionmod, roads, sortie

WS = os.path.abspath(os.path.join(ROOT, "..", ".."))
CANS = os.path.join(WS, "tdp软件依赖的模型", "数据与代码", "pyworkspace", "Canshu")

t0 = time.time()
reg, issues = regionmod.load_from_files(
    "寨岗镇疫区",
    os.path.join(CANS, "zgz_point.xlsx"),
    os.path.join(CANS, "kongdong_12.xlsx"),
    os.path.join(CANS, "Build_with_utm_coords.csv"),
)
print("region load %.2fs" % (time.time() - t0))
for it in issues:
    print(" ", it["level"], it["code"], it["message"])
print("summary", reg.summary())

t0 = time.time()
g = roads.build_graph(reg.roads)
print("graph %.2fs" % (time.time() - t0), g.summary())

t0 = time.time()
grid = gridmod.build_grid(reg, 100.0)
print("grid %.2fs" % (time.time() - t0), grid.summary())

t0 = time.time()
docks, dist_base, base_idx = roads.select_docks(g, reg)
print("docks %.2fs" % (time.time() - t0), "count", len(docks), "base", reg.base, "base_idx", base_idx)
print("dock dist km: min %.1f max %.1f" % (dist_base[docks].min() / 1000, dist_base[docks].max() / 1000))

# 单次架次仿真耗时（取前 400 个单元 + 最近停靠点）
sub = grid.xy[:400]
dn, _ = g.nearest_node(sub.mean(axis=0))
t0 = time.time()
res = sortie.simulate_sortie(sub, g.nodes[dn], 30.0)
dt = time.time() - t0
print("sortie sim %.1f ms" % (dt * 1000))
print({k: v for k, v in res.items() if k not in ("covered_mask", "path")})

# 航向角扫描耗时
t0 = time.time()
best = None
for yaw in range(0, 180, 3):
    r = sortie.simulate_sortie(sub, g.nodes[dn], yaw)
    key = (r["coverage"], -r["t_uav_s"])
    if best is None or key > best[0]:
        best = (key, yaw, r)
print("60 yaw evals %.2f s" % (time.time() - t0), "best yaw", best[1], "cov", best[2]["coverage"], "t_uav", best[2]["t_uav_s"])
