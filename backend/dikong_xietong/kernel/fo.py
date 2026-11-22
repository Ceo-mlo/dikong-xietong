"""FO 固定优化交替迭代（论文 4.2 节）。

外层：从当前前沿抽样种子方案 → op1（停靠点组合）→ op2（孤立块重划分）→ 归档。
两个算子都只重算子区，接受准则用加权标量化 value = 覆盖率 − λ·时间（λ 由种子决定）。
"""
from __future__ import annotations

import math

import numpy as np

from . import archive as archive_mod
from . import model
from .model import cells_xy

K_UCB = 1.0
MIN_BLOCK = 15


class Stats:
    """每个子区的尝试次数与平均收益，供 UCB 选择。

    子区编号随方案（架次档位）变化，故按标签字典存储，不做定长数组。
    """

    def __init__(self, n_sub=0):
        self.tries = {}
        self.gains = {}
        self.total = 0.0

    def _ensure(self, lab):
        if lab not in self.tries:
            self.tries[lab] = 0.0
            self.gains[lab] = 0.0

    def tick(self, lab=None, gain=None):
        self.total += 1.0
        if lab is None:
            return
        self._ensure(lab)
        self.tries[lab] += 1.0
        self.gains[lab] = 0.6 * self.gains[lab] + 0.4 * max(0.0, gain)

    def scores(self, active):
        """返回与 active 等长的得分列表（UCB：平均收益 + 探索项）。"""
        out = []
        for lab in active:
            self._ensure(lab)
            n = self.tries[lab]
            mean = self.gains[lab]
            out.append(mean + K_UCB * math.sqrt(math.log(self.total + 1.0) / (n + 1.0)))
        return np.asarray(out, dtype=float)


def active_labels(sol):
    return [lab for lab in range(sol.n_sub) if len(sol.cells_of(lab)) > 0]


def _cands_of(cand_map, lab):
    if isinstance(cand_map, dict):
        return cand_map.get(lab, [])
    if lab < len(cand_map):
        return cand_map[lab]
    return []


def scalar(metrics, lam, t_scale):
    return metrics["coverage"] - lam * metrics["t_total_s"] / t_scale


def _sub_eval(prepared, sol, lab, dock_pos, yaw):
    idx = sol.cells_of(lab)
    if len(idx) == 0:
        return None
    return sortie_sim(prepared, sol, idx, dock_pos, yaw)


def sortie_sim(prepared, sol, idx, dock_pos, yaw):
    from . import sortie

    return sortie.simulate_sortie(cells_xy(sol.grid, idx), prepared.dock_xy[dock_pos],
                                  float(yaw) % 180.0)


def op1_docks(sol, cand_map, stats, lam, rng, t_scale, n_try=4, cells_total=1):
    """子问题 1：为子区换一个更合适的候选停靠点（簇内替换的单步版本）。"""
    labels = active_labels(sol)
    if not labels:
        return 0.0
    cand_map = cand_map or getattr(sol, "cands", None) or []
    scores = stats.scores(labels)
    order = np.argsort(-scores)
    best_gain = 0.0
    touched = 0
    for k in range(min(n_try, len(order))):
        lab = labels[int(order[k])]
        cands = [p for p, _ in _cands_of(cand_map, lab) if p != sol.dock_pos[lab]]
        if not cands:
            stats.tick(lab, 0.0)
            continue
        cur = sol.eval_sub(lab)
        cur_v = cur["coverage"] / cells_total - lam * cur["t_uav_s"] / t_scale
        pick, pick_v, pick_res = None, cur_v, None
        for pos in cands[:max(3, n_try)]:
            res = _sub_eval(sol.p, sol, lab, pos, sol.yaw[lab])
            if res is None:
                continue
            v = res["coverage"] / cells_total - lam * res["t_uav_s"] / t_scale
            if v > pick_v + 1e-12:
                pick, pick_v, pick_res = pos, v, res
        stats.tick(lab, pick_v - cur_v)
        if pick is not None:
            sol.set_dock(lab, pick)
            touched += 1
            best_gain += pick_v - cur_v
    return best_gain


def _small_blocks(sol, grid, lab, min_block=MIN_BLOCK):
    """返回该子区里小于 min_block 的连通块（按大小升序）。"""
    idx = sol.cells_of(lab)
    if len(idx) < min_block:
        return [idx]
    inside = set(int(v) for v in idx)
    seen = set()
    blocks = []
    for start in idx:
        s = int(start)
        if s in seen:
            continue
        stack = [s]
        seen.add(s)
        comp = []
        while stack:
            cur = stack.pop()
            comp.append(cur)
            for nb in grid.neighbors8(cur):
                if nb in inside and nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        if len(comp) < min_block:
            blocks.append(np.asarray(comp, dtype=np.int64))
    blocks.sort(key=len)
    return blocks


