"""ε-Pareto 归档与前沿后处理（论文 4.2.4 节）。

目标空间是二维的：覆盖率越大越好、总作业时间越小越好。
归档维持一个非支配解集合，并用 ε 网格去重（覆盖率 ε=0.001、时间 ε=60 s）。
"""
from __future__ import annotations

EPS_COV = 0.001
EPS_T = 60.0


def dominates(a, b):
    return (
        a["coverage"] >= b["coverage"] - 1e-12
        and a["t_total_s"] <= b["t_total_s"] + 1e-9
        and (a["coverage"] > b["coverage"] + 1e-12 or a["t_total_s"] < b["t_total_s"] - 1e-9)
    )


def too_close(a, b, eps_cov=EPS_COV, eps_t=EPS_T):
    return abs(a["coverage"] - b["coverage"]) <= eps_cov and abs(a["t_total_s"] - b["t_total_s"]) <= eps_t


def insert(front, cand, eps_cov=EPS_COV, eps_t=EPS_T):
    """尝试把 cand 插入前沿；返回 (front, 是否新增)。"""
    for other in front:
        if dominates(other, cand):
            return front, False
    keep = [o for o in front if not dominates(cand, o)]
    for o in keep:
        if too_close(cand, o, eps_cov, eps_t):
            # ε 邻域内已存在解：保留覆盖更高/时间更短者
            if o["coverage"] >= cand["coverage"] and o["t_total_s"] <= cand["t_total_s"]:
                return keep, False
            keep.remove(o)
            break
    keep.append(cand)
    keep.sort(key=lambda c: (c["coverage"], -c["t_total_s"]))
    return keep, True


def knee_index(front):
    """归一化目标空间里离两端连线最远的点（覆盖-时间权衡的拐点）。"""
    n = len(front)
    if n < 3:
        return None
    cs = [f["coverage"] for f in front]
    ts = [f["t_total_s"] for f in front]
    cmin, cmax = min(cs), max(cs)
    tmin, tmax = min(ts), max(ts)
    dc = max(1e-9, cmax - cmin)
    dt = max(1e-9, tmax - tmin)
    best, best_d = None, -1.0
    for i, f in enumerate(front):
        x = (f["coverage"] - cmin) / dc
        y = 1.0 - (f["t_total_s"] - tmin) / dt
        d = x + y  # 两端连线 x+y=1 的法向距离 ∝ x+y
        if d > best_d:
            best_d, best = d, i
    return best


def annotate(front):
    """给前沿每个解补 MCG（相对更少架次的邻近解）与标记。"""
    ordered = sorted(front, key=lambda f: (f["sorties"], f["t_total_s"]))
    prev = None
    for f in ordered:
        if prev is not None and f["sorties"] != prev["sorties"]:
            dcov = f["coverage"] - prev["coverage"]
            dt = f["t_total_s"] - prev["t_total_s"]
            f["MCG"] = round(dcov / dt * 1e6, 3) if abs(dt) > 1e-6 else None
            f["delta_coverage_pp"] = round(dcov * 100, 2)
            f["delta_time_s"] = round(dt, 1)
        else:
            f.setdefault("MCG", None)
        prev = f
    return ordered


def thin(front, max_size=100):
    """两阶段精简：先按覆盖率均匀分箱，再在箱内保留时间最短者；两端必留。"""
    if len(front) <= max_size:
        return front
    ordered = sorted(front, key=lambda f: f["coverage"])
    keep = {0, len(ordered) - 1}
    bins = max(1, max_size - 2)
    best_in_bin = {}
    for i, f in enumerate(ordered):
        b = min(bins - 1, int(i * bins / len(ordered)))
        cur = best_in_bin.get(b)
        if cur is None or f["t_total_s"] < ordered[cur]["t_total_s"]:
            best_in_bin[b] = i
    keep.update(best_in_bin.values())
    return [ordered[i] for i in sorted(keep)]


def recommendations(front, k=3):
    """Top-K 候选：拐点 / 最大覆盖 / 最短时间（可去重）。"""
    out = []
    seen = set()

    def push(idx, kind, reasons):
        if idx is None or idx in seen or idx >= len(front):
            return
        seen.add(idx)
        f = front[idx]
        out.append({
            "index": idx,
            "plan_id": f.get("id"),
            "kind": kind,
            "reasons": reasons,
            "reason": "；".join(reasons),
            "coverage": f["coverage"],
            "t_total_s": f["t_total_s"],
            "sorties": f["sorties"],
        })

    ordered = sorted(range(len(front)), key=lambda i: (front[i]["coverage"], -front[i]["t_total_s"]))
    knee = knee_index(front)
    if knee is not None:
        f = front[knee]
        best = front[ordered[-1]] if ordered else f
        dpp = (best["coverage"] - f["coverage"]) * 100.0
        dh = (best["t_total_s"] - f["t_total_s"]) / 3600.0
        dsortie = best["sorties"] - f["sorties"]
        push(knee, "knee", [
            f"覆盖率 {f['coverage']*100:.2f}%、总时间 {f['t_total_s']/3600:.2f} h、{f['sorties']} 架次",
            f"相对全覆盖方案少 {dsortie} 架次、省 {dh:.2f} h，覆盖率仅低 {dpp:.2f} 个百分点",
            "拐点之后每提高 1 个百分点覆盖率所需架次显著上升",
        ])
    if ordered:
        f = front[ordered[-1]]
        push(ordered[-1], "max_coverage", [
            f"覆盖率最高 {f['coverage']*100:.2f}%，覆盖单元 {f.get('coverage_cells', 0)} 个",
            f"{f['sorties']} 架次、总时间 {f['t_total_s']/3600:.2f} h",
            "适合疫点普查类任务，接受架次与时间增加",
        ])
        f = front[ordered[0]]
        push(ordered[0], "min_time", [
            f"总时间最短 {f['t_total_s']/3600:.2f} h、{f['sorties']} 架次",
            f"覆盖率 {f['coverage']*100:.2f}%、覆盖效率 CE={f.get('CE', 0):.1f} s/km²",
            "适合日常巡查，覆盖率按档位目标控制",
        ])
    # 平衡解：覆盖率与时间归一化后取最优
    cs = [f["coverage"] for f in front]
    ts = [f["t_total_s"] for f in front]
    dc = max(1e-9, max(cs) - min(cs))
    dt = max(1e-9, max(ts) - min(ts))
    best, best_v = None, -1e18
    for i, f in enumerate(front):
        v = (f["coverage"] - min(cs)) / dc - (f["t_total_s"] - min(ts)) / dt
        if v > best_v:
            best_v, best = v, i
    if best is not None:
        f = front[best]
        push(best, "balanced", [
            f"覆盖率 {f['coverage']*100:.2f}%、总时间 {f['t_total_s']/3600:.2f} h",
            f"覆盖效率 CE={f.get('CE', 0):.1f} s/km²，位于两目标归一化后的折中点",
        ])
    return out[:k]
