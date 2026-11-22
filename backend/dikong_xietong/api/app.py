"""地空协同林业监测决策系统 HTTP 服务（标准库 http.server + numpy）。

路由（对应架构/04 第 3 节的子集）：
  GET  /api/health
  GET  /api/scenario            任务区域、路网采样、停靠点、默认参数
  GET  /api/params              参数默认值与取值域
  POST /api/jobs                提交求解作业（202 + job_id）
  GET  /api/jobs                作业列表
  GET  /api/jobs/<id>           作业状态与结果摘要
  GET  /api/jobs/<id>/events    事件增量（轮询）
  GET  /api/jobs/<id>/stream    SSE 事件流
  POST /api/jobs/<id>/cancel    取消作业
  GET  /api/jobs/<id>/plans/<pid>            方案完整快照
  GET  /api/jobs/<id>/export/<pid>.<ext>     方案导出（geojson/json/kml/csv）
  GET  /api/jobs/<id>/export/plans.csv       前沿总表
  POST /api/decisions           采纳/驳回留痕
  GET  /api/decisions           留痕查询
  POST /api/report              Word 报告
  GET  /                       前端页面
"""
from __future__ import annotations

import json
import mimetypes
import os
import sys
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

if __package__ in (None, ""):
    # 允许直接运行本文件：python backend/dikong_xietong/api/app.py
    _BACKEND = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if _BACKEND not in sys.path:
        sys.path.insert(0, _BACKEND)
    __package__ = "dikong_xietong.api"

import numpy as np

from ..kernel import archive, pipeline, scenario as scenario_mod
from ..kernel.model import Prepared
from ..kernel.region import load_from_files, DEFAULT_SOURCE_DIR, DEFAULT_BASE_LONLAT
from . import export

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
WEB_DIR = os.path.join(ROOT, "web")
DATA_DIR = os.path.join(ROOT, "data")

SOURCE_FILES = {
    "outer": os.path.join(DEFAULT_SOURCE_DIR, "zgz_point.xlsx"),
    "holes": os.path.join(DEFAULT_SOURCE_DIR, "kongdong_12.xlsx"),
    "roads": os.path.join(DEFAULT_SOURCE_DIR, "Build_with_utm_coords.csv"),
}


# ---------------------------------------------------------------- 作业管理
STAGE_WEIGHT = {"S0": (0.02, 0.10), "S1": (0.10, 0.20), "S2": (0.20, 0.42),
                "S3": (0.42, 0.46), "S4": (0.46, 0.90), "S5": (0.90, 0.96),
                "S6": (0.96, 1.00)}


