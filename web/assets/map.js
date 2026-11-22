/* 一张图：Canvas 渲染区域、网格单元、停靠点、车辆路书与架次航迹（无底图、纯离线） */
(function () {
  'use strict';
  const { el } = window.FE;

  const PALETTE = [];
  for (let i = 0; i < 64; i++) {
    const h = 158 + (i * 23) % 132;      // 158°–290°：青绿 → 蓝紫，避开刺眼红黄
    const s = 38 + (i % 4) * 8;
    const l = 42 + (i % 3) * 8;
    PALETTE.push(`hsl(${h},${s}%,${l}%)`);
  }
  const colorOf = (label) => PALETTE[Math.abs(label) % PALETTE.length];

  function MapView(host, opts) {
    opts = opts || {};
    const wrap = el('div', { class: 'map-wrap' });
    wrap.style.height = (opts.height || 520) + 'px';
    const canvas = document.createElement('canvas');
    wrap.appendChild(canvas);
    const hover = el('div', { class: 'map-hover' });
    wrap.appendChild(hover);

    const tools = el('div', { class: 'map-tools' });
    const legend = el('div', { class: 'map-legend' });
    const ctrl = el('div', { class: 'map-ctrl' });
    wrap.appendChild(tools);
    wrap.appendChild(legend);
    wrap.appendChild(ctrl);
    host.innerHTML = '';
    host.appendChild(wrap);

    const ctx = canvas.getContext('2d');
    const view = { scale: 1, ox: 0, oy: 0, ready: false };
    let sc = null, plan = null, base = null, dirty = true;
    let hoverPoint = null;

    const layers = {
      cells: true, docks: true, route: true, sorties: true, roads: true, outline: true,
    };

    function bboxOfScenario() {
      const b = sc.region.bbox;
      return [b[0], b[1], b[2], b[3]];
    }

    function fit() {
      if (!sc) return;
      const W = canvas.clientWidth || 800, H = canvas.clientHeight || 500;
      const [minx, miny, maxx, maxy] = bboxOfScenario();
      const pad = 16;
      const s = Math.min((W - pad * 2) / (maxx - minx), (H - pad * 2) / (maxy - miny));
      view.scale = s;
      view.ox = (W - (maxx - minx) * s) / 2 - minx * s;
      view.oy = H - ((H - (maxy - miny) * s) / 2) + miny * s; // y 轴翻转
      view.ready = true;
      dirty = true;
    }

    function tx(x) { return x * view.scale + view.ox; }
    function ty(y) { return view.oy - y * view.scale; }

    function resize() {
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      const W = wrap.clientWidth, H = wrap.clientHeight;
      canvas.width = Math.round(W * dpr);
      canvas.height = Math.round(H * dpr);
      canvas.style.width = W + 'px';
      canvas.style.height = H + 'px';
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      if (!view.ready) fit();
      dirty = true;
      draw();
    }

    /* ---------- 绘制 ---------- */
    function draw() {
      const W = wrap.clientWidth, H = wrap.clientHeight;
      ctx.clearRect(0, 0, W, H);
      const bg = ctx.createLinearGradient(0, 0, 0, H);
      bg.addColorStop(0, '#0C1A17');
      bg.addColorStop(1, '#0A1512');
      ctx.fillStyle = bg;
      ctx.fillRect(0, 0, W, H);

      if (!sc || !view.ready) {
        ctx.fillStyle = '#5C7A73';
        ctx.font = '12px "Microsoft YaHei",sans-serif';
        ctx.fillText('正在装载区域数据…', 16, 26);
        return;
      }

      if (!base) base = document.createElement('canvas');

      // 底图（区域外框、空洞、路网、单元着色）缓存到离屏
      if (dirty) {
        const dpr = Math.min(2, window.devicePixelRatio || 1);
        base.width = canvas.width;
        base.height = canvas.height;
        const bctx = base.getContext('2d');
        bctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        bctx.clearRect(0, 0, W, H);
        paintRegion(bctx);
        paintCells(bctx);
        paintRoads(bctx);
        dirty = false;
      }
      ctx.drawImage(base, 0, 0, W, H);

      if (layers.sorties) paintSorties(ctx);
      if (layers.route) paintRoute(ctx);
      if (layers.docks) paintDocks(ctx);
      paintBase(ctx);
      if (hoverPoint) paintHover(ctx);
    }

    function traceRing(c, ring) {
      c.beginPath();
      ring.forEach((p, i) => (i ? c.lineTo(tx(p[0]), ty(p[1])) : c.moveTo(tx(p[0]), ty(p[1]))));
      c.closePath();
    }

    function paintRegion(c) {
      if (!layers.outline) return;
      const region = sc.region;
      c.save();
      // 区域填充
      traceRing(c, region.outer);
      c.fillStyle = 'rgba(20,60,50,0.55)';
      c.fill();
      // 空洞（负空间）用背景色覆盖
      region.holes.forEach((ring) => {
        traceRing(c, ring);
        c.fillStyle = '#0A1512';
        c.fill();
        c.strokeStyle = 'rgba(120,170,158,0.35)';
        c.lineWidth = 1;
        c.stroke();
      });
      traceRing(c, region.outer);
      c.strokeStyle = 'rgba(150,220,200,0.85)';
      c.lineWidth = 1.6;
      c.stroke();
      c.restore();
    }

    function paintCells(c) {
      if (!layers.cells || !plan) return;
      const g = plan.grid || sc.grid;
      const res = g.res_m;
      const rle = plan.labels_rle || [];
      c.save();
      c.globalAlpha = 0.55;
      rle.forEach((run) => {
        const [iy, ix0, ix1, label] = run;
        const x0 = g.bbox[0] + ix0 * res;
        const x1 = g.bbox[0] + (ix1 + 1) * res;
        const y0 = g.bbox[1] + iy * res;
        const y1 = g.bbox[1] + (iy + 1) * res;
        c.fillStyle = colorOf(label);
        c.fillRect(tx(x0), ty(y1), Math.max(1, (x1 - x0) * view.scale), Math.max(1, (y1 - y0) * view.scale));
      });
      c.restore();
    }

    function paintRoads(c) {
      if (!layers.roads) return;
      c.save();
      c.strokeStyle = 'rgba(196,214,208,0.30)';
      c.lineWidth = 0.8;
      c.beginPath();
      (sc.region.roads || []).forEach((seg) => {
        c.moveTo(tx(seg[0][0]), ty(seg[0][1]));
        c.lineTo(tx(seg[1][0]), ty(seg[1][1]));
      });
      c.stroke();
      c.restore();
    }

    function paintSorties(c) {
      if (!plan || !plan.subs) return;
      if (!plan._pathIndex) {
        plan._pathIndex = {};
        plan.subs.forEach((s) => { if (s.path && s.path.length) plan._pathIndex[s.label] = s.path; });
      }
      c.save();
      c.lineWidth = 1.1;
      Object.entries(plan._pathIndex).forEach(([label, path]) => {
        c.strokeStyle = colorOf(Number(label));
        c.globalAlpha = 0.9;
        c.beginPath();
        path.forEach((p, i) => (i ? c.lineTo(tx(p[0]), ty(p[1])) : c.moveTo(tx(p[0]), ty(p[1]))));
        c.stroke();
      });
      c.restore();
    }

    function paintRoute(c) {
      const route = plan && plan.route;
      if (!route || !route.order || !route.order.length) return;
      const docks = sc.region.docks;
      const pts = [sc.region.base]
        .concat(route.order.map((i) => [docks[i].x, docks[i].y]))
        .concat([sc.region.base]);
      c.save();
      c.strokeStyle = 'rgba(96,180,255,0.95)';
      c.lineWidth = 2;
      c.setLineDash([6, 4]);
      c.beginPath();
      pts.forEach((p, i) => (i ? c.lineTo(tx(p[0]), ty(p[1])) : c.moveTo(tx(p[0]), ty(p[1]))));
      c.stroke();
      c.restore();
    }

    function paintDocks(c) {
      if (plan && plan.subs) {
        c.save();
        plan.subs.forEach((s) => {
          if (!s.dock_xy) return;
          c.beginPath();
          c.arc(tx(s.dock_xy[0]), ty(s.dock_xy[1]), 3.4, 0, Math.PI * 2);
          c.fillStyle = '#FFD166';
          c.fill();
          c.strokeStyle = '#20302C';
          c.lineWidth = 1;
          c.stroke();
        });
        c.restore();
      } else if (layers.docks) {
        c.save();
        c.fillStyle = 'rgba(255,209,102,0.55)';
        sc.region.docks.forEach((d) => {
          c.fillRect(tx(d.x) - 1, ty(d.y) - 1, 2, 2);
        });
        c.restore();
      }
    }

    function paintBase(c) {
      const b = sc.region.base;
      c.save();
      c.beginPath();
      c.arc(tx(b[0]), ty(b[1]), 5, 0, Math.PI * 2);
      c.fillStyle = '#4ADE80';
      c.fill();
      c.strokeStyle = '#0A1512';
      c.lineWidth = 1.4;
      c.stroke();
      c.fillStyle = '#DCFCE7';
      c.font = '11px "Microsoft YaHei",sans-serif';
      c.fillText('车库', tx(b[0]) + 8, ty(b[1]) + 4);
      c.restore();
    }

    function paintHover(c) {
      const [x, y] = hoverPoint;
      c.save();
      c.strokeStyle = 'rgba(255,255,255,0.35)';
      c.setLineDash([3, 3]);
      c.beginPath();
      c.moveTo(x, 0); c.lineTo(x, wrap.clientHeight);
      c.moveTo(0, y); c.lineTo(wrap.clientWidth, y);
      c.stroke();
      c.restore();
    }

    /* ---------- 交互 ---------- */
    function toUtm(ev) {
      const r = canvas.getBoundingClientRect();
      const px = ev.clientX - r.left, py = ev.clientY - r.top;
      const x = (px - view.ox) / view.scale;
      const y = (view.oy - py) / view.scale;
      return [x, y, px, py];
    }

    let dragging = null;
    canvas.addEventListener('mousedown', (ev) => {
      dragging = { x: ev.clientX, y: ev.clientY, ox: view.ox, oy: view.oy };
      canvas.style.cursor = 'grabbing';
    });
    window.addEventListener('mouseup', () => { dragging = null; canvas.style.cursor = 'grab'; });
    canvas.addEventListener('mousemove', (ev) => {
      if (dragging) {
        view.ox = dragging.ox + (ev.clientX - dragging.x);
        view.oy = dragging.oy + (ev.clientY - dragging.y);
        dirty = true;
        draw();
        return;
      }
      const [x, y, px, py] = toUtm(ev);
      hoverPoint = [px, py];
      const inside = sc && x > sc.region.bbox[0] && x < sc.region.bbox[2] &&
                     y > sc.region.bbox[1] && y < sc.region.bbox[3];
      hover.style.display = inside ? 'block' : 'none';
      hover.style.left = Math.min(px + 12, wrap.clientWidth - 170) + 'px';
      hover.style.top = Math.max(6, py - 26) + 'px';
      if (inside) {
        hover.textContent = `${Math.round(x)}, ${Math.round(y)}`;
      }
      draw();
    });
    canvas.addEventListener('mouseleave', () => { hoverPoint = null; hover.style.display = 'none'; draw(); });
    canvas.addEventListener('wheel', (ev) => {
      ev.preventDefault();
      const r = canvas.getBoundingClientRect();
      const px = ev.clientX - r.left, py = ev.clientY - r.top;
      const k = ev.deltaY < 0 ? 1.18 : 1 / 1.18;
      const wx = (px - view.ox) / view.scale, wy = (view.oy - py) / view.scale;
      view.scale = Math.max(0.2, Math.min(60, view.scale * k));
      view.ox = px - wx * view.scale;
      view.oy = py + wy * view.scale;
      dirty = true;
      draw();
    }, { passive: false });

    canvas.addEventListener('click', (ev) => {
      if (!plan || !opts.onPick) return;
      const [x, y] = toUtm(ev);
      const g = plan.grid || sc.grid;
      const ix = Math.floor((x - g.bbox[0]) / g.res_m);
      const iy = Math.floor((y - g.bbox[1]) / g.res_m);
      let hit = null;
      for (const run of plan.labels_rle || []) {
        if (run[0] === iy && ix >= run[1] && ix <= run[2]) { hit = run[3]; break; }
      }
      if (hit != null) opts.onPick(hit);
    });

    /* ---------- 工具条 ---------- */
    function layerBtn(key, text) {
      const b = el('button', { class: 'btn', text });
      b.style.opacity = layers[key] ? '1' : '.5';
      b.addEventListener('click', () => {
        layers[key] = !layers[key];
        b.style.opacity = layers[key] ? '1' : '.5';
        dirty = true;
        draw();
      });
      return b;
    }
    tools.appendChild(layerBtn('cells', '子区着色'));
    tools.appendChild(layerBtn('sorties', '架次航迹'));
    tools.appendChild(layerBtn('route', '车辆路书'));
    tools.appendChild(layerBtn('roads', '路网'));
    tools.appendChild(layerBtn('docks', '停靠点'));

    const zin = el('button', { class: 'btn', text: '+' });
    zin.addEventListener('click', () => { view.scale *= 1.3; dirty = true; draw(); });
    const zout = el('button', { class: 'btn', text: '−' });
    zout.addEventListener('click', () => { view.scale /= 1.3; dirty = true; draw(); });
    const zfit = el('button', { class: 'btn', text: '⤢' });
    zfit.title = '复位';
    zfit.addEventListener('click', () => { fit(); draw(); });
    ctrl.appendChild(zin); ctrl.appendChild(zout); ctrl.appendChild(zfit);

    function renderLegend(n) {
      const rows = [
        ['#4ADE80', '车库/车辆基地'],
        ['#FFD166', '停靠点'],
        ['rgba(96,180,255,.95)', '车辆路书'],
        ['#9BC7BB', '架次航迹（按子区着色）'],
      ];
      legend.innerHTML = rows.map(([c, t]) =>
        `<div><i style="background:${c}"></i>${t}</div>`).join('');
      if (n != null) legend.innerHTML += `<div class="muted">子区数 ${n}</div>`;
    }

    /* ---------- 对外接口 ---------- */
    const api = {
      wrap,
      setScenario(s) { sc = s; fit(); renderLegend(null); draw(); },
      setPlan(p) {
        plan = p;
        if (p) p._pathIndex = null;
        dirty = true;
        renderLegend(p ? p.subs.length : null);
        if (p && view.ready && view.scale < 0.018) {
          // 100 m 单元在全区视图里只有 1.6 px，放大到能看出着色
          const W = wrap.clientWidth, H = wrap.clientHeight;
          const cx = (sc.region.bbox[0] + sc.region.bbox[2]) / 2;
          const cy = (sc.region.bbox[1] + sc.region.bbox[3]) / 2;
          const k = 0.022 / view.scale;
          view.scale *= k;
          view.ox = W / 2 - cx * view.scale;
          view.oy = H / 2 + cy * view.scale;
        }
        draw();
      },
      setHeight(h) { wrap.style.height = h + 'px'; resize(); },
      fit, draw, resize,
      destroy() { window.removeEventListener('mouseup', () => {}); },
    };

    const ro = new ResizeObserver(() => resize());
    ro.observe(wrap);
    return api;
  }

  window.MapView = MapView;
})();
