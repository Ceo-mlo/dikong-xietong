"""场景（参数集 + 数据 + 求解策略）与哈希指纹。

对应功能 D2（参数集与场景）与 F-D2-04（场景组装 + scenario_hash）。
参数默认值来自论文表 5.1 与 `Canshu/canshu.py`，单位一律写进键名。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

PARAM_DEFAULTS: dict[str, dict[str, Any]] = {
    "uav": {
        "battery_j": 277200.0,
        "max_range_m": 24840.0,
        "safe_factor": 0.8,
        "speed_mps": 15.0,
        "prepare_s": 300.0,
        "hover_s_per_photo": 1.0,
        "max_cells_per_sortie": 469,
    },
    "camera": {
        "heading_overlap": 0.7,
        "side_overlap": 0.4,
        "swath_m": 250.0,
        "photo_step_m": 105.0,
        "altitude_m": 120.0,
    },
    "vehicle": {
        "speed_kph": 39.6,
        "speed_mps": 11.0,
        "max_drive_time_s": 7200.0,
    },
    "solver": {
        "grid_res_m": 100.0,
        "sortie_min": 40,
        "sortie_max": 80,
        "max_outer": 20,
        "seeds_per_outer": 4,
        "stop_no_improve": 10,
        "stop_ce_gain": 0.001,
        "eps_coverage": 0.001,
        "eps_time_s": 60.0,
        "k_ucb": 1.0,
        "softmax_tau": 0.7,
        "lr": 0.4,
        "seed": 2024,
        "mode": "standard",
        "max_plans": 40,
        "ladder_steps": 4,
    },
}

PARAM_BOUNDS: dict[str, tuple[float, float]] = {
    "uav.battery_j": (1.0e5, 5.0e5),
    "uav.max_range_m": (5.0e3, 6.0e4),
    "uav.safe_factor": (0.5, 1.0),
    "uav.speed_mps": (5.0, 25.0),
    "uav.prepare_s": (60.0, 900.0),
    "camera.heading_overlap": (0.5, 0.95),
    "camera.side_overlap": (0.2, 0.9),
    "camera.swath_m": (100.0, 600.0),
    "camera.photo_step_m": (50.0, 300.0),
    "vehicle.speed_kph": (10.0, 80.0),
    "solver.grid_res_m": (10.0, 200.0),
    "solver.sortie_min": (1.0, 400.0),
    "solver.sortie_max": (1.0, 600.0),
    "solver.max_outer": (1.0, 60.0),
    "solver.seeds_per_outer": (1.0, 16.0),
    "solver.max_plans": (4.0, 100.0),
}

MODE_PRESETS: dict[str, dict[str, Any]] = {
    "quick": {
        "mode": "quick",
        "sortie_min": 40,
        "sortie_max": 80,
        "ladder_steps": 3,
        "max_outer": 4,
        "seeds_per_outer": 2,
        "max_plans": 24,
    },
    "standard": {
        "mode": "standard",
        "sortie_min": 40,
        "sortie_max": 100,
        "ladder_steps": 4,
        "max_outer": 20,
        "seeds_per_outer": 4,
        "max_plans": 40,
    },
}


def _flat(params: dict[str, dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for group, items in params.items():
        for k, v in items.items():
            out[f"{group}.{k}"] = v
    return out


def default_params() -> dict[str, dict[str, Any]]:
    """深拷贝默认参数。"""
    return json.loads(json.dumps(PARAM_DEFAULTS))


def validate(params: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """校验范围与类型，返回问题列表（对应 F-D2-02 的 27 项校验的核心项）。"""
    issues = []
    flat = _flat(params)
    for key, (lo, hi) in PARAM_BOUNDS.items():
        if key not in flat:
            issues.append({"level": "error", "code": "D-PAR-01", "field": key,
                           "msg": "缺少必填参数"})
            continue
        try:
            v = float(flat[key])
        except (TypeError, ValueError):
            issues.append({"level": "error", "code": "D-PAR-02", "field": key,
                           "msg": "参数必须是数值"})
            continue
        if not (lo <= v <= hi):
            issues.append({"level": "error", "code": "D-PAR-03", "field": key,
                           "msg": f"取值应在 [{lo:g}, {hi:g}]"})
    solver = params.get("solver", {})
    if solver.get("sortie_min") and solver.get("sortie_max"):
        if float(solver["sortie_min"]) > float(solver["sortie_max"]):
            issues.append({"level": "error", "code": "D-PAR-04", "field": "solver.sortie_min",
                           "msg": "架次下限不能大于上限"})
    cam = params.get("camera", {})
    if cam.get("swath_m") and cam.get("photo_step_m"):
        if float(cam["photo_step_m"]) > float(cam["swath_m"]):
            issues.append({"level": "warning", "code": "D-PAR-05", "field": "camera.photo_step_m",
                           "msg": "拍照步长大于旁向间距，可能出现漏拍"})
    if not issues:
        issues.append({"level": "info", "code": "OK", "field": "-", "msg": "参数校验通过"})
    return issues


def merge_params(base: dict[str, dict[str, Any]], patch: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """接受扁平或分组两种写法的补丁。"""
    out = json.loads(json.dumps(base))
    for key, value in (patch or {}).items():
        if isinstance(value, dict):
            out.setdefault(key, {}).update(value)
        elif "." in key:
            group, sub = key.split(".", 1)
            out.setdefault(group, {})[sub] = value
        else:
            out.setdefault("solver", {})[key] = value
    return out


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def fingerprint(params: dict[str, dict[str, Any]], extra: dict[str, Any] | None = None) -> str:
    """scenario_hash = sha256(canonical_json(params + 数据指纹 + 引擎版本))。"""
    body = {"params": params, "extra": extra or {}, "engine": ENGINE_VERSION}
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


ENGINE_VERSION = "地空协同林业监测决策系统 1.0.0"


def to_cfg(params: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """把分组参数压成内核 SolveConfig 需要的扁平字典。"""
    solver = dict(params.get("solver", {}))
    cfg = {
        "quick": solver.get("mode") == "quick",
        "max_outer": int(solver.get("max_outer", 20)),
        "seeds_per_outer": int(solver.get("seeds_per_outer", 4)),
        "stop_no_improve": int(solver.get("stop_no_improve", 10)),
        "stop_ce_gain": float(solver.get("stop_ce_gain", 0.001)),
        "max_plans": int(solver.get("max_plans", 40)),
        "ladder_steps": int(solver.get("ladder_steps", 4)),
        "seed": int(solver.get("seed", 2024)),
        "sortie_min": int(solver.get("sortie_min", 40)),
        "sortie_max": int(solver.get("sortie_max", 100)),
    }
    return cfg