class Job:
    def __init__(self, params: dict, mode: str):
        self.id = uuid.uuid4().hex[:12]
        self.params = params
        self.mode = mode
        self.status = "queued"          # queued/running/done/failed/canceled
        self.stage = None
        self.progress = 0.0
        self.events: list[dict] = []
        self.logs: list[dict] = []
        self.plans: list[dict] = []
        self.recommendations: list[dict] = []
        self.meta: dict = {}
        self.error: str | None = None
        self.created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.started_at = self.finished_at = None
        self.cancel = threading.Event()
        self.lock = threading.Lock()
        self.scenario_hash = scenario_mod.fingerprint(params, {"source": "zhaigang"})

    # -- 事件 --
    def push(self, ev: dict):
        ev = dict(ev)
        ev.setdefault("ts", time.time())
        with self.lock:
            ev["seq"] = len(self.events)
            self.events.append(ev)
            if (ev.get("type") == "log" or ev.get("stage")) and ev.get("msg"):
                self.logs.append({"stage": ev.get("stage") or ev.get("type"),
                                  "msg": ev["msg"],
                                  "ts": time.strftime("%H:%M:%S", time.localtime(ev["ts"]))})
                if len(self.logs) > 400:
                    del self.logs[:100]
        trace = ev.get("trace") or ev.get("stage")
        if trace:
            self.stage = trace
        if "progress" in ev:
            self.progress = max(self.progress, float(ev["progress"]))
        elif trace in STAGE_WEIGHT:
            lo, hi = STAGE_WEIGHT[trace]
            frac = 0.0
            if trace == "S4":
                outer = ev.get("outer")
                total_outer = ev.get("outer_total") or 20
                frac = min(1.0, float(outer) / max(1, total_outer)) if outer else 0.15
            elif ev.get("type") == "point":
                frac = 0.5
            self.progress = max(self.progress, lo + (hi - lo) * frac)

    def summary(self) -> dict:
        return {
            "job_id": self.id, "status": self.status, "mode": self.mode,
            "stage": self.stage, "progress": round(self.progress, 4),
            "created_at": self.created_at, "started_at": self.started_at,
            "finished_at": self.finished_at, "error": self.error,
            "scenario_hash": self.scenario_hash,
            "plans": len(self.plans),
            "elapsed_s": self.meta.get("elapsed_s"),
        }

    def result(self, scenario_slim: dict) -> dict:
        plans = []
        for p in self.plans:
            light = {k: p.get(k) for k in
                     ("id", "tag", "coverage", "t_total_s", "t_uav_s", "t_car_s", "sorties",
                      "CE", "AUT", "RUR", "ELR", "ALD", "MCG", "delta_coverage_pp",
                      "delta_time_s", "area_km2", "cells", "coverage_cells", "is_knee",
                      "t_fly_s", "t_hover_s", "t_prep_s", "route_m", "exhausted_sorties")}
            light["dock_count"] = len({s["dock"] for s in p["subs"]})
            light["exhausted_count"] = p.get("exhausted_sorties") or 0
            plans.append(light)
        return {"job": self.summary(), "plans": plans,
                "recommendations": self.recommendations,
                "meta": self.meta, "logs": self.logs[-80:],
                "scenario": scenario_slim}