def op2_regions(sol, stats, lam, rng, t_scale, max_attempt=3, cells_total=1):
    """子问题 2：把孤立小块迁到邻接最多的子区，并只重算受影响子区。"""
    grid = sol.grid
    labels = active_labels(sol)
    if not labels:
        return 0.0
    scores = stats.scores(labels)
    order = np.argsort(-scores)
    applied = 0.0
    for k in range(min(max_attempt, len(order))):
        lab = labels[int(order[k])]
        blocks = _small_blocks(sol, grid, lab)
        if not blocks:
            stats.tick(lab, 0.0)
            continue
        block = blocks[int(rng.integers(len(blocks)))]
        votes = {}
        for c in block:
            for nb in grid.neighbors8(int(c)):
                other = int(sol.labels[nb])
                if other != lab:
                    votes[other] = votes.get(other, 0) + 1
        if not votes:
            stats.tick(lab, 0.0)
            continue
        tgt = max(votes, key=votes.get)
        if len(sol.cells_of(lab)) - len(block) < MIN_BLOCK:
            stats.tick(lab, 0.0)
            continue
        before = _pair_value(sol, [lab, tgt], lam, t_scale, cells_total)
        moved = sol.move_cells(block, tgt)
        after = _pair_value(sol, sorted(moved | {lab, tgt}), lam, t_scale, cells_total)
        gain = after - before
        if gain > 1e-12:
            applied += gain
            stats.tick(lab, gain)
        else:
            for c in block:  # 回滚
                sol.labels[int(c)] = lab
            sol.invalidate()
            stats.tick(lab, 0.0)
    return applied


def _pair_value(sol, labs, lam, t_scale, cells_total):
    v = 0.0
    for lab in labs:
        if lab < 0 or lab >= sol.n_sub:
            continue
        if len(sol.cells_of(lab)) == 0:
            continue
        r = sol.eval_sub(lab)
        v += r["coverage"] / cells_total - lam * r["t_uav_s"] / t_scale
    return v


def run_fo(prepared, initial_solutions, cfg, emit=None, should_stop=None):
    """返回 (front, history)。cfg 是 SolveConfig 风格的字典。"""
    lam_by_tag = {s.tag: _lam_of(s.tag) for s in initial_solutions}
    front = []
    history = []
    stats = Stats(initial_solutions[0].n_sub if initial_solutions else 0)
    rng = np.random.default_rng(int(cfg.get("seed", 0)) + 991)
    cells_total = len(prepared.grid)
    t_scale = float(cfg.get("t_scale", 7200.0))
    cand_map = cfg.get("cand_map", {})

    for sol in initial_solutions:
        m = sol.metrics()
        lam = lam_by_tag.get(sol.tag, 0.5)
        item = _as_candidate(sol, m, lam)
        front, added = archive_mod.insert(front, item)
        if added:
            history.append({"outer": 0, "coverage": m["coverage"], "t_total_s": m["t_total_s"],
                            "tag": sol.tag, "added": True})

    max_outer = int(cfg.get("max_outer", 20))
    stop_no_improve = int(cfg.get("stop_no_improve", 10))
    seeds_per_outer = int(cfg.get("seeds_per_outer", 4))
    no_improve = 0
    outer = 0
    for outer in range(1, max_outer + 1):
        if should_stop and should_stop():
            break
        added_any = False
        seeds = _pick_seeds(front, initial_solutions, seeds_per_outer, rng)
        for base, lam in seeds:
            child = base.clone(tag=base.tag)
            op1_docks(child, cand_map, stats, lam, rng, t_scale,
                      n_try=2 if cfg.get("quick") else 4, cells_total=cells_total)
            op2_regions(child, stats, lam, rng, t_scale,
                        max_attempt=2 if cfg.get("quick") else 3, cells_total=cells_total)
            m = child.metrics()
            item = _as_candidate(child, m, lam)
            front, added = archive_mod.insert(front, item)
            added_any = added_any or added
            if added:
                history.append({"outer": outer, "coverage": m["coverage"],
                                "t_total_s": m["t_total_s"], "tag": child.tag, "added": True})
        if emit:
            best = max(front, key=lambda f: f["coverage"]) if front else None
            emit({
                "outer": outer,
                "front_size": len(front),
                "coverage": round(best["coverage"], 4) if best else 0,
                "t_total_s": best["t_total_s"] if best else 0,
                "added": added_any,
            })
        if added_any:
            no_improve = 0
        else:
            no_improve += 1
            if no_improve >= stop_no_improve:
                break
    return front, history


def _lam_of(tag):
    """种子标签形如 n19-cov / n19-time，用覆盖率水平映射成权重。"""
    lam = 0.35
    if "time" in tag:
        lam = 0.75
    elif "cov" in tag:
        lam = 0.2
    return lam


def _pick_seeds(front, initial_solutions, k, rng):
    pool = [(f["solution"], f.get("lam", 0.5)) for f in front if f.get("solution") is not None]
    if not pool:
        return [(s, _lam_of(s.tag)) for s in initial_solutions[:k]]
    if len(pool) <= k:
        return pool
    idx = rng.choice(len(pool), size=k, replace=False)
    return [pool[int(i)] for i in idx]


def _as_candidate(sol, metrics, lam):
    return {
        "coverage": metrics["coverage"],
        "t_total_s": metrics["t_total_s"],
        "sorties": metrics["sorties"],
        "metrics": metrics,
        "tag": sol.tag,
        "lam": lam,
        "solution": sol,
    }
