"""导出器：GeoJSON / CSV / KML / Word 报告（对应功能 O3 与 C2-05）。

全部在内存里生成字节流，不落盘——沙箱与离线部署都不依赖临时目录。
"""
from __future__ import annotations

import csv
import io
import json
from typing import Any


def _ring_coords(ring, close=True):
    pts = [[round(float(x), 2), round(float(y), 2)] for x, y in ring]
    if close and pts and pts[0] != pts[-1]:
        pts.append(pts[0])
    return pts


def plan_geojson(scenario: dict, plan: dict) -> dict:
    """分图层 GeoJSON：区域/空洞/停靠点/车辆路书/子区/航迹。"""
    region = scenario["region"]
    feats: list[dict] = []

    feats.append({
        "type": "Feature",
        "properties": {"kind": "region", "name": region["name"], "area_km2": region["area_km2"]},
        "geometry": {"type": "Polygon",
                     "coordinates": [_ring_coords(region["outer"])] +
                                    [_ring_coords(h) for h in region["holes"]]},
    })

    docks = {d["id"]: d for d in region["docks"]}
    used = sorted({s["dock"] for s in plan["subs"]})
    for did in used:
        d = docks.get(did)
        if not d:
            continue
        feats.append({
            "type": "Feature",
            "properties": {"kind": "dock", "id": did, "label": d.get("label")},
            "geometry": {"type": "Point", "coordinates": [round(d["x"], 2), round(d["y"], 2)]},
        })

    route = plan.get("route") or {}
    order = route.get("order") or []
    if order:
        docks_list = region["docks"]
        pts = [region["base"]]
        pts += [[docks_list[int(i)]["x"], docks_list[int(i)]["y"]] for i in order]
        pts.append(region["base"])
        feats.append({
            "type": "Feature",
            "properties": {"kind": "route", "length_m": route["length_m"],
                           "docks": len(order)},
            "geometry": {"type": "LineString",
                         "coordinates": [[round(float(p[0]), 2), round(float(p[1]), 2)]
                                         for p in pts]},
        })

    avg_yaw = 0.0
    for sub in plan["subs"]:
        if sub.get("path"):
            coords = [[round(float(p[0]), 2), round(float(p[1]), 2)] for p in sub["path"]]
            feats.append({
                "type": "Feature",
                "properties": {"kind": "sortie", "label": sub["label"], "dock": sub["dock"],
                               "yaw_deg": sub["yaw_deg"], "cells": sub["cells"],
                               "coverage": sub["coverage"], "t_uav_s": sub["t_uav_s"],
                               "photos": sub["photos"], "exhausted": sub["exhausted"]},
                "geometry": {"type": "LineString", "coordinates": coords},
            })
        avg_yaw += sub["yaw_deg"]

    n = max(1, len(plan["subs"]))
    return {
        "type": "FeatureCollection",
        "properties": {
            "plan_id": plan.get("id"),
            "tag": plan.get("tag"),
            "coverage": plan.get("coverage"),
            "t_total_s": plan.get("t_total_s"),
            "sorties": plan.get("sorties"),
            "avg_yaw_deg": round(avg_yaw / n, 2),
            "crs": "EPSG:32649",
        },
        "features": feats,
    }


def plans_csv(scenario: dict, plans: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["方案", "架次", "覆盖率(%)", "总时间(h)", "无人机时间(h)", "车辆时间(h)",
                "覆盖效率CE(s/km2)", "AUT(s)", "RUR", "ELR", "ALD(m)", "MCG", "拐点标记"])
    for p in plans:
        w.writerow([
            p.get("id"), p.get("sorties"), round(100 * p.get("coverage", 0), 2),
            round(p.get("t_total_s", 0) / 3600, 2), round(p.get("t_uav_s", 0) / 3600, 2),
            round(p.get("t_car_s", 0) / 3600, 2), p.get("CE"), p.get("AUT"),
            p.get("RUR"), p.get("ELR"), p.get("ALD"),
            p.get("MCG") if p.get("MCG") is not None else "",
            "拐点" if p.get("is_knee") else "",
        ])
    return buf.getvalue().encode("utf-8-sig")


