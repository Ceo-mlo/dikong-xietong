/* 应用外壳：路由、导航、场景装载、作业轮询与 SSE、顶栏状态 */
(function () {
  'use strict';
  const { api, state, fmt, el, esc, toast, status } = window.FE;

  const ICONS = {
    grid: '<path d="M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z"/>',
    db: '<path d="M12 3c4.4 0 8 1.3 8 3s-3.6 3-8 3-8-1.3-8-3 3.6-3 8-3Zm8 6c0 1.7-3.6 3-8 3s-8-1.3-8-3v4c0 1.7 3.6 3 8 3s8-1.3 8-3V9Zm0 7c0 1.7-3.6 3-8 3s-8-1.3-8-3v4c0 1.7 3.6 3 8 3s8-1.3 8-3v-4Z"/>',
    slider: '<path d="M4 6h10v2H4zM18 6h2v2h-2zM4 16h4v2H4zM12 16h8v2h-8z"/><circle cx="16" cy="7" r="2.4"/><circle cx="10" cy="17" r="2.4"/>',
    play: '<path d="M8 5.5v13l11-6.5-11-6.5Z"/>',
    pulse: '<path d="M3 12h4l2.5-6 4 12L16 12h5" fill="none" stroke="currentColor" stroke-width="2"/>',
    scatter: '<circle cx="7" cy="16" r="2.4"/><circle cx="13" cy="11" r="2.4"/><circle cx="18" cy="6" r="2.4"/><path d="M4 4v16h16" fill="none" stroke="currentColor" stroke-width="1.6"/>',
    map: '<path d="M9 3 3 6v15l6-3 6 3 6-3V3l-6 3-6-3Zm0 2.2 4 2v11.6l-4-2V5.2Z"/>',
    check: '<path d="M9.5 16.2 5.3 12l-1.4 1.4 5.6 5.6 11-11L19.1 6.6 9.5 16.2Z"/>',
    doc: '<path d="M6 2h8l4 4v16H6V2Zm2 2v16h8V8h-4V4H8Z"/>',
  };

  function icon(name) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    node.setAttribute('viewBox', '0 0 24 24');
    node.setAttribute('class', 'ic');
    node.setAttribute('aria-hidden', 'true');
    node.innerHTML = ICONS[name] || ICONS.grid;
    return node;
  }

  /* ---------------- 导航 ---------------- */
  const titles = {};
  function buildNav() {
    const ul = document.getElementById('navList');
    ul.innerHTML = '';
    window.VIEW_META.forEach((m, i) => {
      titles[m.id] = m.title;
      const a = el('a', { class: 'nav-item', href: '#/' + m.id });
      a.appendChild(el('span', { class: 'idx mono', text: String(i + 1).padStart(2, '0') }));
      a.appendChild(icon(m.icon));
      a.appendChild(el('span', { text: m.title }));
      a.addEventListener('click', (ev) => {
        ev.preventDefault();
        navigate(m.id);
      });
      ul.appendChild(el('li', {}, [a]));
    });
  }

  function markNav(route) {
    document.querySelectorAll('.nav-item').forEach((n) => {
      n.classList.toggle('active', n.getAttribute('href') === '#/' + route);
    });
  }

  function routeFromHash() {
    const h = (location.hash || '').replace(/^#\/?/, '');
    return window.Views[h] ? h : 'workbench';
  }

  function navigate(route) {
    if (location.hash === '#/' + route) renderRoute();
    else location.hash = '#/' + route;
  }

  let renderTimer = null;
  function renderRoute() {
    const route = routeFromHash();
    state.route = route;
    markNav(route);
    const host = document.getElementById('view');
    host.innerHTML = '';
    host.scrollTop = 0;
    const fn = window.Views[route];
    try {
      fn(host);
    } catch (err) {
      host.appendChild(el('div', { class: 'empty', html:
        `<b>页面渲染失败</b>${esc(err && err.message)}<br><span class="small">可在浏览器控制台查看完整堆栈。</span>` }));
      console.error(err);
    }
    document.title = `${titles[route] || '工作台'} · 地空协同林业监测决策系统`;
    status('就绪');
  }

  function rerender() {
    clearTimeout(renderTimer);
    renderTimer = setTimeout(renderRoute, 30);
  }

  /* ---------------- 顶栏 ---------------- */
  function updateScenarioUi() {
    const sc = state.scenario;
    const chip = document.getElementById('scenarioChip');
    const hash = document.getElementById('hashText');
    const eng = document.getElementById('engineText');
    if (!sc) {
      chip.textContent = '未连接';
      return;
    }
    chip.innerHTML = `<b>${esc(sc.region.name)}</b> · ${fmt.fixed(sc.region.area_km2, 0)} km²`;
    hash.textContent = (state.job && state.job.scenario_hash)
      ? state.job.scenario_hash.slice(0, 12) : (state.scenarioFp || 'fp 待生成');
    hash.title = hash.textContent;
    eng.textContent = (sc.engine || '地空协同林业监测决策系统') + ' · ' + fmt.int(sc.grid.cells) + ' 单元';
    const sel = document.getElementById('scenarioSelect');
    if (sel.options.length === 0) {
      const o = el('option', { value: sc.region.id, text: sc.region.name });
      sel.appendChild(o);
      sel.disabled = sel.options.length <= 1;
    }
  }

  function setJobLight(kind, text) {
    const box = document.getElementById('jobLight');
    box.className = 'job-light' + (kind === 'run' ? ' running' : kind === 'done' ? ' done'
      : kind === 'err' ? ' failed' : '');
    box.querySelector('span').textContent = text;
  }

  function startClock() {
    const node = document.getElementById('clock');
    const tick = () => {
      const d = new Date();
      node.textContent = d.toTimeString().slice(0, 8);
    };
    tick();
    setInterval(tick, 1000);
  }

  /* ---------------- 数据装载 ---------------- */
  async function loadScenario() {
    status('正在读取场景数据…');
    document.getElementById('scenarioChip').textContent = '装载中';
    try {
      const sc = await api.scenario();
      state.scenario = sc;
      state.params = state.params || JSON.parse(JSON.stringify(sc.params));
      updateScenarioUi();
      status('就绪 · 场景已装载');
      rerender();
      renderRoute();
    } catch (e) {
      status('场景读取失败：' + e.message, 'err');
      document.getElementById('scenarioChip').textContent = '服务不可用';
      toast('场景读取失败：' + e.message, 'err');
    }
  }

  async function loadJobs(silent) {
    try {
      const res = await api.jobs();
      state.jobs = res.jobs || [];
      if (!silent) rerender();
      return state.jobs;
    } catch (e) {
      if (!silent) toast('作业列表读取失败：' + e.message, 'err');
      return [];
    }
  }

  async function loadDecisions() {
    try {
      const res = await api.decisions();
      state.decisions = res.decisions || [];
    } catch (e) { /* 忽略 */ }
  }

  async function openJob(jobId) {
    if (!jobId) return;
    state.jobId = jobId;
    setJobLight('run', '载入中');
    status('正在载入作业 ' + jobId);
    try {
      const res = await api.job(jobId);
      applyJob(res);
      navigate(res.job && res.job.status === 'running' ? 'monitor' : 'plans');
    } catch (e) {
      status('作业载入失败', 'err');
      toast(e.message, 'err');
    }
  }

  function applyJob(res) {
    if (res.job && res.job.job_id) state.jobId = res.job.job_id;
    state.job = Object.assign({}, res.job || {}, {
      logs: res.logs || [],
      meta: res.meta || {},
      recommendations: res.recommendations || [],
    });
    state.plans = res.plans || [];
    if (!state.plans.some((p) => p.id === state.planId)) state.planId = state.plans.length ? state.plans[0].id : null;
    const st = res.job.status;
    setJobLight(st === 'running' ? 'run' : st === 'done' ? 'done' : st === 'failed' ? 'err' : '',
      st === 'running' ? '求解中 ' + Math.round((res.job.progress || 0) * 100) + '%'
        : st === 'done' ? '已完成' : st === 'failed' ? '失败' : st === 'canceled' ? '已取消' : '排队');
    updateScenarioUi();
    prefetchDetails();
    rerender();
  }

  /* 预取首个方案与推荐方案的详情，避免页面先渲染骨架再跳变 */
  function prefetchDetails() {
    if (!state.jobId) return;
    const ids = new Set();
    if (state.plans[0]) ids.add(state.plans[0].id);
    ((state.job && state.job.recommendations) || []).forEach((r) => {
      if (r.plan_id) ids.add(r.plan_id);
    });
    ids.forEach((pid) => {
      const key = state.jobId + ':' + pid;
      if (state.planCache[key]) return;
      api.plan(state.jobId, pid).then((res) => {
        state.planCache[key] = res.plan;
        if (state.route === 'plans' || state.route === 'map') rerender();
      }).catch(() => { /* 详情失败不影响列表 */ });
    });
  }

  async function reloadJob() {
    if (!state.jobId) return loadJobs();
    try {
      const res = await api.job(state.jobId);
      applyJob(res);
    } catch (e) { toast(e.message, 'err'); }
  }

  /* SSE：运行中的作业实时刷新日志与进度 */
  let es = null;
  function attachStream(jobId) {
    if (es) { try { es.close(); } catch (e) { /* noop */ } }
    es = window.FE.stream(jobId, {
      log(ev) {
        if (!state.job) return;
        state.job.logs = (state.job.logs || []).concat([{
          ts: new Date().toTimeString().slice(0, 8), stage: ev.stage, msg: ev.msg,
        }]).slice(-200);
        if (ev.stage) state.job.stage = ev.stage;
        if (ev.progress != null) state.job.progress = Math.max(state.job.progress || 0, ev.progress);
        setJobLight('run', '求解中 ' + Math.round((state.job.progress || 0) * 100) + '%');
        if (state.route === 'monitor') rerender();
      },
      point(ev) {
        if (!state.job) return;
        state.job.planCount = (state.job.planCount || 0) + 1;
        if (state.route === 'monitor') rerender();
      },
      fo(ev) {
        if (!state.job) return;
        state.job.meta = state.job.meta || {};
        state.job.meta.history = (state.job.meta.history || []).concat([{
          outer: ev.outer, coverage: ev.coverage, front: ev.front,
        }]);
        if (state.route === 'monitor') rerender();
      },
      done() {
        try { es.close(); } catch (e) { /* noop */ }
        es = null;
        reloadJob().then(() => loadJobs(true));
      },
      end() {
        try { es.close(); } catch (e) { /* noop */ }
        es = null;
      },
      error() { /* 浏览器会自动重连 */ },
    });
  }

  /* ---------------- 模块弹窗 ---------------- */
  function initModal() {
    const modal = document.getElementById('modal');
    const close = () => { modal.hidden = true; };
    document.getElementById('modalClose').addEventListener('click', close);
    modal.addEventListener('click', (ev) => { if (ev.target === modal) close(); });
    document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') close(); });
  }

  /* ---------------- 启动 ---------------- */
  function boot() {
    buildNav();
    initModal();
    startClock();
    document.getElementById('btnRefresh').addEventListener('click', () => {
      loadScenario();
      loadJobs();
    });
    document.getElementById('scenarioSelect').addEventListener('change', () => loadScenario());
    window.addEventListener('hashchange', renderRoute);

    window.FE.nav = navigate;
    window.FE.rerender = rerender;
    window.FE.reloadScenario = loadScenario;
    window.FE.reloadJobs = () => loadJobs();
    window.FE.reloadJob = reloadJob;
    window.FE.openJob = openJob;

    status('正在连接服务…');
    api.health().then((h) => {
      setJobLight('', '空闲');
      status('服务正常 · ' + (h.engine || ''));
      return loadScenario();
    }).then(() => Promise.all([loadJobs(true), loadDecisions()]))
      .then(() => {
        const done = (state.jobs || []).find((j) => j.status === 'done');
        if (done && !state.jobId) return api.job(done.job_id).then(applyJob);
        const running = (state.jobs || []).find((j) => j.status === 'running');
        if (running) { attachStream(running.job_id); return api.job(running.job_id).then(applyJob); }
      })
      .then(() => { renderRoute(); })
      .catch((e) => {
        status('无法连接服务：' + e.message, 'err');
        document.getElementById('view').innerHTML =
          `<div class="empty" style="margin:24px"><b>无法连接后端服务</b>
           ${esc(e.message)}<br><span class="small">请确认已运行 <code class="mono">python run.py</code>，
           并用 HTTP 地址访问本页（不要直接双击 index.html）。</span></div>`;
      });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
