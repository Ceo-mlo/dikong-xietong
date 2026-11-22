"""区域（研究区）装配：读取外环 / 空洞 / 路网原始文件，投影到 UTM 49N，落盘为 JSON。"""
from __future__ import annotations

import csv
import json
import os
import time
from dataclasses import dataclass, field

import numpy as np

from . import geo

_REL_SOURCE_DIR = os.path.join("tdp软件依赖的模型", "数据与代码", "pyworkspace", "Canshu")
_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
_BUNDLED_SOURCE_DIR = os.path.join(_REPO_ROOT, "data", "source")


def _resolve_source_dir() -> str:
    """优先使用仓库内置 data/source，其次从当前目录向上找外部数据目录。"""
    env = os.environ.get("FORESTEYE_SOURCE_DIR")
    if env and os.path.isdir(env):
        return env
    if os.path.isdir(_BUNDLED_SOURCE_DIR):
        return _BUNDLED_SOURCE_DIR
    here = os.path.dirname(os.path.abspath(__file__))
    for base in (os.getcwd(), here):
        probe = base
        for _ in range(6):
            cand = os.path.join(probe, _REL_SOURCE_DIR)
            if os.path.isdir(cand):
                return cand
            parent = os.path.dirname(probe)
            if parent == probe:
                break
            probe = parent
    return _REL_SOURCE_DIR


DEFAULT_SOURCE_DIR = _resolve_source_dir()
DEFAULT_BASE_LONLAT = (112.23460820305844, 24.38383465365925)  # 试验林场车库 / 车辆基地


@dataclass
class Region:
    id: str
    name: str
    outer: np.ndarray
    holes: list = field(default_factory=list)
    roads: list = field(default_factory=list)
    base: np.ndarray = field(default_factory=lambda: np.zeros(2))
    base_lonlat: tuple = DEFAULT_BASE_LONLAT
    sources: dict = field(default_factory=dict)
    created_at: str = ""

    # -- 派生量 -------------------------------------------------------------
    @property
    def bbox(self):
        pts = self.outer
        return (
            float(pts[:, 0].min()),
            float(pts[:, 1].min()),
            float(pts[:, 0].max()),
            float(pts[:, 1].max()),
        )

    @property
    def area_m2(self):
        area = geo.polygon_area(self.outer)
        for hole in self.holes:
            area -= geo.polygon_area(hole)
        return max(area, 0.0)

    def summary(self):
        minx, miny, maxx, maxy = self.bbox
        return {
            "id": self.id,
            "name": self.name,
            "vertices": int(len(self.outer)),
            "holes": len(self.holes),
            "roads": len(self.roads),
            "area_m2": round(self.area_m2, 1),
            "area_ha": round(self.area_m2 / 1e4, 1),
            "area_km2": round(self.area_m2 / 1e6, 3),
            "bbox": [round(v, 1) for v in self.bbox],
            "extent_km": [round((maxx - minx) / 1000, 2), round((maxy - miny) / 1000, 2)],
            "base": [round(float(self.base[0]), 2), round(float(self.base[1]), 2)],
            "base_lonlat": list(self.base_lonlat),
            "sources": dict(self.sources),
            "created_at": self.created_at,
        }

    # -- 序列化 -------------------------------------------------------------
    def to_dict(self, with_roads=True):
        data = {
            "id": self.id,
            "name": self.name,
            "outer": [[round(float(x), 2), round(float(y), 2)] for x, y in self.outer],
            "holes": [
                [[round(float(x), 2), round(float(y), 2)] for x, y in hole]
                for hole in self.holes
            ],
            "base": [round(float(self.base[0]), 2), round(float(self.base[1]), 2)],
            "base_lonlat": list(self.base_lonlat),
            "sources": dict(self.sources),
            "created_at": self.created_at,
        }
        if with_roads:
            data["roads"] = [
                [[round(float(x), 1), round(float(y), 1)] for x, y in line]
                for line in self.roads
            ]
        return data

    @classmethod
    def from_dict(cls, data):
        return cls(
            id=data["id"],
            name=data["name"],
            outer=np.asarray(data["outer"], dtype=float),
            holes=[np.asarray(h, dtype=float) for h in data.get("holes", [])],
            roads=[np.asarray(r, dtype=float) for r in data.get("roads", [])],
            base=np.asarray(data["base"], dtype=float),
            base_lonlat=tuple(data.get("base_lonlat", DEFAULT_BASE_LONLAT)),
            sources=dict(data.get("sources", {})),
            created_at=data.get("created_at", ""),
        )


# ---------------------------------------------------------------------------
# 解析原始文件
# ---------------------------------------------------------------------------
def _read_excel_polygons(path, group_col=1):
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    wb.close()
    groups = {}
    order = []
    for row in rows:
        if len(row) <= max(group_col, 3):
            continue
        lon, lat = row[2], row[3]
        if lon is None or lat is None:
            continue
        key = row[group_col]
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append((float(lon), float(lat)))
    return [groups[k] for k in order]


def _read_roads(path, bbox_pad=2500.0, bbox=None):
    lines = []
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw = row.get("geometry") or ""
            arr = geo.wkt_linestring(raw)
            if arr is None:
                continue
            if bbox is not None:
                minx, miny, maxx, maxy = bbox
                if arr[:, 0].max() < minx - bbox_pad or arr[:, 0].min() > maxx + bbox_pad:
                    continue
                if arr[:, 1].max() < miny - bbox_pad or arr[:, 1].min() > maxy + bbox_pad:
                    continue
            lines.append(arr)
    return lines