def subs_csv(plan: dict) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["子区", "单元数", "停靠点", "航向角(deg)", "覆盖率(%)", "无人机时间(s)",
                "航程(m)", "拍照数", "空驶(m)", "能量耗尽"])
    for s in plan["subs"]:
        w.writerow([s["label"], s["cells"], s["dock"], s["yaw_deg"],
                    round(100 * s["coverage"] / max(1, s["cells_in_sub"]), 2),
                    s["t_uav_s"], s["flight_m"], s["photos"], s["empty_leg_m"],
                    "是" if s["exhausted"] else "否"])
    return buf.getvalue().encode("utf-8-sig")


def route_csv(scenario: dict, plan: dict) -> bytes:
    region = scenario["region"]
    docks = region["docks"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["顺序", "节点类型", "节点", "X", "Y", "段长(m)"])
    route = plan.get("route") or {}
    order = route.get("order") or []
    lens = route.get("leg_lengths") or []
    if order:
        w.writerow([0, "基地", "base", round(region["base"][0], 1), round(region["base"][1], 1),
                    round(float(lens[0]), 1) if lens else ""])
        for i, dock in enumerate(order):
            d = docks[int(dock)]
            idx = min(i + 1, max(0, len(lens) - 1))
            w.writerow([i + 1, "停靠点", int(dock), d["x"], d["y"],
                        round(float(lens[idx]), 1) if lens else ""])
        last = docks[int(order[-1])]
        w.writerow(["返回", "基地", "base", round(region["base"][0], 1),
                    round(region["base"][1], 1),
                    round(float(lens[-1]), 1) if lens else ""])
    w.writerow([])
    w.writerow(["合计行驶距离(m)", route.get("length_m", 0)])
    return buf.getvalue().encode("utf-8-sig")


def plan_kml(scenario: dict, plan: dict) -> bytes:
    region = scenario["region"]
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>',
        f"<name>地空协同林业监测决策系统方案 {plan.get('id')}</name>",
        '<Style id="sortie"><LineStyle><color>ff2eaee0</color><width>2</width></LineStyle></Style>',
        '<Style id="route"><LineStyle><color>ff1e8ae0</color><width>3</width></LineStyle></Style>',
        "<Placemark><name>任务区域</name><Polygon><outerBoundaryIs><LinearRing><coordinates>",
        " ".join(f"{x},{y},0" for x, y in _ring_coords(region["outer"])),
        "</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>",
    ]
    route = plan.get("route") or {}
    order = route.get("order") or []
    if order:
        docks_list = region["docks"]
        pts = [region["base"]] + [[docks_list[int(i)]["x"], docks_list[int(i)]["y"]]
                                  for i in order] + [region["base"]]
        parts.append('<Placemark><name>车辆路书</name><styleUrl>#route</styleUrl>'
                     '<LineString><coordinates>')
        parts.append(" ".join(f"{float(p[0])},{float(p[1])},0" for p in pts))
        parts.append("</coordinates></LineString></Placemark>")
    for sub in plan["subs"]:
        if not sub.get("path"):
            continue
        parts.append(f'<Placemark><name>架次 {sub["label"]}</name><styleUrl>#sortie</styleUrl>'
                     '<LineString><coordinates>')
        parts.append(" ".join(f"{float(p[0])},{float(p[1])},0" for p in sub["path"]))
        parts.append("</coordinates></LineString></Placemark>")
    parts.append("</Document></kml>")
    return "".join(parts).encode("utf-8")


