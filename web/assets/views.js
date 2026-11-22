/* 九个业务视图：工作台 / 数据管理 / 场景与参数 / 求解作业 / 作业监控 / 前沿与方案 / 一张图 / 推荐与决策 / 结论与报告 */
(function () {
  'use strict';
  const { api, state, fmt, el, esc, toast, status, download, downloadPost } = window.FE;

  /* ---------------- 通用构件 ---------------- */
  function pageHead(title, sub, actions) {
    return el('div', { class: 'page-head' }, [
      el('div', {}, [
        el('h1', { text: title }),
        sub ? el('div', { class: 'sub', html: sub }) : null,
      ]),
      el('div', { class: 'page-actions' }, actions || []),
    ]);
  }

  function card(title, hint, body, foot) {
    return el('section', { class: 'card' }, [
      el('div', { class: 'card-head' }, [
        el('h3', { text: title }),
        hint ? el('span', { class: 'hint', html: hint }) : null,
      ]),
      body,
      foot ? el('div', { class: 'card-foot', html: foot }) : null,
    ]);
  }

  function kpi(label, value, unit, foot) {
    return el('div', { class: 'kpi' }, [
      el('div', { class: 'label', text: label }),
      el('div', { class: 'value' }, [
        document.createTextNode(value == null ? '—' : String(value)),
        unit ? el('small', { text: unit }) : null,
      ]),
      foot ? el('div', { class: 'foot', text: foot }) : null,
    ]);
  }

  function tag(text, kind) {
    return el('span', { class: 'tag ' + (kind || ''), text });
  }

  function table(cols, rows, opts) {
    opts = opts || {};
    const thead = el('thead', {}, [
      el('tr', {}, cols.map((c) => el('th', {
        class: c.num ? 'num' : '', text: c.title, style: c.width ? `width:${c.width}` : null,
      }))),
    ]);
    const tbody = el('tbody', {}, rows.map((r, i) => {
      const tr = el('tr', { dataset: { i: String(i) } });
      if (r.__cls) tr.className = r.__cls;
      cols.forEach((c) => {
        const cell = el('td', { class: c.num ? 'num' : '' });
        const v = r[c.key];
        if (c.html) cell.innerHTML = c.html(v, r);
        else cell.textContent = v == null ? '—' : String(v);
        tr.appendChild(cell);
      });
      if (opts.onRow) {
        tr.addEventListener('click', () => opts.onRow(r, tr));
        if (opts.selectable !== false) tr.style.cursor = 'pointer';
      }
      return tr;
    }));
    const t = el('table', { class: 'tbl' }, [thead, tbody]);
    return el('div', { class: 'table-wrap' }, [t]);
  }

  function emptyBox(title, text) {
    return el('div', { class: 'empty', html: `<b>${esc(title)}</b>${esc(text || '')}` });
  }

  function kvGrid(pairs) {
    return el('div', { class: 'kv-grid' }, pairs.flatMap(([k, v]) => [
      el('div', { class: 'k', text: k }),
      el('div', { class: 'v', text: v == null ? '—' : String(v) }),
    ]));
  }

  function btn(text, onclick, kind) {
    return el('button', { class: 'btn ' + (kind || ''), text, onclick });
  }

  function fmtDur(sec) {
    if (sec == null) return '—';
    const s = Math.round(sec);
    return s < 60 ? `${s} 秒` : `${Math.floor(s / 60)} 分 ${String(s % 60).padStart(2, '0')} 秒`;
  }

  function modeName(m) { return m === 'quick' ? '快速档' : m === 'fine' ? '精细档' : '标准档'; }

  const REC_LABEL = {
    knee: '拐点方案（推荐）', max_coverage: '最大覆盖', min_time: '最短时间',
    balanced: '折中方案', max_ce: '最高覆盖效率',
  };

  /* 载入最新作业（若状态里没有） */
  function withJob(cb) {
    if (state.job) { cb(state.job); return; }
    const j = (state.jobs || []).find((x) => x.status === 'done');
    if (!j) { cb(null); return; }
    api.job(j.job_id).then((res) => {
      state.jobId = j.job_id;
      state.job = res.job;
      state.plans = res.plans || [];
      state.planId = null;
      cb(res);
    }).catch((e) => cb(null, e));
  }

  function choosePlan(host, plan, rerender) {
    state.planId = plan.id;
    api.plan(state.jobId, plan.id).then((res) => {
      state.planCache[state.jobId + ':' + plan.id] = res.plan;
      rerender();
    }).catch((e) => toast(e.message, 'err'));
  }

  function planDetail(planId) {
    return state.jobId ? state.planCache[state.jobId + ':' + planId] : null;
  }

  /* ================= 1. 工作台 ================= */
  function workbench(host) {
    const sc = state.scenario;
    host.appendChild(pageHead('工作台', '当前任务区域、最近一次求解与推荐结论',
      [btn('新建场景', () => window.FE.nav('params'), 'primary'), btn('提交求解', () => window.FE.nav('jobs'))]));

    if (!sc) { host.appendChild(emptyBox('正在读取场景…', '服务连接后自动装载区域数据。')); return; }

    const rec = state.job && state.job.recommendations;
    const knee = rec && rec.find((r) => r.kind === 'knee');
    const kneePlan = knee && state.plans.find((p) => p.id === knee.plan_id);

    host.appendChild(el('div', { class: 'grid g4' }, [
      kpi('任务区域', fmt.fixed(sc.region.area_km2, 1), 'km²', sc.region.name),
      kpi('网格单元', fmt.int(sc.grid.cells), '个', `分辨率 ${sc.grid.res_m} m · ${sc.grid.nx}×${sc.grid.ny}`),
      kpi('候选停靠点', fmt.int(sc.region.docks.length), '个', `路网 ${fmt.int(sc.graph.nodes)} 节点 / ${fmt.fixed(sc.graph.length_km, 0)} km`),
      kpi('前沿方案', state.plans.length ? fmt.int(state.plans.length) : '—', '个',
        state.job ? `最近作业 ${state.job.mode ? modeName(state.job.mode) : ''} · ${fmtDur(state.job.meta && state.job.meta.elapsed_s)}` : '尚未提交求解'),
    ]));

    const quick = el('ul', { class: 'list' }, [
      ['导入与体检区域数据', '数据管理'],
      ['调整参数并提交求解', '场景与参数'],
      ['查看 Pareto 前沿与方案', '前沿与方案'],
      ['在图上核对航迹与路书', '一张图'],
      ['采纳推荐并生成报告', '结论与报告'],
    ].map(([t, r]) => el('li', {}, [
      el('span', { class: 'idx mono', text: '→' }),
      el('span', { text: t, style: 'flex:1' }),
      btn('进入', () => window.FE.nav(routeOf(r)), 'soft'),
    ])));

    const left = card('快捷入口', '按主流程顺序', quick);
    const right = card('推荐方案', kneePlan ? `${kneePlan.id} · 拐点` : '暂无', kneePlan
      ? el('div', {}, [
        kvGrid([
          ['覆盖率', fmt.pct(kneePlan.coverage)],
          ['总作业时间', fmt.hours(kneePlan.t_total_s) + ' h'],
          ['架次', kneePlan.sorties + ' 架次'],
          ['覆盖效率 CE', fmt.fixed(kneePlan.CE, 1) + ' s/km²'],
        ]),
        el('div', { class: 'card-foot' }, [btn('查看方案', () => { window.FE.nav('plans'); }, 'primary')]),
      ])
      : emptyBox('尚无求解结果', '提交一次求解后显示推荐方案与理由。'));

    host.appendChild(el('div', { class: 'grid g-3-2' }, [left, right]));

    const recent = state.jobs.slice(0, 6);
    host.appendChild(card('最近作业', '点击行进入监控',
      recent.length ? table([
        { key: 'job_id', title: '作业号' },
        { key: 'status', title: '状态' },
        { key: 'mode', title: '档位' },
        { key: 'plans', title: '方案数', num: true },
        { key: 'elapsed_s', title: '耗时', num: true },
      ], recent.map((j) => ({
        ...j,
        status: j.status === 'done' ? '已完成' : j.status === 'running' ? '运行中'
          : j.status === 'failed' ? '失败' : j.status === 'canceled' ? '已取消' : '排队',
        mode: modeName(j.mode),
        elapsed_s: j.elapsed_s == null ? '—' : fmt.fixed(j.elapsed_s, 1) + ' s',
      })), {
        onRow: (r) => { window.FE.openJob(r.job_id); },
      }) : emptyBox('暂无作业', '点击右上角"提交求解"开始。')));
  }

  function routeOf(name) {
    return {
      工作台: 'workbench', 数据管理: 'data', 场景与参数: 'params', 求解作业: 'jobs',
      作业监控: 'monitor', 前沿与方案: 'plans', 一张图: 'map', 推荐与决策: 'decide',
    }[name] || 'workbench';
  }

  /* ================= 2. 数据管理 ================= */
  function dataView(host) {
    const sc = state.scenario;
    host.appendChild(pageHead('数据管理', '任务区域装配结果、导入体检与计算规模',
      [btn('重新体检', () => window.FE.reloadScenario(), 'soft')]));
    if (!sc) { host.appendChild(emptyBox('正在读取…', '')); return; }

    const r = sc.region, g = sc.grid, gr = sc.graph;
    const wizard = el('div', { class: 'wizard' }, [
      ['1 区域外环', `${r.vertices} 顶点`, 'zgz_point.xlsx'],
      ['2 剔除空洞', `${r.holes_count} 个`, 'kongdong_12.xlsx'],
      ['3 路网建图', `${gr.nodes} 节点`, 'Build_with_utm_coords.csv'],
      ['4 网格与停靠点', `${g.cells} 单元`, `EPSG:32649 · ${g.res_m} m`],
    ].map(([t, v, s], i) => el('div', { class: 'step' }, [
      el('span', { class: 'n', text: '0' + (i + 1) }),
      el('b', { text: t }),
      el('div', { class: 'mono', text: v }),
      el('div', { class: 'muted small', text: s }),
    ])));
    host.appendChild(card('导入向导', '四步装配，任一步异常会在体检报告中阻断', wizard));

    const issues = (sc.issues || []).filter((it) => it.level !== 'info' && it.code !== 'OK');
    const issueRows = issues.map((it) => ({
      level: it.level === 'error' ? '阻断' : it.level === 'warning' ? '警告' : '提示',
      code: it.code, msg: it.msg,
      __cls: it.level === 'error' ? 'is-err' : '',
    }));
    host.appendChild(el('div', { class: 'grid g-2-1' }, [
      card('体检报告', issues.length ? `共 ${issues.length} 条` : '全部通过',
        issues.length ? table([
          { key: 'level', title: '级别', width: '64px' },
          { key: 'code', title: '代码', width: '96px' },
          { key: 'msg', title: '说明' },
        ], issueRows) : emptyBox('数据体检通过', '外环闭合、空洞有效、路网连通、停靠点可达。')),
      card('区域概况', '', kvGrid([
        ['区域名称', r.name],
        ['有效面积', fmt.fixed(r.area_km2, 2) + ' km²'],
        ['面积（公顷）', fmt.fixed(r.area_ha, 1)],
        ['外环顶点', r.vertices],
        ['空洞数量', r.holes_count],
        ['路网要素', r.roads_count],
        ['基地坐标', `E ${fmt.fixed(r.base[0], 0)} / N ${fmt.fixed(r.base[1], 0)}`],
        ['经度纬度', `${fmt.fixed(r.base_lonlat[0], 6)}, ${fmt.fixed(r.base_lonlat[1], 6)}`],
      ])),
    ]));

    host.appendChild(el('div', { class: 'grid g3' }, [
      card('网格', `${g.res_m} m 分辨率`, kvGrid([
        ['有效单元', fmt.int(g.cells)],
        ['候选（包络）', fmt.int(g.candidates)],
        ['保留率', fmt.pct(g.keep_ratio)],
        ['栅格 nx/ny', `${g.nx} × ${g.ny}`],
      ])),
      card('路网', '已做拓扑修补', kvGrid([
        ['节点', fmt.int(gr.nodes)],
        ['线段', fmt.int(gr.segments)],
        ['总里程', fmt.fixed(gr.length_km, 1) + ' km'],
        ['平均段长', fmt.fixed(gr.mean_segment_m, 1) + ' m'],
        ['打断 / 桥接', `${fmt.int(gr.topology_splits || 0)} / ${fmt.int(gr.bridges || 0)}`],
      ])),
      card('停靠点', `τ_dock 筛选后`, kvGrid([
        ['数量', fmt.int(r.docks.length)],
        ['最远车程', fmt.km(Math.max.apply(null, r.docks.map((d) => d.dist_base_m))) + ' km'],
        ['最近车程', fmt.km(Math.min.apply(null, r.docks.map((d) => d.dist_base_m))) + ' km'],
      ])),
    ]));

    host.appendChild(card('数据口径', '与论文模型一致的常量',
      kvGrid([
        ['投影', 'EPSG:32649（WGS84 / UTM 49N）'],
        ['测量面积', '外环 − 空洞（二维投影面积）'],
        ['架次定义', '一次起飞到一次降落'],
        ['覆盖率口径', '被覆盖有效单元 / 有效单元总数'],
        ['能量口径', '标称 277 200 J × 安全系数 0.8 = 可用 19 872 m'],
      ]), '论文第 3 章式(3.1)–(3.26)，实现细节见架构文档 03。'));
  }

  /* ================= 3. 场景与参数 ================= */
  function paramsView(host) {
    const sc = state.scenario;
    host.appendChild(pageHead('场景与参数', '参数改动不会修改算法，只影响本次求解口径',
      [btn('恢复默认', () => { if (sc) { window.FE.state.params = JSON.parse(JSON.stringify(sc.params)); render(); } }, 'soft')]));
    if (!sc) { host.appendChild(emptyBox('正在读取…', '')); return; }

    state.params = state.params || JSON.parse(JSON.stringify(sc.params));
    const p = state.params;
    const bounds = sc.param_bounds || {};

    const mode = el('div', { class: 'seg' }, ['quick', 'standard'].map((m) => {
      const b = el('button', { text: modeName(m) + (m === 'quick' ? ' · 演示' : ' · 交付') });
      if ((state.filters.mode || 'quick') === m) b.classList.add('on');
      b.addEventListener('click', () => {
        state.filters.mode = m;
        const preset = (sc.modes[m] || {});
        Object.keys(preset).forEach((k) => { if (k !== 'mode') p.solver[k] = preset[k]; });
        render();
      });
      return b;
    }));

    const head = el('div', { class: 'chips', style: 'margin-bottom:8px' }, [
      el('span', { class: 'muted small', text: '求解档位' }), mode,
      el('span', { class: 'muted small', text: '网格' }),
      (function () {
        const sel = el('select', {});
        [50, 100, 200].forEach((v) => {
          const o = el('option', { value: String(v), text: v + ' m' });
          if (Number(p.solver.grid_res_m) === v) o.selected = true;
          sel.appendChild(o);
        });
        sel.addEventListener('change', () => { p.solver.grid_res_m = Number(sel.value); updateHash(); });
        return sel;
      })(),
      el('span', { class: 'muted small', text: '场景哈希' }),
      el('span', { class: 'mono', id: 'paramHash', text: hashPreview() }),
    ]);
    host.appendChild(head);

    function hashPreview() {
      return (state.job && state.job.scenario_hash ? state.job.scenario_hash : '待提交后生成').slice(0, 16);
    }
    function updateHash() {
      const n = document.getElementById('paramHash');
      if (n) n.textContent = hashPreview();
    }

    const groups = [
      ['无人机 UAV', 'uav', [
        ['battery_j', '标称能量', 'J', 1], ['max_range_m', '最大航程', 'm', 1],
        ['safe_factor', '安全系数', '', 0.01], ['speed_mps', '飞行速度', 'm/s', 0.1],
        ['prepare_s', '起降准备', 's', 1], ['hover_s_per_photo', '单点拍摄', 's', 0.1],
        ['max_cells_per_sortie', '单架次上限', '单元', 1],
      ]],
      ['相机 Camera', 'camera', [
        ['heading_overlap', '航向重叠率', '', 0.01], ['side_overlap', '旁向重叠率', '', 0.01],
        ['swath_m', '旁向间距', 'm', 1], ['photo_step_m', '航向间距', 'm', 1],
        ['altitude_m', '相对航高', 'm', 1],
      ]],
      ['车辆 Vehicle', 'vehicle', [
        ['speed_kph', '车速', 'km/h', 0.1], ['speed_mps', '车速（内部）', 'm/s', 0.1],
        ['max_drive_time_s', '单车程上限', 's', 1],
      ]],
      ['求解器 Solver', 'solver', [
        ['sortie_min', '架次下限', '架次', 1], ['sortie_max', '架次上限', '架次', 1],
        ['max_outer', '外层迭代', '次', 1], ['seeds_per_outer', '每外层次数', '个', 1],
        ['stop_no_improve', '无改进终止', '次', 1], ['seed', '随机种子', '', 1],
      ]],
    ];

    const accs = groups.map(([title, key, fields], idx) => {
      const body = el('div', { class: 'form-grid' }, fields.map(([f, label, unit, step]) => {
        const input = el('input', {
          type: 'number', step: String(step), value: String(p[key][f]),
          dataset: { path: key + '.' + f },
        });
        input.addEventListener('change', () => {
          p[key][f] = Number(input.value);
          updateHash();
        });
        const b = bounds[key + '.' + f];
        return el('label', { class: 'field' }, [
          el('span', { class: 'lb', text: label + (unit ? `（${unit}）` : '') }),
          input,
          b ? el('span', { class: 'unit', text: `范围 ${b[0]} – ${b[1]}` }) : null,
        ]);
      }));
      const head2 = el('button', {
        class: 'acc-head', type: 'button',
        html: `<span class="acc-t"><b>${esc(title)}</b><span class="muted small mono">${esc(key)}</span></span>`
          + '<span class="muted" data-role="caret">展开</span>',
      });
      const wrap = el('div', { class: 'acc' }, [head2, el('div', { class: 'acc-body hide' }, [body])]);
      head2.addEventListener('click', () => {
        const b = wrap.querySelector('.acc-body');
        const open = b.classList.toggle('hide') === false;
        head2.querySelector('[data-role=caret]').textContent = open ? '收起' : '展开';
      });
      if (idx === 0) {
        wrap.querySelector('.acc-body').classList.remove('hide');
        head2.querySelector('[data-role=caret]').textContent = '收起';
      }
      return wrap;
    });
    host.appendChild(card('参数模板', '四组参数：无人机 / 相机 / 车辆 / 求解器', el('div', { class: 'grid g2' }, accs)));

    const actions = el('div', { class: 'chips' }, [
      btn('校验参数', async () => {
        window.FE.state.params = state.params;
        toast('参数已暂存，提交时由服务端复核');
      }, 'soft'),
      btn('提交求解', async () => {
        status('正在提交求解作业…');
        try {
          const res = await api.submit({ mode: state.filters.mode || 'quick', params: state.params });
          toast('作业已提交：' + res.job_id, 'ok');
          window.FE.openJob(res.job_id);
        } catch (e) {
          status('提交失败', 'err');
          toast(e.message, 'err');
        }
      }, 'primary'),
    ]);
    host.appendChild(card('提交', '快速档约 10 秒出前沿，标准档约 1–2 小时（受外层迭代次数影响）', actions));

    function render() { window.FE.rerender(); }
  }

  /* ================= 4. 求解作业 ================= */
  function jobsView(host) {
    const sc = state.scenario;
    host.appendChild(pageHead('求解作业', '异步提交、排队与取消；同一场景哈希默认排队',
      [btn('刷新', () => window.FE.reloadJobs(), 'soft')]));

    const form = el('div', { class: 'grid g3' }, [
      el('label', { class: 'field' }, [
        el('span', { class: 'lb', text: '求解档位' }),
        (function () {
          const sel = el('select', {});
          [['quick', '快速档 · 100 m / 3 外层 / ≤900 s'],
            ['standard', '标准档 · 100 m / 20 外层 / ≤7 200 s']].forEach(([v, t]) => {
            const o = el('option', { value: v, text: t });
            if ((state.filters.mode || 'quick') === v) o.selected = true;
            sel.appendChild(o);
          });
          sel.addEventListener('change', () => { state.filters.mode = sel.value; });
          return sel;
        })(),
      ]),
      el('label', { class: 'field' }, [
        el('span', { class: 'lb', text: '网格分辨率' }),
        (function () {
          const sel = el('select', {});
          [100, 200, 50].forEach((v) => {
            const o = el('option', { value: String(v), text: v + ' m' + (v === 50 ? '（慢，精细）' : '') });
            if (v === 100) o.selected = true;
            sel.appendChild(o);
          });
          sel.addEventListener('change', () => { state.filters.gridRes = Number(sel.value); });
          return sel;
        })(),
      ]),
      el('div', { class: 'field' }, [
        el('span', { class: 'lb', text: '操作' }),
        btn('提交求解作业', async () => {
          status('正在提交…');
          try {
            const res = await api.submit({
              mode: state.filters.mode || 'quick',
              grid_res_m: state.filters.gridRes || 100,
              params: state.params || (sc && sc.params) || {},
            });
            toast('已受理：' + res.job_id, 'ok');
            window.FE.openJob(res.job_id);
          } catch (e) { status('提交失败', 'err'); toast(e.message, 'err'); }
        }, 'primary'),
      ]),
    ]);
    host.appendChild(card('新建作业', sc ? `${sc.region.name} · ${sc.grid.cells} 单元` : '', form));

    const rows = (state.jobs || []).map((j) => ({
      ...j,
      idText: j.job_id,
      stateText: { queued: '排队', running: '运行中', done: '已完成', failed: '失败', canceled: '已取消' }[j.status] || j.status,
      modeText: modeName(j.mode),
      prog: fmt.pct(j.progress, 0),
      elapsed: j.elapsed_s == null ? '—' : fmt.fixed(j.elapsed_s, 1) + ' s',
    }));

    host.appendChild(card('作业列表', '点击查看结果；运行中的作业可取消', rows.length ? table([
      { key: 'idText', title: '作业号' },
      { key: 'stateText', title: '状态', width: '80px' },
      { key: 'modeText', title: '档位', width: '96px' },
      { key: 'stage', title: '阶段', width: '64px' },
      { key: 'prog', title: '进度', num: true, width: '80px' },
      { key: 'plans', title: '方案', num: true, width: '64px' },
      { key: 'elapsed', title: '耗时', num: true, width: '90px' },
      {
        key: 'job_id', title: '操作', width: '120px', html: (v, r) => (
          r.status === 'running' || r.status === 'queued'
            ? `<button class="btn ghost" data-act="cancel" data-id="${esc(v)}">取消</button>`
            : `<button class="btn ghost" data-act="open" data-id="${esc(v)}">查看</button>`
        ),
      },
    ], rows, {
      onRow: (r) => window.FE.openJob(r.job_id),
    }) : emptyBox('暂无作业', '在上方提交一次求解。')));

    host.querySelectorAll('[data-act]').forEach((b) => {
      b.addEventListener('click', async (ev) => {
        ev.stopPropagation();
        const id = b.dataset.id;
        if (b.dataset.act === 'cancel') {
          try { await api.cancel(id); toast('已请求取消'); window.FE.reloadJobs(); }
          catch (e) { toast(e.message, 'err'); }
        } else window.FE.openJob(id);
      });
    });
  }

  /* ================= 5. 作业监控 ================= */
  function monitorView(host) {
    host.appendChild(pageHead('作业监控', '阶段进度、日志流与前沿增长',
      [btn('刷新', () => window.FE.reloadJob(), 'soft'),
        btn('查看前沿', () => window.FE.nav('plans'), 'primary')]));

    const job = state.job;
    if (!job) { host.appendChild(emptyBox('暂无作业', '先在"求解作业"页提交一次。')); return; }

    const st = job.job || job;
    const stages = ['S0', 'S1', 'S2', 'S3', 'S4', 'S5', 'S6'];
    const names = { S0: '预处理', S1: '划分', S2: '航向', S3: '路书', S4: 'FO', S5: '归档', S6: '指标' };
    const curIdx = stages.indexOf(st.stage);
    const stageRow = el('div', { class: 'stages' }, stages.map((s, i) => {
      const cls = 'stage' + (st.status === 'done' || (curIdx >= 0 && i < curIdx) ? ' done'
        : st.stage === s ? ' active' : '');
      return el('div', { class: cls }, [
        el('div', { class: 'mono', text: s }),
        el('div', { text: names[s] }),
      ]);
    }));

    const bar = el('div', { class: 'progress' }, [
      (function () {
        const i = el('i');
        i.style.width = Math.round((st.progress || 0) * 100) + '%';
        return i;
      })(),
    ]);

    host.appendChild(card('进度', `${st.status === 'done' ? '已完成' : st.status === 'running' ? '运行中' : st.status} · ${fmt.pct(st.progress || 0, 0)}`,
      el('div', {}, [
        bar,
        el('div', { class: 'chips', style: 'margin-top:8px' }, [
          tag(`${st.job_id}`, 'mono'),
          tag(modeName(st.mode)),
          tag(st.scenario_hash ? st.scenario_hash.slice(0, 12) : '—', 'mono'),
        ]),
        stageRow,
      ])));

    const logs = (job.logs || []).slice(-60).map((l) => {
      const kind = /失败|错误|error|异常/.test(l.msg || '') ? 'e'
        : /警告|warn/.test(l.msg || '') ? 'w' : 's';
      return `<div><span class="t">${esc(l.ts || l.time || '')}</span> <span class="${kind}">[${esc(l.stage || '')}]</span> ${esc(l.msg || '')}</div>`;
    }).join('');
    const logBox = el('div', { class: 'logbox', html: logs || '<div class="t">暂无日志</div>' });
    logBox.style.height = '260px';

    const growth = el('div', {});
    host.appendChild(el('div', { class: 'grid g-3-2' }, [
      card('运行日志', 'SSE 实时推送，完成后保留最近 80 条', logBox),
      card('前沿增长', '纵轴为当前最优覆盖率', growth),
    ]));

    const series = (job.meta && job.meta.history) || [];
    window.Charts.growthChart(growth, series.map((h) => ({ x: h.outer || h.n || 0, y: h.coverage || 0 })), { xLabel: '外层迭代' });
    if (!series.length) {
      const done = (state.plans || []).length;
      growth.innerHTML = '';
      growth.appendChild(emptyBox('暂无迭代曲线', done ? `本次作业已产出 ${done} 个方案，可到"前沿与方案"查看。` : '运行过程中会实时绘制。'));
    }

    const issues = [];
    if (st.status === 'failed' && st.error) issues.push(['阻断', 'J-FAILED', st.error]);
    if ((state.plans || []).some((p) => p.exhausted_count)) issues.push(['提示', 'E-S4-01', '存在能量耗尽折返架次，已按实际拍摄点计入覆盖']);
    host.appendChild(card('诊断', issues.length ? '需要关注的问题' : '未发现异常',
      issues.length ? table([
        { key: 'level', title: '级别', width: '64px' },
        { key: 'code', title: '代码', width: '110px' },
        { key: 'msg', title: '说明' },
      ], issues.map(([level, code, msg]) => ({ level, code, msg })))
        : emptyBox('运行正常', '阶段推进、精度校验与归档均通过。')));
  }

  /* ================= 6. 前沿与方案 ================= */
  function plansView(host) {
    host.appendChild(pageHead('前沿与方案', 'ε-支配前沿：覆盖率与总作业时间的权衡',
      [btn('导出方案表', () => exportReq('plans.csv'), 'soft')]));
    const plans = state.plans || [];
    if (!plans.length) { host.appendChild(emptyBox('暂无方案', '先提交一次求解作业。')); return; }

    if (!state.planId) state.planId = plans[0].id;
    const sel = plans.find((p) => p.id === state.planId) || plans[0];

    const chartHost = el('div', {});
    const chartCard = card('Pareto 前沿', '气泡=架次，颜色=覆盖效率 CE，点击选方案',
      el('div', {}, [chartHost]));
    const left = el('div', {}, [chartCard]);
    window.Charts.paretoChart(chartHost, plans, {
      selected: sel,
      onSelect: (p) => choosePlan(host, p, () => window.FE.rerender()),
    });

    const detailHost = el('div', {});
    host.appendChild(el('div', { class: 'split' }, [left, el('div', {}, [detailHost])]));

    const rows = plans.map((p) => ({
      ...p,
      cov: fmt.pct(p.coverage),
      t: fmt.hours(p.t_total_s),
      ce: fmt.fixed(p.CE, 1),
      knee: p.is_knee ? '拐点' : '',
      __cls: p.id === sel.id ? 'selected' : '',
    }));
    left.appendChild(card('方案表', `${plans.length} 个非支配解`, table([
      { key: 'id', title: '方案', width: '70px' },
      { key: 'cov', title: '覆盖率', num: true },
      { key: 't', title: '总时间(h)', num: true },
      { key: 'sorties', title: '架次', num: true },
      { key: 'ce', title: 'CE', num: true },
      { key: 'knee', title: '标记', width: '64px' },
    ], rows, { onRow: (r) => choosePlan(host, plans.find((p) => p.id === r.id), () => window.FE.rerender()) })));

    /* 详情 */
    const d = planDetail(sel.id);
    if (!d) {
      detailHost.appendChild(card('概览', sel.id + (sel.is_knee ? ' · 拐点' : ''), kvGrid([
        ['覆盖率', fmt.pct(sel.coverage)],
        ['覆盖单元', `${fmt.int(sel.coverage_cells)} / ${fmt.int(sel.cells)}`],
        ['总作业时间', fmt.hours(sel.t_total_s) + ' h'],
        ['无人机 / 车辆', `${fmt.hours(sel.t_uav_s)} / ${fmt.hours(sel.t_car_s)} h`],
        ['架次', sel.sorties],
        ['使用停靠点', sel.dock_count == null ? '—' : sel.dock_count],
      ]), '架次明细、车辆路书与导出在详情载入后显示。'));
      detailHost.appendChild(card('导出', '文件名含场景哈希与方案号', el('div', { class: 'chips' }, [
        btn('GeoJSON', () => exportReq(`${sel.id}.geojson`), 'soft'),
        btn('KML', () => exportReq(`${sel.id}.kml`), 'soft'),
        btn('子区 CSV', () => exportReq(`${sel.id}_subs.csv`), 'soft'),
        btn('路书 CSV', () => exportReq(`${sel.id}_route.csv`), 'soft'),
        btn('JSON', () => exportReq(`${sel.id}.json`), 'soft'),
      ])));
      api.plan(state.jobId, sel.id).then((res) => {
        state.planCache[state.jobId + ':' + sel.id] = res.plan;
        window.FE.rerender();
      }).catch((e) => toast(e.message, 'err'));
      state._exportReq = exportReq;
      return;
    }

    const subRows = (d.subs || []).map((s) => ({
      ...s,
      cov: fmt.pct(s.coverage),
      t: fmt.hours(s.t_uav_s),
      cells: s.cells_in_sub,
      cov_cells: s.coverage,
      cov: s.cells_in_sub ? fmt.pct(s.coverage / s.cells_in_sub) : '—',
      dock: s.dock,
      ex: s.exhausted ? '是' : '',
    }));
    const timeHost = el('div', {});

    const blocks = [
      card('概览', sel.id + (sel.is_knee ? ' · 拐点' : ''), kvGrid([
        ['覆盖率', fmt.pct(d.coverage)],
        ['覆盖单元', `${fmt.int(d.coverage_cells)} / ${fmt.int(d.cells)}`],
        ['总作业时间', fmt.hours(d.t_total_s) + ' h'],
        ['无人机时间', fmt.hours(d.t_uav_s) + ' h'],
        ['车辆时间', fmt.hours(d.t_car_s) + ' h'],
        ['架次', d.sorties],
        ['子区', (d.subs || []).length],
        ['使用停靠点', new Set((d.subs || []).map((s) => s.dock)).size],
      ])),
      card('时间构成', '', timeHost),
      card('车辆路书', `行驶 ${fmt.km(d.route_m)} km`,
        (d.route && d.route.order || []).length ? el('ol', { class: 'list scroll', style: 'padding-left:18px' },
          (d.route.order || []).map((i, k) => el('li', {
            text: `停靠点 #${i}　${k === 0 ? '（首个）' : ''}`,
          }))) : emptyBox('无路书', '')),
      card('架次明细', `${subRows.length} 个架次`, subRows.length ? table([
        { key: 'label', title: '子区', width: '56px' },
        { key: 'dock', title: '停靠点', width: '72px' },
        { key: 'cells', title: '单元', num: true },
        { key: 'cov_cells', title: '覆盖', num: true },
        { key: 'cov', title: '覆盖率', num: true },
        { key: 't', title: '时长(h)', num: true },
        { key: 'ex', title: '折返', width: '48px' },
      ], subRows) : emptyBox('无架次', '')),
      card('导出', '文件名含场景哈希与方案号',
        el('div', { class: 'chips' }, [
          btn('GeoJSON', () => exportReq(`${sel.id}.geojson`), 'soft'),
          btn('KML', () => exportReq(`${sel.id}.kml`), 'soft'),
          btn('子区 CSV', () => exportReq(`${sel.id}_subs.csv`), 'soft'),
          btn('路书 CSV', () => exportReq(`${sel.id}_route.csv`), 'soft'),
          btn('JSON', () => exportReq(`${sel.id}.json`), 'soft'),
        ])),
      card('溯源', '同一场景哈希 + 随机种子可复现',
        kvGrid([
          ['作业号', state.jobId],
          ['场景哈希', (state.job && state.job.scenario_hash) || '—'],
          ['引擎版本', (state.scenario && state.scenario.engine) || '—'],
          ['网格', `${(d.grid && d.grid.res_m) || 100} m`],
        ])),
    ];
    blocks.forEach((b) => detailHost.appendChild(b));
    window.Charts.stackedTime(timeHost, {
      t_fly_s: d.t_fly_s, t_hover_s: d.t_hover_s, t_prep_s: d.t_prep_s, t_car_s: d.t_car_s,
    });

    function exportReq(name) {
      if (!state.jobId) { toast('尚未选择作业', 'err'); return; }
      download(`/api/jobs/${state.jobId}/export/${name}`);
      toast('开始下载 ' + name);
    }
    state._exportReq = exportReq;
  }

  /* ================= 7. 一张图 ================= */
  function mapView(host) {
    const sc = state.scenario;
    host.appendChild(pageHead('一张图', '子区着色、架次航迹、车辆路书与停靠点',
      [btn('回到前沿', () => window.FE.nav('plans'), 'soft')]));
    if (!sc) { host.appendChild(emptyBox('正在读取区域…', '')); return; }

    const plans = state.plans || [];
    if (!plans.length) {
      host.appendChild(emptyBox('暂无方案', '提交求解后可在图上查看航迹。'));
      return;
    }
    let cur = plans.find((p) => p.id === state.planId) || plans[0];

    const mapHost = el('div', {});
    const side = el('div', {});
    host.appendChild(el('div', { class: 'split', style: 'grid-template-columns:minmax(0,3.4fr) minmax(300px,1fr)' }, [mapHost, side]));

    const picker = el('select', {});
    plans.forEach((p) => {
      const o = el('option', { value: p.id, text: `${p.id}｜覆盖 ${fmt.pct(p.coverage)}｜${fmt.hours(p.t_total_s)} h` });
      if (p.id === cur.id) o.selected = true;
      picker.appendChild(o);
    });
    picker.addEventListener('change', () => {
      cur = plans.find((p) => p.id === picker.value) || cur;
      state.planId = cur.id;
      load();
    });

    const info = el('div', {});
    const view = window.MapView(mapHost, {
      height: 560,
      onPick: (label) => {
        const d = planDetail(cur.id);
        const s = d && (d.subs || []).find((x) => x.label === label);
        if (!s) { info.innerHTML = ''; info.appendChild(kvGrid([['子区', label], ['状态', '未载入详情']])); return; }
        info.innerHTML = '';
        info.appendChild(kvGrid([
          ['子区', s.label],
          ['停靠点', s.dock],
          ['单元数', s.cells],
          ['覆盖单元', s.coverage_cells],
          ['覆盖率', fmt.pct(s.coverage)],
          ['架次时长', fmt.hours(s.t_uav_s) + ' h'],
          ['航向角', fmt.fixed(s.yaw_deg, 0) + '°'],
          ['折返', s.exhausted ? '是' : '否'],
        ]));
      },
    });
    view.setScenario(sc);

    function load() {
      api.plan(state.jobId, cur.id).then((res) => {
        state.planCache[state.jobId + ':' + cur.id] = res.plan;
        view.setPlan(res.plan);
        info.innerHTML = '';
        info.appendChild(kvGrid([
          ['方案', cur.id],
          ['覆盖率', fmt.pct(cur.coverage)],
          ['总时间', fmt.hours(cur.t_total_s) + ' h'],
          ['架次', cur.sorties],
          ['CE', fmt.fixed(cur.CE, 1) + ' s/km²'],
        ]));
        info.appendChild(el('div', { class: 'card-foot', text: '点击地图上的子区查看该架次信息。' }));
      }).catch((e) => toast(e.message, 'err'));
    }

    side.appendChild(card('显示方案', '', el('div', {}, [picker])));
    side.appendChild(card('子区信息', '点击地图单元', info));
    side.appendChild(card('图层', '左上角按钮可开关', el('div', { class: 'muted small', html:
      '子区着色 · 架次航迹 · 车辆路书 · 路网 · 停靠点<br>滚轮缩放，拖拽平移。' })));

    if (state.planCache[state.jobId + ':' + cur.id]) view.setPlan(state.planCache[state.jobId + ':' + cur.id]);
    else load();
  }

  /* ================= 8. 推荐与决策 ================= */
  function decideView(host) {
    host.appendChild(pageHead('推荐与决策', '系统只给候选与理由，采纳与否由人决定'));
    const job = state.job;
    if (!job) { host.appendChild(emptyBox('暂无推荐', '先完成一次求解。')); return; }
    const recs = job.recommendations || [];
    const plans = state.plans || [];

    if (!recs.length) { host.appendChild(emptyBox('暂无推荐', '前沿方案不足时不做推荐。')); return; }

    const cards = el('div', { class: 'grid g3' }, recs.map((r) => {
      const p = plans.find((x) => x.id === r.plan_id) || {};
      const reasons = (r.reasons || (r.reason ? r.reason.split('；') : [])).map((t, i) => el('div', { class: 'reason' }, [
        el('span', { class: 'b', text: String(i + 1) }), el('span', { text: t }),
      ]));
      return el('div', { class: 'cand' + (r.kind === 'knee' ? ' best' : '') }, [
        el('h4', {}, [el('span', { text: REC_LABEL[r.kind] || r.kind }), tag(r.plan_id, 'mono')]),
        kvGrid([
          ['覆盖率', fmt.pct(p.coverage)],
          ['总时间', fmt.hours(p.t_total_s) + ' h'],
          ['架次', p.sorties],
          ['CE', fmt.fixed(p.CE, 1)],
        ]),
        el('div', {}, reasons),
        el('div', { class: 'acts' }, [
          btn('采纳', () => decide(r, 'adopt', cards), 'primary'),
          btn('驳回', () => decide(r, 'reject'), 'soft'),
          btn('看图', () => { state.planId = r.plan_id; window.FE.nav('map'); }, 'soft'),
        ]),
      ]);
    }));
    host.appendChild(card('Top-K 候选', `${recs.length} 个候选，理由均带数字支撑`, cards));

    const list = (state.decisions || []).slice().reverse();
    host.appendChild(card('决策留痕', '只追加、不修改', list.length ? table([
      { key: 'created_at', title: '时间' },
      { key: 'plan_id', title: '方案' },
      { key: 'actionText', title: '动作' },
      { key: 'operator', title: '操作人' },
      { key: 'comment', title: '说明' },
    ], list.map((d) => ({ ...d, actionText: d.action === 'adopt' ? '采纳' : '驳回' })))
      : emptyBox('暂无记录', '采纳或驳回后在此留痕。')));

    function decide(rec, action, box) {
      const comment = action === 'adopt' ? '按推荐理由采纳为作业计划' : '本次不采用';
      api.decide({ job_id: state.jobId, plan_id: rec.plan_id, action, comment })
        .then((res) => {
          toast(action === 'adopt' ? '已采纳并留痕' : '已驳回并留痕', 'ok');
          state.decisions = (state.decisions || []).concat([res.decision]);
          window.FE.rerender();
        })
        .catch((e) => toast(e.message, 'err'));
    }
  }

  /* ================= 9. 结论与报告 ================= */
  function reportView(host) {
    host.appendChild(pageHead('结论与报告', '结论卡、口径说明与可交付报告',
      [btn('生成 Word 报告', async () => {
        if (!state.jobId) { toast('尚无作业', 'err'); return; }
        status('正在生成报告…');
        try {
          await downloadPost('/report', { job_id: state.jobId }, `报告_${state.jobId}.docx`);
          toast('报告已生成', 'ok');
          status('就绪');
        } catch (e) { status('生成失败', 'err'); toast(e.message, 'err'); }
      }, 'primary')]));

    const job = state.job;
    if (!job) { host.appendChild(emptyBox('暂无结论', '完成一次求解后可生成结论与报告。')); return; }
    const plans = state.plans || [];
    const recs = job.recommendations || [];
    const knee = recs.find((r) => r.kind === 'knee');
    const kp = knee && plans.find((p) => p.id === knee.plan_id);
    const maxp = plans.reduce((a, b) => (b.coverage > a.coverage ? b : a), plans[0] || {});
    const minp = plans.reduce((a, b) => (b.t_total_s < a.t_total_s ? b : a), plans[0] || {});

    host.appendChild(el('div', { class: 'grid g4' }, [
      kpi('推荐方案', kp ? kp.id : '—', '', kp ? '拐点解 · 边际收益最高' : ''),
      kpi('最大覆盖率', maxp ? fmt.pct(maxp.coverage) : '—', '', maxp ? `${maxp.sorties} 架次` : ''),
      kpi('最短作业时间', minp ? fmt.hours(minp.t_total_s) : '—', 'h', minp ? `${minp.sorties} 架次` : ''),
      kpi('架次区间', plans.length ? `${Math.min.apply(null, plans.map((p) => p.sorties))}–${Math.max.apply(null, plans.map((p) => p.sorties))}` : '—', '架次', '由区域面积与单架次上限推导'),
    ]));

    const cmpRows = plans.map((p) => ({
      id: p.id,
      cov: fmt.pct(p.coverage),
      t: fmt.hours(p.t_total_s),
      sorties: p.sorties,
      ce: fmt.fixed(p.CE, 1),
      aut: fmt.fixed(p.AUT / 3600, 2),
      rur: fmt.fixed(p.RUR, 2),
      elr: fmt.pct(p.ELR, 1),
      mark: p.is_knee ? '拐点' : '',
    }));
    host.appendChild(el('div', { class: 'grid g-3-2' }, [
      card('方案对比', '全部非支配解', table([
        { key: 'id', title: '方案', width: '64px' },
        { key: 'cov', title: '覆盖率', num: true },
        { key: 't', title: '总时间(h)', num: true },
        { key: 'sorties', title: '架次', num: true },
        { key: 'ce', title: 'CE', num: true },
        { key: 'rur', title: 'RUR', num: true },
        { key: 'elr', title: 'ELR', num: true },
        { key: 'mark', title: '' },
      ], cmpRows)),
      card('指标口径', '对外统一解释', kvGrid([
        ['覆盖率', '被覆盖有效单元 / 有效单元总数'],
        ['总作业时间', '无人机作业时间 + 车辆行驶时间'],
        ['CE 覆盖效率', '单位覆盖面积所需时间（s/km²）'],
        ['AUT', '单架次平均无人机作业时间（h）'],
        ['RUR 续航利用', '单架次飞行距离 / 安全可用航程'],
        ['ELR 空驶率', '往返空驶时间 / 总作业时间'],
        ['ALD', '车辆平均行驶距离'],
      ]), '与架构文档 05 共用同一份口径文案。'),
    ]));

    host.appendChild(card('管理启示', '按防控等级分档使用结论', el('div', {}, [
      el('div', { class: 'reason' }, [el('span', { class: 'b', text: '1' }),
        el('span', { text: '日常巡查按覆盖率 70% 目标编制，采用拐点方案，架次与时间成本最低。' })]),
      el('div', { class: 'reason' }, [el('span', { class: 'b', text: '2' }),
        el('span', { text: '疫点普查按 100% 覆盖编制，采用最大覆盖方案，接受架次与时间增加。' })]),
      el('div', { class: 'reason' }, [el('span', { class: 'b', text: '3' }),
        el('span', { text: '拐点之后覆盖率每提高 1 个百分点，所需架次与时间显著上升，需人工复核。' })]),
      el('div', { class: 'reason' }, [el('span', { class: 'b', text: '4' }),
        el('span', { text: '系统只给建议，作业计划须经值班长确认后方可下发。' })]),
    ])));

    host.appendChild(card('交付清单', '与作业号绑定，可追溯',
      el('div', { class: 'chips' }, [
        btn('方案对比 CSV', () => download(`/api/jobs/${state.jobId}/export/plans.csv`), 'soft'),
        btn('推荐方案 GeoJSON', () => kp && download(`/api/jobs/${state.jobId}/export/${kp.id}.geojson`), 'soft'),
        btn('Word 报告', async () => {
          try { await downloadPost('/report', { job_id: state.jobId }, `报告_${state.jobId}.docx`); }
          catch (e) { toast(e.message, 'err'); }
        }, 'soft'),
      ])));
  }

  window.Views = {
    workbench, data: dataView, params: paramsView, jobs: jobsView,
    monitor: monitorView, plans: plansView, map: mapView,
    decide: decideView, report: reportView,
  };

  window.VIEW_META = [
    { id: 'workbench', title: '工作台', icon: 'grid' },
    { id: 'data', title: '数据管理', icon: 'db' },
    { id: 'params', title: '场景与参数', icon: 'slider' },
    { id: 'jobs', title: '求解作业', icon: 'play' },
    { id: 'monitor', title: '作业监控', icon: 'pulse' },
    { id: 'plans', title: '前沿与方案', icon: 'scatter' },
    { id: 'map', title: '一张图', icon: 'map' },
    { id: 'decide', title: '推荐与决策', icon: 'check' },
    { id: 'report', title: '结论与报告', icon: 'doc' },
  ];
})();
