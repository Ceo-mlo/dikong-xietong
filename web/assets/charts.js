/* 轻量 SVG 图表：前沿散点、时间构成、收敛曲线、指标对比 */
(function () {
  'use strict';
  const { fmt, el, esc } = window.FE;

  const NS = 'http://www.w3.org/2000/svg';

  function svg(w, h) {
    const node = document.createElementNS(NS, 'svg');
    node.setAttribute('viewBox', `0 0 ${w} ${h}`);
    node.setAttribute('class', 'chart');
    node.setAttribute('role', 'img');
    node.style.height = 'auto';
    return node;
  }

  function shape(tag, attrs, text) {
    const node = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null) continue;
      node.setAttribute(k, v);
    }
    if (text != null) node.textContent = text;
    return node;
  }

  function niceMax(v) {
    if (!v || v <= 0) return 1;
    const exp = Math.pow(10, Math.floor(Math.log10(v)));
    const f = v / exp;
    const m = f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10;
    return m * exp;
  }

  const COLOR = {
    primary: '#0F6E5C', accent: '#E08A1E', car: '#2563EB',
    fly: '#0F6E5C', hover: '#3FAE93', prep: '#9BC7BB',
    grid: '#EDF2F2', axis: '#DDE5E6', text: '#6B7280',
  };

  /* ---------- 1. Pareto 前沿散点（气泡=架次，色=CE） ---------- */
  function paretoChart(host, plans, opts) {
    opts = opts || {};
    host.innerHTML = '';
    if (!plans || !plans.length) {
      host.appendChild(el('div', { class: 'empty', html: '<b>暂无前沿</b>提交一次求解后显示非支配方案。' }));
      return;
    }
    const W = 760, H = 330, M = { l: 52, r: 92, t: 18, b: 34 };
    const iw = W - M.l - M.r, ih = H - M.t - M.b;
    const xs = plans.map((p) => p.coverage * 100);
    const ys = plans.map((p) => p.t_total_s / 3600);
    const ces = plans.map((p) => (isFinite(p.CE) ? p.CE : 0));
    const xmin = Math.max(0, Math.floor(Math.min(...xs) / 5) * 5 - 5);
    const xmax = Math.min(100, Math.ceil(Math.max(...xs) / 5) * 5 + 2);
    const ymax = niceMax(Math.max(...ys) * 1.08);
    const ymin = 0;
    const ceMin = Math.min(...ces), ceMax = Math.max(...ces);
    const rMax = Math.max(...plans.map((p) => p.sorties));

    const sx = (v) => M.l + ((v - xmin) / Math.max(1e-6, xmax - xmin)) * iw;
    const sy = (v) => M.t + ih - ((v - ymin) / Math.max(1e-6, ymax - ymin)) * ih;

    const root = svg(W, H);
    root.setAttribute('aria-label', '覆盖率-总时间权衡前沿');

    // 网格与坐标轴
    for (let i = 0; i <= 4; i++) {
      const v = ymin + (ymax - ymin) * i / 4;
      const y = sy(v);
      root.appendChild(shape('line', { x1: M.l, x2: M.l + iw, y1: y, y2: y, class: 'grid-line' }));
      root.appendChild(shape('text', { x: M.l - 6, y: y + 3, 'text-anchor': 'end' }, v.toFixed(1)));
    }
    for (let i = 0; i <= 5; i++) {
      const v = xmin + (xmax - xmin) * i / 5;
      const x = sx(v);
      root.appendChild(shape('line', { x1: x, x2: x, y1: M.t, y2: M.t + ih, class: 'grid-line' }));
      root.appendChild(shape('text', { x, y: H - 14, 'text-anchor': 'middle' }, v.toFixed(0) + '%'));
    }
    root.appendChild(shape('line', { x1: M.l, x2: M.l, y1: M.t, y2: M.t + ih, class: 'axis' }));
    root.appendChild(shape('line', { x1: M.l, x2: M.l + iw, y1: M.t + ih, y2: M.t + ih, class: 'axis' }));
    root.appendChild(shape('text', { x: M.l + iw / 2, y: H - 2, 'text-anchor': 'middle' }, '覆盖率（%）'));
    root.appendChild(shape('text', {
      x: 12, y: M.t + ih / 2, 'text-anchor': 'middle',
      transform: `rotate(-90 12 ${M.t + ih / 2})`,
    }, '总作业时间（h）'));

    // 指导线：越靠右下越优
    root.appendChild(shape('text', { x: M.l + iw - 4, y: M.t + ih - 6, 'text-anchor': 'end', fill: '#94A3B8' }, '越靠右下越优'));

    // 气泡
    const sorted = plans.slice().sort((a, b) => b.sorties - a.sorties);
    sorted.forEach((p) => {
      const r = 6 + 12 * Math.sqrt(p.sorties / rMax);
      const t = ceMax === ceMin ? 0.5 : (p.CE - ceMin) / (ceMax - ceMin);
      const fill = `hsl(${168 - 26 * t}, ${42 + 18 * t}%, ${46 - 8 * t}%)`;
      const cx = sx(p.coverage * 100), cy = sy(p.t_total_s / 3600);
      const g = shape('g');
      const dot = shape('circle', {
        cx, cy, r, fill, 'fill-opacity': 0.82,
        stroke: p === opts.selected ? COLOR.accent : '#fff',
        'stroke-width': p === opts.selected ? 3 : 1.2,
        class: 'pt' + (p === opts.selected ? ' sel' : ''),
        tabindex: '0', role: 'button',
        'aria-label': `方案 ${p.id}，覆盖 ${fmt.pct(p.coverage)}，总时间 ${fmt.hours(p.t_total_s)} 小时`,
      });
      dot.addEventListener('click', () => opts.onSelect && opts.onSelect(p));
      dot.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); opts.onSelect && opts.onSelect(p); }
      });
      const tip = shape('title', {}, `${p.id}｜覆盖 ${fmt.pct(p.coverage)}｜${fmt.hours(p.t_total_s)} h｜${p.sorties} 架次`);
      dot.appendChild(tip);
      g.appendChild(dot);
      if (p.is_knee) {
        g.appendChild(shape('text', { x: cx, y: cy - r - 5, 'text-anchor': 'middle', class: 'lbl', fill: COLOR.accent },
          '拐点 ' + p.id));
      } else if (p === opts.selected) {
        g.appendChild(shape('text', { x: cx, y: cy - r - 5, 'text-anchor': 'middle', class: 'lbl' }, p.id));
      }
      root.appendChild(g);
    });

    // 色带图例（右侧竖条，标注覆盖效率范围）
    const lx = M.l + iw + 14;
    root.appendChild(shape('text', { x: lx, y: M.t + 4 }, 'CE'));
    root.appendChild(shape('text', { x: lx, y: M.t + 16, fill: '#94A3B8', 'font-size': '9' }, 's/km²'));
    const barTop = M.t + 40, barH = ih - 62;
    root.appendChild(shape('text', { x: lx, y: barTop - 4, fill: '#6B7280', 'font-size': '9' },
      ceMax.toFixed(0)));
    root.appendChild(shape('text', { x: lx, y: barTop + barH + 12, fill: '#6B7280', 'font-size': '9' },
      ceMin.toFixed(0)));
    const grad = shape('linearGradient', { id: 'ceGrad', x1: 0, y1: 1, x2: 0, y2: 0 });
    grad.appendChild(shape('stop', { offset: '0%', 'stop-color': 'hsl(168, 42%, 46%)' }));
    grad.appendChild(shape('stop', { offset: '100%', 'stop-color': 'hsl(158, 52%, 40%)' }));
    const defs = shape('defs');
    defs.appendChild(grad);
    root.appendChild(defs);
    root.appendChild(shape('rect', { x: lx, y: barTop, width: 8, height: barH, rx: 3, fill: 'url(#ceGrad)' }));
    host.appendChild(root);
  }

  /* ---------- 2. 时间构成（横向堆叠条） ---------- */
  function stackedTime(host, plan) {
    host.innerHTML = '';
    if (!plan) {
      host.appendChild(el('div', { class: 'empty', html: '<b>未选择方案</b>在上方前沿或方案表中选择。' }));
      return;
    }
    const parts = [
      { k: '飞行', v: plan.t_fly_s || 0, c: COLOR.fly },
      { k: '悬停拍摄', v: plan.t_hover_s || 0, c: COLOR.hover },
      { k: '起降准备', v: plan.t_prep_s || 0, c: COLOR.prep },
      { k: '车辆行驶', v: plan.t_car_s || 0, c: COLOR.car },
    ];
    const total = parts.reduce((s, p) => s + p.v, 0) || 1;
    const W = 720, H = 96, M = { l: 8, r: 8, t: 22, b: 30 };
    const iw = W - M.l - M.r;
    const root = svg(W, H);
    let x = M.l;
    parts.forEach((p) => {
      const w = Math.max(0, (p.v / total) * iw);
      if (w > 0.4) {
        root.appendChild(shape('rect', { x, y: M.t, width: w, height: 22, fill: p.c, rx: 2 }));
        if (w > 52) {
          root.appendChild(shape('text', {
            x: x + w / 2, y: M.t + 15, 'text-anchor': 'middle', fill: '#fff', 'font-size': '10',
          }, Math.round(100 * p.v / total) + '%'));
        }
      }
      x += w;
    });
    let lx = M.l;
    parts.forEach((p) => {
      root.appendChild(shape('rect', { x: lx, y: H - 18, width: 8, height: 8, fill: p.c, rx: 2 }));
      root.appendChild(shape('text', { x: lx + 12, y: H - 10 }, `${p.k} ${(p.v / 3600).toFixed(1)}h`));
      lx += 150;
    });
    root.appendChild(shape('text', { x: M.l, y: 12 }, `合计 ${(total / 3600).toFixed(2)} h`));
    host.appendChild(root);
  }

  /* ---------- 3. 收敛 / 前沿增长曲线 ---------- */
  function growthChart(host, series, opts) {
    opts = opts || {};
    host.innerHTML = '';
    if (!series || !series.length) {
      host.appendChild(el('div', { class: 'empty', html: '<b>暂无迭代数据</b>作业运行中会实时绘制前沿增长。' }));
      return;
    }
    const W = 720, H = 200, M = { l: 44, r: 14, t: 16, b: 28 };
    const iw = W - M.l - M.r, ih = H - M.t - M.b;
    const xs = series.map((d) => d.x);
    const ys = series.map((d) => d.y);
    const xmax = Math.max(1, ...xs), ymin = Math.min(...ys) * 0.98, ymax = Math.max(...ys) * 1.02 || 1;
    const sx = (v) => M.l + (v / xmax) * iw;
    const sy = (v) => M.t + ih - ((v - ymin) / Math.max(1e-6, ymax - ymin)) * ih;
    const root = svg(W, H);
    for (let i = 0; i <= 4; i++) {
      const v = ymin + (ymax - ymin) * i / 4;
      const y = sy(v);
      root.appendChild(shape('line', { x1: M.l, x2: M.l + iw, y1: y, y2: y, class: 'grid-line' }));
      root.appendChild(shape('text', { x: M.l - 6, y: y + 3, 'text-anchor': 'end' }, (v * 100).toFixed(0) + '%'));
    }
    root.appendChild(shape('line', { x1: M.l, x2: M.l, y1: M.t, y2: M.t + ih, class: 'axis' }));
    root.appendChild(shape('line', { x1: M.l, x2: M.l + iw, y1: M.t + ih, y2: M.t + ih, class: 'axis' }));
    root.appendChild(shape('text', { x: M.l + iw / 2, y: H - 6, 'text-anchor': 'middle' }, opts.xLabel || '迭代'));

    const path = series.map((d, i) => `${i ? 'L' : 'M'}${sx(d.x).toFixed(1)},${sy(d.y).toFixed(1)}`).join(' ');
    root.appendChild(shape('path', { d: path, fill: 'none', stroke: COLOR.primary, 'stroke-width': 2 }));
    series.forEach((d) => {
      root.appendChild(shape('circle', { cx: sx(d.x), cy: sy(d.y), r: 3, fill: COLOR.primary }));
    });
    const last = series[series.length - 1];
    root.appendChild(shape('text', {
      x: Math.min(M.l + iw, sx(last.x)), y: sy(last.y) - 8, 'text-anchor': 'end', class: 'lbl',
    }, (last.y * 100).toFixed(2) + '%'));
    host.appendChild(root);
  }

  /* ---------- 4. 方案指标对比（分组柱状） ---------- */
  function compareBar(host, plans, key, opts) {
    opts = opts || {};
    host.innerHTML = '';
    if (!plans || !plans.length) {
      host.appendChild(el('div', { class: 'empty', html: '<b>暂无数据</b>' }));
      return;
    }
    const vals = plans.map((p) => Number(p[key]) || 0);
    const max = niceMax(Math.max(...vals) * 1.1);
    const W = 720, H = 180, M = { l: 46, r: 10, t: 14, b: 26 };
    const iw = W - M.l - M.r, ih = H - M.t - M.b;
    const bw = Math.max(6, Math.min(46, iw / plans.length - 8));
    const root = svg(W, H);
    for (let i = 0; i <= 3; i++) {
      const v = max * i / 3;
      const y = M.t + ih - (v / max) * ih;
      root.appendChild(shape('line', { x1: M.l, x2: M.l + iw, y1: y, y2: y, class: 'grid-line' }));
      root.appendChild(shape('text', { x: M.l - 6, y: y + 3, 'text-anchor': 'end' }, opts.fmtY ? opts.fmtY(v) : v.toFixed(0)));
    }
    plans.forEach((p, i) => {
      const v = Number(p[key]) || 0;
      const x = M.l + (i + 0.5) * (iw / plans.length) - bw / 2;
      const h = (v / max) * ih;
      const sel = p === opts.selected;
      root.appendChild(shape('rect', {
        x, y: M.t + ih - h, width: bw, height: h, rx: 2,
        fill: p.is_knee ? COLOR.accent : sel ? COLOR.primary : '#7EBFAF',
      }));
      root.appendChild(shape('text', { x: x + bw / 2, y: H - 8, 'text-anchor': 'middle' }, p.id));
    });
    host.appendChild(root);
  }

  window.Charts = { paretoChart, stackedTime, growthChart, compareBar, COLOR };
})();