def load_from_files(name, outer_path, holes_path, road_path, base_lonlat=None):
    """读取三个原始文件并装配 Region；返回 (region|None, issues)。"""
    issues = []
    base_lonlat = base_lonlat or DEFAULT_BASE_LONLAT

    # 外环
    polys = _read_excel_polygons(outer_path)
    if not polys:
        issues.append({"code": "D-REG-01", "level": "blocker", "message": "外环文件无可解析顶点"})
        return None, issues
    outer_ll = max(polys, key=len)
    if len(outer_ll) < 3:
        issues.append({"code": "D-REG-02", "level": "blocker", "message": "外环顶点少于 3 个"})
        return None, issues
    outer = geo.utm_forward([p[0] for p in outer_ll], [p[1] for p in outer_ll])
    if len(outer) > 3 and np.allclose(outer[0], outer[-1]):
        outer = outer[:-1]
    area_outer = geo.polygon_area(outer)
    if area_outer < 1e5:
        issues.append({"code": "D-REG-03", "level": "blocker", "message": f"外环面积异常（{area_outer:.0f} m²）"})

    # 空洞
    holes = []
    if holes_path and os.path.exists(holes_path):
        hole_polys = _read_excel_polygons(holes_path)
        for hp in hole_polys:
            if len(hp) < 3:
                continue
            arr = geo.utm_forward([p[0] for p in hp], [p[1] for p in hp])
            if len(arr) > 3 and np.allclose(arr[0], arr[-1]):
                arr = arr[:-1]
            holes.append(arr)
    if not holes:
        issues.append({"code": "D-HOLE-01", "level": "warning", "message": "无空洞（非监测区域），按外环全域计算"})

    # 路网
    minx, miny, maxx, maxy = (
        float(outer[:, 0].min()),
        float(outer[:, 1].min()),
        float(outer[:, 0].max()),
        float(outer[:, 1].max()),
    )
    roads = _read_roads(road_path, bbox=(minx, miny, maxx, maxy)) if road_path and os.path.exists(road_path) else []
    if not roads:
        issues.append({"code": "D-ROAD-01", "level": "blocker", "message": "路网为空或全部落在研究区外"})

    base = geo.utm_forward([base_lonlat[0]], [base_lonlat[1]])[0]
    region = Region(
        id=_slug(name),
        name=name,
        outer=outer,
        holes=holes,
        roads=roads,
        base=base,
        base_lonlat=tuple(base_lonlat),
        sources={
            "outer": os.path.basename(outer_path),
            "holes": os.path.basename(holes_path) if holes_path else "",
            "roads": os.path.basename(road_path) if road_path else "",
        },
        created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )

    # 校验：外环自相交（快速三角形检测，仅报告）
    if _self_intersects(outer):
        issues.append({"code": "D-REG-04", "level": "warning", "message": "外环存在自相交，已按奇偶规则计算覆盖"})
    if len(holes) > 0 and any(geo.polygon_area(h) <= 0 for h in holes):
        issues.append({"code": "D-HOLE-02", "level": "warning", "message": "存在退化空洞（面积 0）"})
    issues.append(
        {
            "code": "OK",
            "level": "info",
            "message": f"装配完成：有效面积 {region.area_m2/1e4:.1f} 公顷，空洞 {len(holes)} 个，路网 {len(roads)} 段",
        }
    )
    return region, issues


def _slug(text):
    keep = "".join(ch for ch in text if ch.isalnum())
    return (keep or "region")[:24]


def _self_intersects(poly):
    pts = np.asarray(poly, dtype=float)
    n = len(pts)
    if n < 4:
        return False
    sample = range(0, n, max(1, n // 260))
    segs = [(i, (i + 1) % n) for i in sample]
    for a in range(len(segs)):
        for b in range(a + 2, len(segs)):
            i, j = segs[a]
            k, l = segs[b]
            if len({i, j, k, l}) < 4:
                continue
            if _seg_cross(pts[i], pts[j], pts[k], pts[l]):
                return True
    return False


def _seg_cross(p1, p2, p3, p4):
    def orient(a, b, c):
        v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        return 0 if abs(v) < 1e-12 else (1 if v > 0 else -1)

    o1, o2 = orient(p1, p2, p3), orient(p1, p2, p4)
    o3, o4 = orient(p3, p4, p1), orient(p3, p4, p2)
    return o1 != o2 and o3 != o4


# ---------------------------------------------------------------------------
# 存储
# ---------------------------------------------------------------------------
def save_region(region, data_dir):
    os.makedirs(os.path.join(data_dir, "regions"), exist_ok=True)
    path = os.path.join(data_dir, "regions", f"{region.id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(region.to_dict(), f, ensure_ascii=False, separators=(",", ":"))
    return path


def load_region(region_id, data_dir):
    path = os.path.join(data_dir, "regions", f"{region_id}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return Region.from_dict(json.load(f))


def list_regions(data_dir):
    folder = os.path.join(data_dir, "regions")
    if not os.path.isdir(folder):
        return []
    out = []
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as f:
                data = json.load(f)
            out.append(
                {
                    "id": data["id"],
                    "name": data["name"],
                    "vertices": len(data.get("outer", [])),
                    "holes": len(data.get("holes", [])),
                    "roads": len(data.get("roads", [])),
                    "created_at": data.get("created_at", ""),
                    "sources": data.get("sources", {}),
                }
            )
        except Exception:  # 损坏文件不应阻断列表
            continue
    return out
