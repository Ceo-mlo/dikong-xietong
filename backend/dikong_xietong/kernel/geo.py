"""几何与投影工具：UTM 49N 正反算、点在多边形、凸包、抽稀、旋转。全部仅依赖 numpy。"""
from __future__ import annotations

import math

import numpy as np

# ---- WGS84 椭球与 UTM 49N 参数（与数据源 Build_with_utm_coords.csv 同一套坐标系）----
A = 6378137.0
F = 1 / 298.257223563
E2 = F * (2 - F)
EP2 = E2 / (1 - E2)
K0 = 0.9996
LON0 = 111.0
FE = 500000.0
FN = 0.0


def utm_forward(lon, lat):
    """经纬度 -> UTM 49N（EPSG:32649），Snyder 六阶级数，毫米级。"""
    lon = np.asarray(lon, dtype=float)
    lat = np.asarray(lat, dtype=float)
    phi = np.radians(lat)
    lam = np.radians(lon) - np.radians(LON0)
    e4 = E2 * E2
    e6 = e4 * E2
    sin_phi = np.sin(phi)
    cos_phi = np.cos(phi)
    tan_phi = np.tan(phi)
    n = A / np.sqrt(1 - E2 * sin_phi**2)
    t = tan_phi**2
    c = EP2 * cos_phi**2
    aa = cos_phi * lam
    m = A * (
        (1 - E2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * phi
        - (3 * E2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024) * np.sin(2 * phi)
        + (15 * e4 / 256 + 45 * e6 / 1024) * np.sin(4 * phi)
        - (35 * e6 / 3072) * np.sin(6 * phi)
    )
    x = FE + K0 * n * (
        aa
        + (1 - t + c) * aa**3 / 6
        + (5 - 18 * t + t**2 + 72 * c - 58 * EP2) * aa**5 / 120
    )
    y = FN + K0 * (
        m
        + n * tan_phi * (
            aa**2 / 2
            + (5 - t + 9 * c + 4 * c**2) * aa**4 / 24
            + (61 - 58 * t + t**2 + 600 * c - 330 * EP2) * aa**6 / 720
        )
    )
    return np.stack([x, y], axis=-1)


def utm_inverse(x, y):
    """UTM 49N -> 经纬度（对外导出 WGS84 用）。"""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    e4 = E2 * E2
    e6 = e4 * E2
    e1 = (1 - math.sqrt(1 - E2)) / (1 + math.sqrt(1 - E2))
    xx = x - FE
    yy = y - FN
    mu = yy / K0 / (A * (1 - E2 / 4 - 3 * e4 / 64 - 5 * e6 / 256))
    phi1 = (
        mu
        + (3 * e1 / 2 - 27 * e1**3 / 32) * np.sin(2 * mu)
        + (21 * e1**2 / 16 - 55 * e1**4 / 32) * np.sin(4 * mu)
        + (151 * e1**3 / 96) * np.sin(6 * mu)
        + (1097 * e1**4 / 512) * np.sin(8 * mu)
    )
    sin_phi1 = np.sin(phi1)
    cos_phi1 = np.cos(phi1)
    tan_phi1 = np.tan(phi1)
    c1 = EP2 * cos_phi1**2
    t1 = tan_phi1**2
    n1 = A / np.sqrt(1 - E2 * sin_phi1**2)
    r1 = A * (1 - E2) / (1 - E2 * sin_phi1**2) ** 1.5
    d = xx / (n1 * K0)
    lat = phi1 - (n1 * tan_phi1 / r1) * (
        d**2 / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1**2 - 9 * EP2) * d**4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1**2 - 252 * EP2 - 3 * c1**2) * d**6 / 720
    )
    lon = np.radians(LON0) + (
        d
        - (1 + 2 * t1 + c1) * d**3 / 6
        + (5 - 2 * c1 + 28 * t1 - 3 * c1**2 + 8 * EP2 + 24 * t1**2) * d**5 / 120
    ) / cos_phi1
    return np.stack([np.degrees(lon), np.degrees(lat)], axis=-1)


