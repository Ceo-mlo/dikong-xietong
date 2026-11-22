"""端到端求解冒烟脚本：装配数据 → S0–S6 → 打印前沿与耗时。

用法：
    python scripts/solve_demo.py [quick|standard]
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "backend"))

from dikong_xietong.kernel import pipeline, region as regionmod  # noqa: E402

CANS = regionmod.DEFAULT_SOURCE_DIR

MODE = sys.argv[1] if len(sys.argv) > 1 else "quick"
CFG = {
    "quick": {"quick": True, "max_outer": 4, "seeds_per_outer": 2, "ladder_steps": 3,
              "max_plans": 24, "seed": 2024},
    "standard": {"quick": False, "max_outer": 20, "seeds_per_outer": 4, "ladder_steps": 4,
                 "max_plans": 40, "seed": 2024},
}[MODE]


def emit(ev):
    if ev.get("type") == "log":
        print(f"[{ev['stage']}] {ev['msg']}")
    elif ev.get("type") == "fo":
        print(f"  FO 外层 {ev['outer']}: 前沿 {ev['front_size']} 个，最高覆盖 "
              f"{ev['coverage']*100:.2f}%，新增 {ev['added']}")


def main():
    t0 = time.time()
    reg, issues = regionmod.load_from_files(
        "寨岗镇疫区",
        os.path.join(CANS, "zgz_point.xlsx"),
        os.path.join(CANS, "kongdong_12.xlsx"),
        os.path.join(CANS, "Build_with_utm_coords.csv"),
    )
    print(f"区域装配 {time.time()-t0:.2f}s {reg.summary()}")
    for it in issues:
        if it["level"] != "OK":
            print("  !", it["code"], it["message"])

    prep = pipeline.build_prepared(reg, 100.0, emit=emit)
    print(f"预处理完成 {time.time()-t0:.2f}s：{prep.summary()}")

    out = pipeline.solve(prep, CFG, emit=emit, t0=t0)
    print("\n=== 前沿（按架次） ===")
    for p in out["plans"]:
        m = p["metrics"]
        print(f"{p['id']:>4} {m['sorties']:>3} 架次  覆盖 {m['coverage']*100:6.2f}%  "
              f"总时间 {m['t_total_s']/3600:6.2f} h  无人机 {m['t_uav_s']/3600:6.2f} h  "
              f"车辆 {m['t_car_s']/3600:5.2f} h  CE {m['CE']:7.1f}  RUR {m['RUR']:.3f}  "
              f"ELR {m['ELR']:.3f}  MCG {m['MCG']}")
    print("\n=== 推荐 ===")
    for r in out["recommendations"]:
        print(f"  {r['kind']}: {out['plans'][r['index']]['id']} — {r['reason']}")
    print("\nmeta:", json.dumps(out["meta"], ensure_ascii=False, default=str)[:1200])

    outdir = os.path.join(ROOT, "runtime")
    try:
        os.makedirs(outdir, exist_ok=True)
    except PermissionError:
        pass  # 沙箱下由外部先行创建
    outfile = os.path.join(outdir, f"front_{MODE}.json")
    try:
        with open(outfile, "w", encoding="utf-8") as fh:
            json.dump({"plans": out["plans"], "recommendations": out["recommendations"],
                       "meta": out["meta"]}, fh, ensure_ascii=False)
        print(f"\n结果已写入 {outfile}，总耗时 {time.time()-t0:.1f}s")
    except PermissionError:
        print("\n（沙箱拒绝写盘，结果仅在内存中）")


if __name__ == "__main__":
    main()
