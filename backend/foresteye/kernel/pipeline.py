"""S0–S6 求解流水线：把内核各阶段编排成一次可复现的作业（架构/03 第 3 节）。"""
from __future__ import annotations

import math
import time

import numpy as np

from . import archive as archive_mod
from . import fo as fo_mod
from . import ga, geo, model, partition
from .grid import build_grid
from .model import Prepared, Solution
from .region import load_region, load_from_files, save_region
from .roads import build_graph, select_docks

STAGES = [
    ("S0", "预处理：网格、路网、停靠点"),
    ("S1", "初始划分：KMeans++ 与停靠点匹配"),
    ("S2", "航向角优化：双适应度 GA"),
    ("S3", "车辆路书：最近邻 + 2-opt"),
    ("S4", "FO 交替迭代：停靠点 + 重划分"),
    ("S5", "Pareto 归档与精简"),
    ("S6", "指标计算与方案快照"),
]


def build_prepared(region, res=100.0, dock_cfg=None, emit=None):
    dock_cfg = dock_cfg or {}
    t0 = time.time()
    grid = build_grid(region, res)
    if emit:
        emit({"type": "log", "stage": "S0", "msg": f"网格 {len(grid)} 个有效单元（{res:.0f} m）"})
    graph = build_graph(region.roads, snap_m=45.0, merge_m=2.0)
    meta = graph.summary()
    base_node, _bd = graph.nearest_node(region.base)
    docks, dist_base, base_idx = select_docks(
        graph, region, base_node,
        tau_dock_m=float(dock_cfg.get("tau_dock_m", 60000.0)),
        max_docks=int(dock_cfg.get("max_docks", 280)),
        bucket_m=float(dock_cfg.get("bucket_m", 200.0)),
    )
    reachable = int(np.isfinite(dist_base).sum())
    if emit:
        emit({"type": "log", "stage": "S0",
              "msg": f"路网 {meta['nodes']} 节点 / {meta['segments']} 段 / {meta['length_km']:.1f} km；"
                     f"停靠点 {len(docks)} 个（可达 {reachable}）"})
    prep = Prepared(region=region, grid=grid, graph=graph, docks=docks,
                    dock_xy=np.asarray(graph.nodes, dtype=float)[docks],
                    dist_base=np.asarray(dist_base, dtype=float)[docks], base_node=base_idx)
    prep.prepare_ms = int((time.time() - t0) * 1000)
    return prep


def default_ladder(total_cells, cfg):
    # 一个子区对应一个停靠点、一个架次（论文 3.1 节），因此子区规模必须落在单架次能力内：
    # N_min >= ceil(总单元 / 单架次最大单元)，否则必然出现大量折返、覆盖率被能量截断。
    feasible_min = math.ceil(total_cells / float(model.MAX_CELLS_PER_SORTIE) * 1.02)
    lo = int(cfg.get("sortie_min") or max(8, math.ceil(total_cells / 720.0)))
    hi = int(cfg.get("sortie_max") or max(lo + 3, math.ceil(total_cells / 360.0)))
    # 式(3.25)：子区不得超过单架次能力，否则每个架次都折返、覆盖率被能量截断。
    if lo < feasible_min:
        lo = feasible_min
        hi = max(hi, lo + 2)
    steps = int(cfg.get("ladder_steps", 4))
    if steps <= 1:
        return [lo]
    out = sorted({int(round(lo + (hi - lo) * i / (steps - 1))) for i in range(steps)})
    return out


def solve(prepared, cfg, emit=None, should_stop=None, t0=None):
    """执行一次完整求解，返回 {plans, front, meta}。"""
    t0 = t0 or time.time()
    cfg = dict(cfg)
    quick = bool(cfg.get("quick"))
    rng = np.random.default_rng(int(cfg.get("seed", 2024)))
    grid = prepared.grid
    xy = np.column_stack((grid.x, grid.y))
    total = len(grid)
    ladder = cfg.get("ladder") or default_ladder(total, cfg)

    def log(stage, msg):
        if emit:
            emit({"type": "log", "stage": stage, "msg": msg})

    # S1 + S2：逐架次档位生成初始方案
    initials = []
    for li, n_sortie in enumerate(ladder):
        if should_stop and should_stop():
            break
        labels, _ = partition.kmeanspp(xy, n_sortie, seed=int(cfg.get("seed", 2024)) + li)
        labels = partition.repair_connectivity(grid, labels, min_size=12, rounds=2)
        cells = partition.subregion_cells(labels)
        cents = [(float(grid.x[c].mean()), float(grid.y[c].mean())) for c in cells]
        cands = partition.dock_candidates(cents, prepared.dock_xy, prepared.dist_base, top_m=12)
        dock_pos = partition.assign_docks(cands, prepared.dock_xy, labels, grid)
        for mode in ("coverage", "time"):
            if should_stop and should_stop():
                break
            yaw = []
            for k, cl in enumerate(cells):
                pos = dock_pos[k]
                if len(cl) == 0 or pos < 0:
                    yaw.append(0.0)
                    continue
                y, _res, _n = ga.heading_ga(
                    xy[cl], prepared.dock_xy[pos], mode=mode,
                    pop=8 if quick else 10, gens=6 if quick else 12,
                    seed=int(cfg.get("seed", 2024)) + k * 7 + (0 if mode == "coverage" else 3),
                )
                yaw.append(y)
            sol = Solution(prepared, labels, dock_pos, yaw, tag=f"n{n_sortie}-{mode[:3]}")
            sol.cands = cands
            m = sol.metrics()
            log("S2", f"{sol.tag}：覆盖 {m['coverage']*100:.2f}%、{m['t_total_s']/3600:.2f} h、"
                     f"{m['sorties']} 架次")
            initials.append(sol)
            if emit:
                emit({"type": "point", "stage": "S2", "tag": sol.tag,
                      "coverage": m["coverage"], "t_total_s": m["t_total_s"],
                      "sorties": m["sorties"]})

    # S4：FO 迭代
    log("S4", f"初始解 {len(initials)} 个，进入 FO 迭代（外层上限 {cfg.get('max_outer', 20)}）")

    def on_outer(ev):
        if emit:
            emit({"type": "fo", **ev})

    front, history = fo_mod.run_fo(prepared, initials, cfg, emit=on_outer, should_stop=should_stop)

    # S5 + S6：归档精简与快照
    front = archive_mod.thin(front, int(cfg.get("max_plans", 40)))
    archive_mod.annotate(front)
    plans = []
    for i, f in enumerate(sorted(front, key=lambda x: (x["sorties"], x["t_total_s"]))):
        snap = f["solution"].snapshot()
        snap["id"] = f"P{i+1:02d}"
        snap["metrics"].update({k: f.get(k) for k in ("MCG", "delta_coverage_pp", "delta_time_s")})
        plans.append(snap)
    recs = archive_mod.recommendations(plans, k=3)
    elapsed = time.time() - t0
    meta = {
        "elapsed_s": round(elapsed, 1),
        "stages": [s for s, _ in STAGES],
        "ladder": ladder,
        "front_size": len(plans),
        "initial_solutions": len(initials),
        "engine": "地空协同林业监测决策系统 1.0.0",
        "seed": cfg.get("seed", 2024),
        "quick": quick,
        "prepared": prepared.summary(),
        "prepared_ms": getattr(prepared, "prepare_ms", None),
        "params": {k: v for k, v in cfg.items() if k not in ("cand_map",)},
    }
    log("S6", f"完成：{len(plans)} 个方案，用时 {elapsed:.1f} s")
    return {"plans": plans, "recommendations": recs, "meta": meta, "history": history}
