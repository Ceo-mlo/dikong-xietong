# 地空协同林业监测决策系统（v1.0 规划决策版）

把论文《基于地空协同的林业监测多目标路径优化研究》的模型做成可运行、可复现、可导出的决策软件。
单机（1 车库 / 1 车辆 / 1 无人机多次起降）、纯离线，仅依赖 `numpy`、`openpyxl`、`python-docx`。

## 启动

三种方式任选其一：

```bat
REM 1) 双击
start.bat

REM 2) 仓库根目录
python run.py --port 8766

REM 3) 直接跑服务文件（已支持）
python backend\foresteye\api\app.py --port 8766
```

然后浏览器打开 <http://127.0.0.1:8766>。

> 若 `python` 不可用（Windows 商店占位符），用真实解释器或改 `start.bat` 里的 `PY` 路径。
> 需要 `numpy`、`openpyxl`、`python-docx` 三个包，其余全部标准库。
> 仓库已内置运行所需的原始边界/路网数据（`data/source/`：`zgz_point.xlsx`、`kongdong_12.xlsx`、`Build_with_utm_coords.csv`）；也可用环境变量 `FORESTEYE_SOURCE_DIR` 指定其他数据目录。

## 页面

| # | 路由 | 说明 |
| --- | --- | --- |
| 1 | `#/workbench` | 工作台：区域/网格/停靠点规模、最近作业、拐点推荐 |
| 2 | `#/data` | 数据管理：四步导入向导、体检报告、区域/网格/路网/停靠点 |
| 3 | `#/params` | 场景与参数：四组参数、档位、架次区间、场景哈希 |
| 4 | `#/jobs` | 求解作业：提交表单、作业列表、取消 |
| 5 | `#/monitor` | 作业监控：S0–S6 阶段条、SSE 日志、前沿增长曲线、诊断 |
| 6 | `#/plans` | 前沿与方案：Pareto 散点、方案表、详情六块（含导出） |
| 7 | `#/map` | 一张图：子区着色、架次航迹、车辆路书、停靠点、点击选子区 |
| 8 | `#/decide` | 推荐与决策：Top-K 候选 + 数字理由 + 采纳/驳回留痕 |
| 9 | `#/report` | 结论与报告：结论卡、对比表、指标口径、管理启示、Word 报告 |

## 目录

```
地空协同林业监测决策系统/
├── run.py                    启动器（把 backend 加入 sys.path）
├── start.bat                 Windows 双击启动
├── backend/foresteye/
│   ├── kernel/               纯算法内核，不依赖 HTTP
│   │   ├── geo.py            UTM 49N 正反算、点在多边形、凸包、RDP
│   │   ├── region.py         区域装配（外环 − 空洞 + 路网 + 基地）
│   │   ├── grid.py           网格化与行游程编码
│   │   ├── roads.py          路网拓扑修补、吸附、桥接、Dijkstra、停靠点筛选
│   │   ├── sortie.py         单架次牛耕航迹仿真（含空洞分段与能量返航）
│   │   ├── partition.py      KMeans++ 划分、连通修复、停靠点匹配
│   │   ├── ga.py             双适应度航向角 GA
│   │   ├── model.py          方案评估：指标、车辆路书、快照
│   │   ├── fo.py             FO 交替迭代（op1 停靠点 / op2 重划分）
│   │   ├── archive.py        ε-Pareto 归档、拐点、推荐
│   │   ├── pipeline.py       S0–S6 编排
│   │   └── scenario.py       参数模板、校验、场景指纹
│   └── api/
│       ├── app.py            HTTP 服务 + 作业队列 + SSE
│       └── export.py         GeoJSON / KML / CSV / JSON / Word
├── web/                      index.html + assets（无 CDN）
├── scripts/                  calibrate.py 标定、solve_demo.py 冒烟
└── data/
    ├── source/               原始数据（随仓库提供）
    └── ...                   区域缓存（首次运行生成，已忽略）
```

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查 |
| GET | `/api/scenario` | 区域、网格、路网采样、停靠点、默认参数 |
| POST | `/api/jobs` | 提交作业，`{"mode":"quick"\|"standard"}`，202 + job_id |
| GET | `/api/jobs` | 作业列表 |
| GET | `/api/jobs/{id}` | 作业详情（plans / recommendations / meta / logs） |
| GET | `/api/jobs/{id}/stream` | SSE：log / point / fo / done / end |
| POST | `/api/jobs/{id}/cancel` | 取消（5 s 内响应） |
| GET | `/api/jobs/{id}/plans/{pid}` | 方案完整快照 |
| GET | `/api/jobs/{id}/export/{pid}.geojson\|kml\|json` | 单方案导出 |
| GET | `/api/jobs/{id}/export/plans.csv` | 前沿总表 |
| GET/POST | `/api/decisions` | 决策留痕 |
| POST | `/api/report` | Word 报告（docx 流） |

## 算法口径

- 数据：寨岗镇真实边界（外环 285 顶点、48 空洞）、UTM 49N（EPSG:32649）、100 m 网格 28 643 单元、280 个候选停靠点。
- 目标：max 覆盖率、min 总作业时间 `T = T_fly + T_hover + T_prepare + T_car`；ε-Pareto 归档，ε_cov = 0.001、ε_t = 60 s。
- 约束：单架次 469 单元上限（式 3.25）、能量 277 200 J × 0.8、返航校验、子区连通、停靠点可达。
- FO：op1 停靠点 UCB-logit + VNS，op2 孤立块迁移/单元回退，增量合成 + 定期全量对拍。
- 指标：CE、AUT、RUR、MCG、ELR、ALD（口径见页面"指标口径"卡）。

## 已知限制

单车单机；规划覆盖率（非实飞）；无在线底图（Canvas 绘制）；无空域/视频/设备台账（P1/P2 版本）；
50 m 网格未做性能承诺；报告模板固定。