class Store:
    def __init__(self):
        self.region = None
        self._prepared: dict[float, Prepared] = {}
        self.jobs: dict[str, Job] = {}
        self.order: list[str] = []
        self.decisions: list[dict] = []
        self.lock = threading.Lock()
        self.started = time.time()
        self.issues: list[dict] = []

    # -- 数据准备 --
    def ensure_region(self):
        if self.region is not None:
            return self.region
        region, issues = load_from_files(
            "寨岗镇疫区", SOURCE_FILES["outer"], SOURCE_FILES["holes"], SOURCE_FILES["roads"],
            base_lonlat=DEFAULT_BASE_LONLAT)
        self.region = region
        self.issues = issues
        return region

    def prepared(self, res: float) -> Prepared:
        key = float(res)
        if key not in self._prepared:
            region = self.ensure_region()
            self._prepared[key] = pipeline.build_prepared(region, key)
        return self._prepared[key]

    def scenario_payload(self) -> dict:
        region = self.ensure_region()
        prep = self.prepared(scenario_mod.PARAM_DEFAULTS["solver"]["grid_res_m"])
        outer = [[round(float(x), 2), round(float(y), 2)] for x, y in region.outer]
        holes = [[[round(float(x), 2), round(float(y), 2)] for x, y in ring]
                 for ring in region.holes]
        docks = [{"id": int(i), "x": round(float(xy[0]), 1), "y": round(float(xy[1]), 1),
                  "dist_base_m": round(float(d), 1)}
                 for i, (xy, d) in enumerate(zip(prep.dock_xy, prep.dist_base))]
        roads = []
        stride = max(1, len(region.roads) // 1200)
        for line in region.roads[::stride]:
            if len(line) < 2:
                continue
            roads.append([[round(float(line[0][0]), 1), round(float(line[0][1]), 1)],
                          [round(float(line[-1][0]), 1), round(float(line[-1][1]), 1)]])
        return {
            "region": {
                "id": region.id, "name": region.name,
                "area_km2": round(region.area_m2 / 1e6, 3),
                "area_ha": round(region.area_m2 / 1e4, 1),
                "vertices": len(region.outer),
                "holes_count": len(region.holes),
                "roads_count": len(region.roads),
                "bbox": [round(float(v), 1) for v in region.bbox],
                "base": [round(float(region.base[0]), 1), round(float(region.base[1]), 1)],
                "base_lonlat": list(region.base_lonlat),
                "outer": outer, "holes": holes, "docks": docks, "roads": roads,
            },
            "grid": prep.grid.summary(),
            "graph": prep.graph.summary(),
            "params": scenario_mod.default_params(),
            "param_bounds": {k: list(v) for k, v in scenario_mod.PARAM_BOUNDS.items()},
            "modes": scenario_mod.MODE_PRESETS,
            "engine": scenario_mod.ENGINE_VERSION,
            "issues": self.issues,
            "cached_grids": sorted(self._prepared.keys()),
        }

    # -- 作业 --
    def submit(self, params: dict, mode: str) -> Job:
        job = Job(params, mode)
        with self.lock:
            self.jobs[job.id] = job
            self.order.insert(0, job.id)
        thread = threading.Thread(target=self._run, args=(job,), daemon=True)
        thread.start()
        return job

    def _run(self, job: Job):
        job.status = "running"
        job.started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        t0 = time.time()

        def emit(ev: dict):
            ev = dict(ev)
            ev.setdefault("type", "log")
            job.push(ev)

        try:
            res = float(job.params.get("solver", {}).get("grid_res_m", 100.0))
            job.push({"type": "log", "stage": "S0", "trace": "S0", "progress": 0.02,
                      "msg": f"装配区域与预处理（网格 {res:.0f} m）"})
            prep = self.prepared(res)
            job.push({"type": "log", "stage": "S0", "trace": "S0", "progress": 0.08,
                      "msg": f"预处理完成：{prep.grid.summary()['cells']} 单元 / "
                             f"{len(prep.docks)} 停靠点 / {prep.prepare_ms} ms"})
            cfg = scenario_mod.to_cfg(job.params)
            out = pipeline.solve(prep, cfg, emit=emit,
                                 should_stop=job.cancel.is_set, t0=t0)
            job.plans = out["plans"]
            for p in job.plans:
                p.update({k: p["totals"][k] for k in
                          ("t_fly_s", "t_hover_s", "t_prep_s", "route_m", "flight_m",
                           "empty_leg_m", "exhausted_sorties", "coverage_cells")})
            job.recommendations = out["recommendations"]
            knee = None
            for rec in out["recommendations"]:
                if rec["kind"] == "knee":
                    knee = rec["index"]
            for i, p in enumerate(job.plans):
                p["is_knee"] = (i == knee)
            job.meta = dict(out["meta"])
            job.meta["history"] = out.get("history") or []
            job.status = "done"
            job.progress = 1.0
            job.push({"type": "done", "trace": "S6", "progress": 1.0,
                      "msg": f"完成：{len(job.plans)} 个方案，用时 {job.meta.get('elapsed_s')} s"})
        except Exception as exc:  # noqa: BLE001
            job.status = "failed" if not job.cancel.is_set() else "canceled"
            job.error = f"{type(exc).__name__}: {exc}"
            job.push({"type": "error", "stage": "S4", "msg": job.error,
                      "detail": traceback.format_exc()[-1500:]})
        finally:
            job.finished_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)


STORE = Store()
SCENARIO_CACHE: dict[str, dict] = {}


def slim_scenario() -> dict:
    if "slim" not in SCENARIO_CACHE:
        full = STORE.scenario_payload()
        SCENARIO_CACHE["slim"] = {"region": full["region"], "grid": full["grid"],
                                  "graph": full["graph"], "engine": full["engine"]}
    return SCENARIO_CACHE["slim"]


