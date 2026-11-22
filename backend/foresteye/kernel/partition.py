"""S1 初始划分：KMeans++ 聚类 → 连通性修复 → 停靠点两阶段匹配（论文 4.1.2 节）。"""
from __future__ import annotations

from collections import Counter, deque

import numpy as np


def kmeanspp(xy, k, seed=7, iters=25, max_sample=9000):
    """KMeans++ 初始化 + Lloyd 迭代；点集过大时先降采样求中心，再全量归属。"""
    rng = np.random.default_rng(seed)
    pts = np.asarray(xy, dtype=float)
    n = len(pts)
    if n == 0:
        return np.zeros(0, dtype=np.int64), np.zeros((k, 2))
    k = int(max(1, min(k, n)))
    sample = pts
    if n > max_sample:
        idx = rng.choice(n, size=max_sample, replace=False)
        sample = pts[idx]

    centers = [sample[rng.integers(len(sample))]]
    d2 = ((sample - centers[0]) ** 2).sum(axis=1)
    for _ in range(1, k):
        total = float(d2.sum())
        if total <= 0:
            centers.append(sample[rng.integers(len(sample))])
        else:
            probs = d2 / total
            centers.append(sample[rng.choice(len(sample), p=probs)])
        d2 = np.minimum(d2, ((sample - centers[-1]) ** 2).sum(axis=1))
    C = np.array(centers, dtype=float)

    for _ in range(iters):
        labels_s = _assign(sample, C)
        newC = C.copy()
        for j in range(k):
            m = labels_s == j
            if m.any():
                newC[j] = sample[m].mean(axis=0)
        if np.allclose(newC, C, atol=1e-6):
            C = newC
            break
        C = newC

    labels = _assign(pts, C)
    return labels, C


def _assign(pts, centers):
    d = ((pts[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
    return d.argmin(axis=1).astype(np.int64)


def repair_connectivity(grid, labels, min_size=12, rounds=2):
    """把小于 min_size 的连通小块并入相邻多数标签，并清理空标签（论文 4.2.3 的孤立块修复）。"""
    labels = np.asarray(labels, dtype=np.int64).copy()
    n = len(labels)
    for _ in range(rounds):
        seen = np.zeros(n, dtype=bool)
        changed = False
        for start in range(n):
            if seen[start]:
                continue
            lab = labels[start]
            comp = []
            dq = deque([start])
            seen[start] = True
            while dq:
                cur = dq.popleft()
                comp.append(cur)
                for nb in grid.neighbors8(cur):
                    if not seen[nb] and labels[nb] == lab:
                        seen[nb] = True
                        dq.append(nb)
            if len(comp) >= min_size:
                continue
            votes = Counter()
            for c in comp:
                for nb in grid.neighbors8(c):
                    if labels[nb] != lab:
                        votes[int(labels[nb])] += 1
            if votes:
                tgt = votes.most_common(1)[0][0]
                for c in comp:
                    labels[c] = tgt
                changed = True
        if not changed:
            break
    # 重排为 0..k-1，并丢掉空标签
    used = np.unique(labels)
    remap = {int(old): new for new, old in enumerate(used)}
    return np.array([remap[int(v)] for v in labels], dtype=np.int64)


def subregion_cells(labels):
    """标签 -> 单元下标列表的列表。"""
    out = {}
    for idx, lab in enumerate(labels):
        out.setdefault(int(lab), []).append(idx)
    return [np.asarray(out[k], dtype=np.int64) for k in sorted(out)]


def dock_candidates(centroids, dock_xy, dist_base, top_m=12):
    """每个子区取欧氏最近 top_m 个可达停靠点（两阶段匹配的第一阶段）。"""
    dock_xy = np.asarray(dock_xy, dtype=float)
    out = []
    for c in centroids:
        d = np.hypot(dock_xy[:, 0] - c[0], dock_xy[:, 1] - c[1])
        order = np.argsort(d)[:top_m]
        out.append([(int(i), float(d[i])) for i in order if np.isfinite(dist_base[i])])
    return out


def assign_docks(cands, dock_xy, labels, grid):
    """贪婪唯一分配：先给选择余地小的子区挑停靠点。返回每个子区的停靠点下标。"""
    m = len(cands)
    if m == 0:
        return []
    flex = [len(c) for c in cands]
    order = sorted(range(m), key=lambda i: flex[i])
    used = set()
    assigned = [-1] * m
    for i in order:
        pick = None
        for j, d in cands[i]:
            if j not in used:
                pick = j
                break
        if pick is None and cands[i]:
            pick = cands[i][0][0]
        assigned[i] = int(pick) if pick is not None else -1
        if pick is not None:
            used.add(pick)
    return assigned