def report_docx(scenario: dict, plans: list[dict], recommendations: list[dict],
                meta: dict, decisions: list[dict] | None = None) -> bytes:
    """生成《协同监测作业方案报告》Word 文档。"""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    region = scenario["region"]
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Microsoft YaHei"
    style.font.size = Pt(10.5)

    title = doc.add_heading("地空协同林业监测作业方案报告", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_paragraph(f"任务区域：{region['name']}（面积 {region['area_km2']:.2f} km²，"
                      f"空洞 {region['holes_count']} 个，路网 {region['roads_count']} 段）")
    doc.add_paragraph(f"求解引擎：{meta.get('engine', '地空协同林业监测决策系统')}；"
                      f"网格 {meta.get('prepared', {}).get('cells', '-')} 单元 / "
                      f"{meta.get('prepared', {}).get('area_km2', 0):.1f} km²；"
                      f"架次区间 {meta.get('ladder')}；用时 {meta.get('elapsed_s')} s")

    doc.add_heading("一、结论摘要", level=1)
    doc.add_paragraph(
        f"本次共得到 {len(plans)} 个非支配方案。覆盖率区间 "
        f"{min(p['coverage'] for p in plans) * 100:.2f}%–{max(p['coverage'] for p in plans) * 100:.2f}%，"
        f"总作业时间区间 {min(p['t_total_s'] for p in plans) / 3600:.2f}–"
        f"{max(p['t_total_s'] for p in plans) / 3600:.2f} 小时。")

    doc.add_heading("二、推荐方案与理由", level=1)
    for rec in recommendations:
        p = plans[rec["index"]]
        doc.add_paragraph(f"· {rec['kind']}｜{p['id']}：{rec['reason']}", style="List Bullet")

    doc.add_heading("三、方案对比表", level=1)
    table = doc.add_table(rows=1, cols=8)
    table.style = "Table Grid"
    heads = ["方案", "架次", "覆盖率", "总时间(h)", "无人机(h)", "车辆(h)", "CE(s/km²)", "RUR"]
    for i, h in enumerate(heads):
        table.rows[0].cells[i].text = h
    for p in plans:
        row = table.add_row().cells
        row[0].text = str(p.get("id"))
        row[1].text = str(p.get("sorties"))
        row[2].text = f"{p['coverage'] * 100:.2f}%"
        row[3].text = f"{p['t_total_s'] / 3600:.2f}"
        row[4].text = f"{p['t_uav_s'] / 3600:.2f}"
        row[5].text = f"{p['t_car_s'] / 3600:.2f}"
        row[6].text = f"{p['CE']:.1f}"
        row[7].text = f"{p['RUR']:.3f}"

    doc.add_heading("四、指标口径", level=1)
    for text in (
        "覆盖率：被拍摄网格单元数 / 有效网格单元数；单架次因能量不足提前返航时只计实际拍摄单元。",
        "总作业时间 = 无人机作业时间（飞行 + 悬停拍摄 + 起降准备）+ 车辆行驶时间。",
        "覆盖效率 CE = 总作业时间 /（覆盖率 × 区域面积），单位 s/km²，越小越好。",
        "AUT：单架次平均无人机作业时间；RUR：单架次平均航程 / 最大航程。",
        "ELR：往返首个作业单元的空驶时间占总作业时间比例；ALD：车辆平均单段行驶距离。",
        "MCG：增加一架次带来的覆盖率增量 / 时间增量，用于判断边际收益是否递减。",
    ):
        doc.add_paragraph(text, style="List Bullet")

    doc.add_heading("五、管理启示", level=1)
    doc.add_paragraph("① 日常巡查可按 70% 覆盖率档组织，疫点普查按 100% 覆盖组织；"
                      "② 拐点解相对全覆盖方案架次更少，适合作为常态化作业计划；"
                      "③ 方案须经值班人员复核确认后方可下发，系统只提供候选与理由。")

    if decisions:
        doc.add_heading("六、决策留痕", level=1)
        for d in decisions:
            doc.add_paragraph(f"{d.get('created_at', '')}｜{d.get('plan_id', '')}｜"
                              f"{d.get('action')}｜{d.get('comment', '')}", style="List Bullet")

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def result_json(scenario: dict, plan: dict) -> bytes:
    payload = {
        "scenario": {k: scenario["region"][k] for k in
                     ("id", "name", "area_km2", "vertices", "holes_count", "roads_count")},
        "plan": plan,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
