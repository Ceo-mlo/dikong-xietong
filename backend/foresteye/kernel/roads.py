"""路网图与可达性：几何吸附建图、最短路、候选停靠点筛选。

原始路网是 GIS 导出的独立线要素（相交处未打断、端点常有 10–50 m 缺口），
因此建图时需要「拓扑修补」：
1) 先把各线要素的顶点作为初始节点；
2) 对每个节点，在其邻域内的线段上做投影，若垂足落在段内且距离 ≤ snap_m，
   则在该处打断线段并插入节点（论文 4.1.1 节「映射路网节点 + 连通性检查」的工程实现）；
3) 用并查集合并 2 m 内重合的节点，最后生成邻接表。
"""
from __future__ import annotations

import heapq
import math

import numpy as np

SNAP_M = 45.0
MERGE_M = 2.0


class _UnionFind:
    __slots__ = ("p",)

    def __init__(self, n):
        self.p = list(range(n))

    def find(self, a):
        p = self.p
        root = a
        while p[root] != root:
            root = p[root]
        while p[a] != root:
            p[a], a = root, p[a]
        return root

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra
            return True
        return False


class RoadGraph:
    def __init__(self, nodes, adj, n_edges, total_len, meta=None):
        self.nodes = nodes
        self.adj = adj
        self.n_edges = n_edges
        self.total_len = total_len
        self.meta = meta or {}
        self._dist_cache = {}
        self._mat_cache = {}

    def __len__(self):
        return len(self.nodes)

    def dijkstra(self, src):
        src = int(src)
        hit = self._dist_cache.get(src)
        if hit is not None:
            return hit
        dist = np.full(len(self.nodes), np.inf)
        dist[src] = 0.0
        pq = [(0.0, src)]
        adj = self.adj
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist[u]:
                continue
            for v, w in adj[u]:
                nd = d + w
                if nd < dist[v] - 1e-9:
                    dist[v] = nd
                    heapq.heappush(pq, (nd, v))
        if len(self._dist_cache) < 800:
            self._dist_cache[src] = dist
        return dist

    def paths_from(self, src):
        dist = np.full(len(self.nodes), np.inf)
        prev = np.full(len(self.nodes), -1, dtype=np.int64)
        dist[int(src)] = 0.0
        pq = [(0.0, int(src))]
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist[u]:
                continue
            for v, w in self.adj[u]:
                nd = d + w
                if nd < dist[v] - 1e-9:
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(pq, (nd, v))
        return dist, prev

    def route(self, src, dst):
        src, dst = int(src), int(dst)
        if src == dst:
            return 0.0, [src]
        dist, prev = self.paths_from(src)
        if not np.isfinite(dist[dst]):
            return None, []
        path = [dst]
        guard = 0
        while path[-1] != src and guard <= len(self.nodes):
            nxt = int(prev[path[-1]])
            if nxt < 0:
                return None, []
            path.append(nxt)
            guard += 1
        path.reverse()
        return float(dist[dst]), path

    def distance_matrix(self, ids):
        key = tuple(int(i) for i in ids)
        hit = self._mat_cache.get(key)
        if hit is not None:
            return hit
        n = len(ids)
        mat = np.full((n, n), np.inf)
        for a, i in enumerate(ids):
            d = self.dijkstra(i)
            mat[a, :] = d[np.asarray(ids)]
        mat = np.minimum(mat, mat.T)
        if len(self._mat_cache) < 16:
            self._mat_cache[key] = mat
        return mat

    def nearest_node(self, point, candidates=None):
        pts = self.nodes if candidates is None else self.nodes[candidates]
        d2 = (pts[:, 0] - point[0]) ** 2 + (pts[:, 1] - point[1]) ** 2
        k = int(np.argmin(d2))
        return (k if candidates is None else int(candidates[k])), math.sqrt(float(d2[k]))

    def component_stats(self, sample=4000):
        """连通分量规模（抽样估算，用于导入体检）。"""
        seen = np.zeros(len(self.nodes), dtype=bool)
        sizes = []
        step = max(1, len(self.nodes) // sample)
        for s in range(0, len(self.nodes), step):
            if seen[s]:
                continue
            dq = [s]
            seen[s] = True
            n = 0
            while dq:
                u = dq.pop()
                n += 1
                for v, _ in self.adj[u]:
                    if not seen[v]:
                        seen[v] = True
                        dq.append(v)
            sizes.append(n)
        sizes.sort(reverse=True)
        return {"components_sampled": len(sizes), "largest": sizes[:3]}

    def summary(self):
        return {
            "nodes": int(len(self.nodes)),
            "segments": int(self.n_edges),
            "length_km": round(self.total_len / 1000, 2),
            "mean_segment_m": round(self.total_len / max(1, self.n_edges), 1),
            **self.meta,
        }


def _bucketize(ax, ay, bx, by, cell=60.0):
    """把线段登记到其覆盖的格子里（按 60 m 步长采样）。"""
    buckets = {}
    for e in range(len(ax)):
        x0, y0, x1, y1 = ax[e], ay[e], bx[e], by[e]
        length = math.hypot(x1 - x0, y1 - y0)
        steps = max(1, int(length // cell) + 1)
        for s in range(steps + 1):
            t = s / steps
            key = (int((x0 + (x1 - x0) * t) // cell), int((y0 + (y1 - y0) * t) // cell))
            buckets.setdefault(key, []).append(e)
    return buckets


def build_graph(road_lines, snap_m=SNAP_M, merge_m=MERGE_M):
    coords = []
    index = {}

    def nid(x, y):
        key = (round(float(x), 1), round(float(y), 1))
        k = index.get(key)
        if k is None:
            k = len(coords)
            index[key] = k
            coords.append([key[0], key[1]])
        return k

    raw_edges = set()
    for pts in road_lines:
        prev = None
        for x, y in pts:
            i = nid(x, y)
            if prev is not None and prev != i:
                raw_edges.add((prev, i) if prev < i else (i, prev))
            prev = i

    coords = np.asarray(coords, dtype=float)
    edges = np.asarray(sorted(raw_edges), dtype=np.int64) if raw_edges else np.zeros((0, 2), dtype=np.int64)
    if len(edges) == 0:
        return RoadGraph(coords, [[] for _ in range(len(coords))], 0, 0.0, {"snap_m": snap_m})

    ax = coords[edges[:, 0], 0]
    ay = coords[edges[:, 0], 1]
    bx = coords[edges[:, 1], 0]
    by = coords[edges[:, 1], 1]
    buckets = _bucketize(ax, ay, bx, by)

    # ---- 拓扑修补：把节点投影到邻域线段上，记录打断点 ----
    splits = {}  # edge index -> list[(t, node)]
    cell = 60.0
    cell_of = np.floor(coords / cell).astype(np.int64)
    node_bucket = {}
    for k in range(len(coords)):
        node_bucket.setdefault((int(cell_of[k, 0]), int(cell_of[k, 1])), []).append(k)

    added = 0
    for (bu, bv), node_ids in node_bucket.items():
        cand = []
        for du in (-1, 0, 1):
            for dv in (-1, 0, 1):
                cand.extend(buckets.get((bu + du, bv + dv), ()))
        if not cand:
            continue
        cand = np.asarray(sorted(set(cand)), dtype=np.int64)
        x1, y1 = ax[cand], ay[cand]
        x2, y2 = bx[cand], by[cand]
        dx, dy = x2 - x1, y2 - y1
        seg2 = dx * dx + dy * dy
        seg2[seg2 < 1e-9] = 1e-9
        for k in node_ids:
            px, py = coords[k]
            t = ((px - x1) * dx + (py - y1) * dy) / seg2
            t = np.clip(t, 0.0, 1.0)
            proj_x = x1 + t * dx
            proj_y = y1 + t * dy
            dist = np.hypot(px - proj_x, py - proj_y)
            # 只接受段内投影，且不要落在已有端点附近
            valid = (dist <= snap_m) & (t > 0.02) & (t < 0.98)
            valid &= (dist > merge_m)
            if not valid.any():
                continue
            j = int(np.argmin(np.where(valid, dist, np.inf)))
            e = int(cand[j])
            splits.setdefault(e, []).append((float(t[j]), int(k)))
            added += 1

    # ---- 重建边（含打断） ----
    new_edges = {}
    for e in range(len(edges)):
        a, b = int(edges[e, 0]), int(edges[e, 1])
        pts = splits.get(e)
        if not pts:
            key = (a, b) if a < b else (b, a)
            w = math.hypot(coords[a, 0] - coords[b, 0], coords[a, 1] - coords[b, 1])
            if w < new_edges.get(key, float("inf")):
                new_edges[key] = w
            continue
        chain = [a] + [n for _, n in sorted(pts)] + [b]
        for u, v in zip(chain[:-1], chain[1:]):
            if u == v:
                continue
            key = (u, v) if u < v else (v, u)
            w = math.hypot(coords[u, 0] - coords[v, 0], coords[u, 1] - coords[v, 1])
            if w > 1e-6 and w < new_edges.get(key, float("inf")):
                new_edges[key] = w

    # ---- 邻近吸附：把 snap_m 内的节点并入同一簇（补上要素间的 10–50 m 缺口）----
    uf = _UnionFind(len(coords))
    gaps = []
    for (bu, bv), ids in node_bucket.items():
        for du in (-1, 0, 1):
            for dv in (-1, 0, 1):
                other = node_bucket.get((bu + du, bv + dv))
                if not other:
                    continue
                for i in ids:
                    for j in other:
                        if i >= j:
                            continue
                        d = math.hypot(coords[i, 0] - coords[j, 0], coords[i, 1] - coords[j, 1])
                        if merge_m < d <= snap_m:
                            gaps.append((i, j, d))
                        if d <= snap_m:
                            uf.union(i, j)
    roots = {}
    cluster_of = np.zeros(len(coords), dtype=np.int64)
    sums = []
    for i in range(len(coords)):
        r = uf.find(i)
        c = roots.get(r)
        if c is None:
            c = len(roots)
            roots[r] = c
            sums.append([0.0, 0.0, 0])
        cluster_of[i] = c
        sums[c][0] += coords[i, 0]
        sums[c][1] += coords[i, 1]
        sums[c][2] += 1
    super_xy = np.asarray([[s[0] / s[2], s[1] / s[2]] for s in sums], dtype=float)

    merged_edges = {}
    for (a, b), w in new_edges.items():
        ca, cb = int(cluster_of[a]), int(cluster_of[b])
        if ca == cb:
            continue
        key = (ca, cb) if ca < cb else (cb, ca)
        if w < merged_edges.get(key, float("inf")):
            merged_edges[key] = w
    for i, j, d in gaps:
        ca, cb = int(cluster_of[i]), int(cluster_of[j])
        if ca == cb:
            continue
        key = (ca, cb) if ca < cb else (cb, ca)
        if d < merged_edges.get(key, float("inf")):
            merged_edges[key] = d

    adj = [[] for _ in range(len(super_xy))]
    for (a, b), w in merged_edges.items():
        adj[a].append((b, w))
        adj[b].append((a, w))

    bridges = _connect_components(adj, super_xy)

    return RoadGraph(
        super_xy, adj, len(merged_edges), float(sum(merged_edges.values())),
        {
            "snap_m": snap_m,
            "topology_splits": int(added),
            "snapped_clusters": int(len(coords) - len(super_xy)),
            "bridges": bridges,
        },
    )


def _connect_components(adj, pts, sample=240):
    """把互不连通的路网分量用最近节点对桥接成连通图（最小生成树，避免大规模人工断网）。"""
    n = len(adj)
    comp_id = np.full(n, -1, dtype=np.int64)
    comps = []
    for s in range(n):
        if comp_id[s] >= 0:
            continue
        cid = len(comps)
        stack = [s]
        comp_id[s] = cid
        members = []
        while stack:
            u = stack.pop()
            members.append(u)
            for v, _ in adj[u]:
                if comp_id[v] < 0:
                    comp_id[v] = cid
                    stack.append(v)
        comps.append(np.asarray(members, dtype=np.int64))
    if len(comps) <= 1:
        return 0

    step = max(1, sample)
    picks = [c[:: max(1, len(c) // step)] if len(c) > step else c for c in comps]
    nc = len(comps)
    best = np.full(nc, np.inf)
    best_pair = [None] * nc
    linked = np.zeros(nc, dtype=bool)
    linked[0] = True
    bridges = 0
    for _ in range(nc - 1):
        # 用已连通分量的代表点更新到未连通分量的最近距离
        for a in np.where(linked)[0]:
            pa = pts[picks[a]]
            for b in np.where(~linked)[0]:
                pb = pts[picks[b]]
                d2 = ((pa[:, None, :] - pb[None, :, :]) ** 2).sum(axis=2)
                k = int(np.argmin(d2))
                i, j = divmod(k, d2.shape[1])
                dist = math.sqrt(float(d2[i, j]))
                if dist < best[b]:
                    best[b] = dist
                    best_pair[b] = (int(picks[a][i]), int(picks[b][j]))
        b = int(np.argmin(np.where(~linked, best, np.inf)))
        i, j = best_pair[b]
        adj[i].append((j, best[b]))
        adj[j].append((i, best[b]))
        linked[b] = True
        bridges += 1
    return bridges


def select_docks(graph, region, base_idx=None, tau_dock_m=60000.0, max_docks=280, pad=400.0, bucket_m=200.0):
    """候选停靠点：路网节点中「基地可达（≤τ_dock）且靠近研究区」者，按 200 m 抽稀。"""
    if base_idx is None:
        base_idx = graph.nearest_node(region.base)[0]
    dist_base = graph.dijkstra(base_idx)
    minx, miny, maxx, maxy = region.bbox
    pts = graph.nodes
    inside_box = (
        (pts[:, 0] >= minx - pad) & (pts[:, 0] <= maxx + pad)
        & (pts[:, 1] >= miny - pad) & (pts[:, 1] <= maxy + pad)
    )
    ok = np.where(inside_box & np.isfinite(dist_base) & (dist_base <= tau_dock_m))[0]
    if len(ok) == 0:
        ok = np.where(np.isfinite(dist_base))[0]
    buckets = {}
    for idx in ok:
        key = (int(pts[idx, 0] // bucket_m), int(pts[idx, 1] // bucket_m))
        cur = buckets.get(key)
        if cur is None or dist_base[idx] < dist_base[cur]:
            buckets[key] = idx
    chosen = np.array(sorted(buckets.values()), dtype=np.int64)
    if len(chosen) > max_docks:
        chosen = chosen[np.argsort(dist_base[chosen])]
        step = len(chosen) / max_docks
        chosen = np.array([chosen[min(len(chosen) - 1, int(i * step))] for i in range(max_docks)], dtype=np.int64)
    return chosen, dist_base, base_idx
