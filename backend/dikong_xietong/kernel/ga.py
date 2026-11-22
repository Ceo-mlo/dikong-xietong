"""S2 航向角优化：双适应度遗传算法（论文 4.1.4 节）。

染色体为 8 位二进制航向角（0–179°，步长 1.41°）；两个适应度分别对应
「覆盖率优先」与「作业时间优先」，用于生成初始帕累托集的两端。
"""
from __future__ import annotations

import math

import numpy as np

from . import sortie

BITS = 8


def _decode(bits):
    v = 0
    for b in bits:
        v = (v << 1) | int(b)
    return v % 180


def _encode(deg, rng):
    v = int(deg) % 180
    return [(v >> (BITS - 1 - i)) & 1 for i in range(BITS)]


def heading_ga(cells_xy, dock_xy, mode="coverage", pop=10, gens=12, seed=0, max_range=sortie.MAX_RANGE):
    """返回 (最佳航向角, 该航向下的仿真结果, 评估次数)。"""
    cells_xy = np.asarray(cells_xy, dtype=float)
    m = len(cells_xy)
    if m == 0:
        return 0.0, sortie._empty_result(0.0, dock_xy), 0

    cache = {}

    def sim(yaw):
        key = int(round(yaw)) % 180
        hit = cache.get(key)
        if hit is None:
            hit = sortie.simulate_sortie(cells_xy, dock_xy, key, max_range)
            cache[key] = hit
        return hit

    def fitness(yaw, res):
        cov = res["coverage"] / m
        if mode == "time":
            # 时间优先：时间越短越好，覆盖率作为可行性罚项
            return -(res["t_uav_s"]) + 1e6 * cov
        if cov < 0.995:
            return cov
        return 1.0 + 0.002 * (2000.0 - res["t_uav_s"]) / 2000.0

    rng = np.random.default_rng(seed)
    # 种子：0/90 两个工程常用角 + 若干随机个体
    seeds = [_encode(0, rng), _encode(90, rng)]
    while len(seeds) < pop:
        seeds.append([int(b) for b in rng.integers(0, 2, BITS)])

    population = seeds[:pop]
    best = None
    evals = 0
    for _ in range(gens):
        scored = []
        for indiv in population:
            yaw = _decode(indiv)
            res = sim(yaw)
            evals += 1
            fit = fitness(yaw, res)
            scored.append((fit, indiv, yaw, res))
            if best is None or fit > best[0]:
                best = (fit, yaw, res)
        if mode == "coverage":
            scored.sort(key=lambda t: (-t[0], t[3]["t_uav_s"]))
        else:
            scored.sort(key=lambda t: -t[0])
        # 精英保留 1 个
        newpop = [list(scored[0][1])]
        fits = np.array([max(1e-6, s[0] - min(0.0, scored[-1][0])) for s in scored])
        probs = fits / fits.sum()
        while len(newpop) < pop:
            a = population[_roulette(probs, rng)]
            b = population[_roulette(probs, rng)]
            child = _crossover(a, b, rng)
            child = _mutate(child, rng, rate=0.12)
            newpop.append(child)
        population = newpop
    yaw = best[1]
    res = best[2]
    return float(yaw), res, evals


def _roulette(probs, rng):
    return int(rng.choice(len(probs), p=probs))


def _crossover(a, b, rng, p=0.7):
    if rng.random() > p:
        return list(a)
    cut = int(rng.integers(1, BITS))
    return list(a[:cut]) + list(b[cut:])


def _mutate(indiv, rng, rate=0.12):
    return [1 - g if rng.random() < rate else g for g in indiv]


def coarse_scan(cells_xy, dock_xy, step=15, max_range=sortie.MAX_RANGE):
    """粗扫描（确定性兜底）：返回覆盖最优 / 时间最优两个航向。"""
    best_cov = None
    best_time = None
    for yaw in range(0, 180, step):
        r = sortie.simulate_sortie(cells_xy, dock_xy, yaw, max_range)
        key_cov = (r["coverage"], -r["t_uav_s"])
        if best_cov is None or key_cov > best_cov[0]:
            best_cov = (key_cov, yaw, r)
        key_t = (-r["t_uav_s"], r["coverage"])
        if best_time is None or key_t > best_time[0]:
            best_time = (key_t, yaw, r)
    return best_cov[1], best_time[1]