def point_in_polygon(x, y, poly):
    """射线法（向量化）：x,y 可为标量或同形状数组，poly 为 (n,2) 闭合或非闭合环。"""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    pts = np.asarray(poly, dtype=float)
    if len(pts) >= 3 and (pts[0] != pts[-1]).any():
        pts = np.vstack([pts, pts[:1]])
    inside = np.zeros(np.broadcast(x, y).shape, dtype=bool)
    xb = np.broadcast_to(x, inside.shape)
    yb = np.broadcast_to(y, inside.shape)
    for i in range(len(pts) - 1):
        x1, y1 = pts[i]
        x2, y2 = pts[i + 1]
        if y1 == y2:
            continue
        cond = ((y1 > yb) != (y2 > yb)) & (
            xb < (x2 - x1) * (yb - y1) / (y2 - y1) + x1
        )
        inside ^= cond
    return inside


def points_in_region(x, y, outer, holes):
    """点在有效区域内 = 在外环内且不在任何空洞内。"""
    keep = point_in_polygon(x, y, outer)
    for hole in holes:
        if len(hole) >= 3:
            keep &= ~point_in_polygon(x, y, hole)
    return keep


def polygon_area(poly):
    """鞋带公式，返回绝对面积。"""
    pts = np.asarray(poly, dtype=float)
    if len(pts) < 3:
        return 0.0
    x, y = pts[:, 0], pts[:, 1]
    return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2.0)


def polygon_centroid(poly):
    pts = np.asarray(poly, dtype=float)
    x, y = pts[:, 0], pts[:, 1]
    cross = x * np.roll(y, -1) - np.roll(x, -1) * y
    area = cross.sum() / 2.0
    if abs(area) < 1e-9:
        return tuple(pts.mean(axis=0))
    cx = ((x + np.roll(x, -1)) * cross).sum() / (6 * area)
    cy = ((y + np.roll(y, -1)) * cross).sum() / (6 * area)
    return (float(cx), float(cy))


def convex_hull(points):
    """Andrew 单调链，返回逆时针凸包顶点列表。"""
    pts = sorted({(round(float(p[0]), 3), round(float(p[1]), 3)) for p in points})
    if len(pts) <= 2:
        return [list(p) for p in pts]

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return [list(p) for p in lower[:-1] + upper[:-1]]


def rotate(points, ang, cx, cy):
    ca, sa = math.cos(ang), math.sin(ang)
    arr = np.asarray(points, dtype=float)
    x = arr[:, 0] - cx
    y = arr[:, 1] - cy
    return np.stack([x * ca - y * sa + cx, x * sa + y * ca + cy], axis=-1)


def rotate_about(points, ang, cx, cy, inverse=False):
    if inverse:
        ang = -ang
    ca, sa = math.cos(ang), math.sin(ang)
    arr = np.asarray(points, dtype=float)
    x = arr[:, 0] - cx
    y = arr[:, 1] - cy
    return np.stack([x * ca - y * sa + cx, x * sa + y * ca + cy], axis=-1)


def rdp(points, eps):
    """Douglas-Peucker 折线抽稀（航迹与大文件导出用）。"""
    pts = np.asarray(points, dtype=float)
    if len(pts) < 3:
        return pts.tolist()
    keep = np.zeros(len(pts), dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        s, e = stack.pop()
        if e <= s + 1:
            continue
        p0, p1 = pts[s], pts[e]
        d = p1 - p0
        length = math.hypot(d[0], d[1])
        seg = pts[s + 1:e]
        if length < 1e-9:
            dist = np.hypot(seg[:, 0] - p0[0], seg[:, 1] - p0[1])
        else:
            dist = np.abs(d[0] * (p0[1] - seg[:, 1]) - (p0[0] - seg[:, 0]) * d[1]) / length
        k = int(np.argmax(dist))
        if dist[k] > eps:
            idx = s + 1 + k
            keep[idx] = True
            stack.append((s, idx))
            stack.append((idx, e))
    return pts[keep].tolist()


def path_length(points):
    pts = np.asarray(points, dtype=float)
    if len(pts) < 2:
        return 0.0
    d = np.diff(pts, axis=0)
    return float(np.hypot(d[:, 0], d[:, 1]).sum())


def wkt_linestring(text):
    """解析 'LINESTRING (x y, x y, ...)'，返回 (n,2) 坐标。"""
    start = text.find("(")
    end = text.rfind(")")
    if start < 0 or end < 0:
        return None
    body = text[start + 1:end]
    coords = []
    for pair in body.split(","):
        parts = pair.split()
        if len(parts) < 2:
            continue
        try:
            coords.append((float(parts[0]), float(parts[1])))
        except ValueError:
            continue
    if len(coords) < 2:
        return None
    return np.asarray(coords, dtype=float)