# ---------------------------------------------------------------- HTTP 层
class Handler(BaseHTTPRequestHandler):
    server_version = "DikongXietong/1.0"
    protocol_version = "HTTP/1.1"

    # -- 工具 --
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body: bytes, ctype="application/json; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code, payload):
        self._send(code, json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))

    def _err(self, code, msg, detail=None):
        self._json(code, {"error": {"code": code, "message": msg, "detail": detail}})

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    # -- 路由 --
    def do_GET(self):
        u = urlparse(self.path)
        path, qs = u.path, parse_qs(u.query)
        try:
            if path in ("/api/health", "/healthz"):
                return self._json(200, {"ok": True, "engine": scenario_mod.ENGINE_VERSION,
                                        "uptime_s": round(time.time() - STORE.started, 1),
                                        "jobs": len(STORE.jobs)})
            if path == "/api/params":
                return self._json(200, {"params": scenario_mod.default_params(),
                                        "bounds": {k: list(v) for k, v in scenario_mod.PARAM_BOUNDS.items()},
                                        "modes": scenario_mod.MODE_PRESETS})
            if path == "/api/scenario":
                return self._json(200, STORE.scenario_payload())
            if path == "/api/jobs":
                return self._json(200, {"jobs": [STORE.get(j).summary() for j in STORE.order
                                                 if STORE.get(j)]})
            if path == "/api/decisions":
                return self._json(200, {"decisions": STORE.decisions[-200:]})
            if path.startswith("/api/jobs/"):
                return self._job_routes(path, qs)
            if path == "/api/routes":
                return self._json(200, {"routes": ROUTES})
            return self._static(path)
        except BrokenPipeError:
            raise
        except Exception as exc:  # noqa: BLE001
            return self._err(500, str(exc), traceback.format_exc()[-1200:])

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = self._body()
            if path == "/api/jobs":
                return self._create_job(body)
            if path == "/api/decisions":
                return self._create_decision(body)
            if path == "/api/report":
                return self._report(body)
            return self._err(404, "接口不存在")
        except Exception as exc:  # noqa: BLE001
            return self._err(500, str(exc), traceback.format_exc()[-1200:])

    # -- 作业子路由 --
    def _job_routes(self, path, qs):
        parts = [p for p in path.split("/") if p]
        # /api/jobs/<id>/...
        if len(parts) < 3:
            return self._err(404, "接口不存在")
        job = STORE.get(parts[2])
        if not job:
            return self._err(404, "作业不存在")
        rest = parts[3:]
        if not rest:
            return self._json(200, job.result(slim_scenario()))
        if rest[0] == "events":
            since = int(qs.get("since", ["0"])[0])
            return self._json(200, {"job": job.summary(),
                                    "events": job.events[since:], "next": len(job.events)})
        if rest[0] == "stream":
            return self._sse(job, int(qs.get("since", ["0"])[0]))
        if rest[0] == "cancel" and self.command == "POST":
            job.cancel.set()
            job.status = "canceled"
            job.push({"type": "log", "stage": "S4", "msg": "用户取消作业"})
            return self._json(200, {"job": job.summary()})
        if rest[0] == "plans" and len(rest) >= 2:
            plan = next((p for p in job.plans if p.get("id") == rest[1]), None)
            if not plan:
                return self._err(404, "方案不存在")
            return self._json(200, {"plan": plan, "scenario": slim_scenario(),
                                    "meta": job.meta})
        if rest[0] == "export" and len(rest) >= 2:
            return self._export(job, rest[1])
        return self._err(404, "接口不存在")

    def _create_job(self, body):
        mode = body.get("mode", "quick")
        params = scenario_mod.merge_params(scenario_mod.default_params(), body.get("params") or {})
        params["solver"]["mode"] = mode
        preset = scenario_mod.MODE_PRESETS.get(mode) or {}
        for k, v in preset.items():
            if k != "mode" and not (body.get("params") or {}).get(k):
                params["solver"][k] = v
        if body.get("grid_res_m"):
            params["solver"]["grid_res_m"] = float(body["grid_res_m"])
        issues = [i for i in scenario_mod.validate(params) if i["level"] == "error"]
        if issues:
            return self._json(422, {"error": {"code": "D-PAR", "message": "参数校验未通过",
                                              "issues": issues}})
        job = STORE.submit(params, mode)
        return self._json(202, {"job_id": job.id, "status": job.status,
                                "scenario_hash": job.scenario_hash})

    def _export(self, job, name: str):
        if not job.plans:
            return self._err(409, "作业尚未产出方案")
        if name == "plans.csv":
            return self._send(200, export.plans_csv(STORE.scenario_payload(), job.plans),
                              "text/csv; charset=utf-8",
                              {"Content-Disposition": f'attachment; filename="plans_{job.id}.csv"'})
        base, _, ext = name.rpartition(".")
        pid, _, which = base.partition("_")
        which = which or "subs"
        plan = next((p for p in job.plans if p.get("id") == pid), None)
        if not plan:
            return self._err(404, "方案不存在")
        sc = STORE.scenario_payload()
        fn = f"{job.id}_{pid}"
        if ext == "geojson":
            body = json.dumps(export.plan_geojson(sc, plan), ensure_ascii=False).encode("utf-8")
            return self._send(200, body, "application/geo+json",
                              {"Content-Disposition": f'attachment; filename="{fn}.geojson"'})
        if ext == "kml":
            return self._send(200, export.plan_kml(sc, plan), "application/vnd.google-earth.kml+xml",
                              {"Content-Disposition": f'attachment; filename="{fn}.kml"'})
        if ext == "csv":
            body = export.route_csv(sc, plan) if which == "route" else export.subs_csv(plan)
            return self._send(200, body, "text/csv; charset=utf-8",
                              {"Content-Disposition": f'attachment; filename="{fn}_{which}.csv"'})
        if ext == "json":
            return self._send(200, export.result_json(sc, plan), "application/json",
                              {"Content-Disposition": f'attachment; filename="{fn}.json"'})
        return self._err(404, "不支持的导出格式")

    def _create_decision(self, body):
        rec = {
            "id": uuid.uuid4().hex[:10],
            "job_id": body.get("job_id"),
            "plan_id": body.get("plan_id"),
            "action": body.get("action", "adopt"),
            "comment": body.get("comment", ""),
            "operator": body.get("operator", "调度员"),
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        STORE.decisions.append(rec)
        return self._json(201, {"decision": rec})

    def _report(self, body):
        job = STORE.get(body.get("job_id", ""))
        if not job or not job.plans:
            return self._err(409, "作业尚未产出方案")
        ids = body.get("plan_ids")
        plans = [p for p in job.plans if not ids or p.get("id") in ids]
        decisions = [d for d in STORE.decisions if d["job_id"] == job.id]
        data = export.report_docx(STORE.scenario_payload(), plans, job.recommendations,
                                  job.meta, decisions)
        return self._send(200, data,
                          "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                          {"Content-Disposition":
                           f'attachment; filename="report_{job.id}.docx"'})

    # -- SSE --
    def _sse(self, job: Job, since: int):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        cursor = since
        last_beat = time.time()
        try:
            while True:
                with job.lock:
                    pending = job.events[cursor:]
                    cursor = len(job.events)
                for ev in pending:
                    payload = json.dumps(ev, ensure_ascii=False, default=str)
                    self.wfile.write(f"id: {ev['seq']}\nevent: {ev['type']}\ndata: {payload}\n\n"
                                     .encode("utf-8"))
                if pending:
                    self.wfile.flush()
                    last_beat = time.time()
                if job.status in ("done", "failed", "canceled"):
                    self.wfile.write(b"event: end\ndata: {}\n\n")
                    self.wfile.flush()
                    return
                if time.time() - last_beat > 10:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                    last_beat = time.time()
                time.sleep(0.25)
        except (BrokenPipeError, ConnectionResetError):
            return

    # -- 静态文件 --
    def _static(self, path):
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = os.path.normpath(os.path.join(WEB_DIR, rel))
        if not target.startswith(os.path.normpath(WEB_DIR)):
            return self._err(403, "越界访问")
        if not os.path.isfile(target):
            return self._err(404, f"文件不存在: {rel}")
        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        with open(target, "rb") as fh:
            body = fh.read()
        return self._send(200, body, ctype)

    def do_HEAD(self):
        return self.do_GET()


ROUTES = [
    {"path": "/api/health", "method": "GET", "desc": "健康检查"},
    {"path": "/api/scenario", "method": "GET", "desc": "任务区域与参数"},
    {"path": "/api/jobs", "method": "POST", "desc": "提交求解作业"},
    {"path": "/api/jobs/<id>", "method": "GET", "desc": "作业状态与结果"},
    {"path": "/api/jobs/<id>/stream", "method": "GET", "desc": "SSE 事件流"},
]


def serve(port: int = 8765, host: str = "127.0.0.1"):
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"地空协同林业监测决策系统服务已启动：http://{host}:{port}/  （Ctrl+C 停止）", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止", flush=True)
    finally:
        httpd.server_close()


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="地空协同林业监测决策系统")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8766)
    _args = ap.parse_args()
    serve(_args.port, _args.host)
