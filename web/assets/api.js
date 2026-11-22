/* 接口客户端 + 全局状态 + 小工具 */
(function () {
  'use strict';

  const BASE = '/api';

  async function req(method, path, body) {
    const opt = { method, headers: {} };
    if (body !== undefined) {
      opt.headers['Content-Type'] = 'application/json';
      opt.body = JSON.stringify(body);
    }
    const res = await fetch(BASE + path, opt);
    const ctype = res.headers.get('Content-Type') || '';
    if (!res.ok) {
      let payload = null;
      if (ctype.includes('json')) {
        payload = await res.json().catch(() => null);
      }
      const msg = payload && payload.error
        ? (payload.error.message || payload.error.code || '请求失败')
        : ('HTTP ' + res.status);
      const err = new Error(msg);
      err.payload = payload;
      throw err;
    }
    if (ctype.includes('json')) return res.json();
    return res;
  }

  const api = {
    health: () => req('GET', '/health'),
    scenario: () => req('GET', '/scenario'),
    jobs: () => req('GET', '/jobs'),
    job: (id) => req('GET', '/jobs/' + id),
    submit: (payload) => req('POST', '/jobs', payload),
    cancel: (id) => req('POST', '/jobs/' + id + '/cancel'),
    plan: (jobId, planId) => req('GET', '/jobs/' + jobId + '/plans/' + planId),
    decisions: () => req('GET', '/decisions'),
    decide: (payload) => req('POST', '/decisions', payload),
    report: (payload) => req('POST', '/report', payload),
  };

  /* ---------------- 全局状态 ---------------- */
  const state = {
    scenario: null,
    jobs: [],
    job: null,          // 当前作业详情
    jobId: null,
    plans: [],
    planId: null,
    planCache: {},      // jobId:planId -> 详情
    decisions: [],
    route: 'P2',
    filters: { mode: 'quick', gridRes: 100 },
    busy: false,
  };

  /* ---------------- 工具 ---------------- */
  const fmt = {
    int(v) { return v == null || isNaN(v) ? '—' : Math.round(v).toLocaleString('en-US'); },
    fixed(v, n) { return v == null || isNaN(v) ? '—' : Number(v).toFixed(n); },
    pct(v, n) { return v == null || isNaN(v) ? '—' : (Number(v) * 100).toFixed(n == null ? 2 : n) + '%'; },
    hours(v, n) { return v == null || isNaN(v) ? '—' : (Number(v) / 3600).toFixed(n == null ? 2 : n); },
    km(v, n) { return v == null || isNaN(v) ? '—' : (Number(v) / 1000).toFixed(n == null ? 2 : n); },
    sec(v) { return v == null || isNaN(v) ? '—' : Number(v).toFixed(1); },
    money() { return '—'; },
  };

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (v == null || v === false) continue;
        if (k === 'class') node.className = v;
        else if (k === 'html') node.innerHTML = v;
        else if (k === 'text') node.textContent = v;
        else if (k === 'dataset') Object.assign(node.dataset, v);
        else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
        else node.setAttribute(k, v);
      }
    }
    (children || []).forEach((c) => {
      if (c == null) return;
      node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    });
    return node;
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function toast(msg, kind) {
    const wrap = document.getElementById('toastWrap');
    const node = el('div', { class: 'toast ' + (kind || '') });
    node.innerHTML = '<span>' + esc(msg) + '</span>';
    wrap.appendChild(node);
    setTimeout(() => { node.style.opacity = '0'; node.style.transform = 'translateY(6px)'; }, 3200);
    setTimeout(() => node.remove(), 3600);
  }

  function status(text, kind) {
    const node = document.getElementById('statusText');
    node.textContent = text;
    node.style.color = kind === 'warn' ? 'var(--warn)' : kind === 'err' ? 'var(--danger)' : '';
  }

  function download(url, filename) {
    const a = document.createElement('a');
    a.href = url;
    if (filename) a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  async function downloadPost(path, body, filename) {
    const res = await fetch(BASE + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const payload = await res.json().catch(() => null);
      throw new Error((payload && payload.error && payload.error.message) || ('HTTP ' + res.status));
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    download(url, filename);
    setTimeout(() => URL.revokeObjectURL(url), 4000);
  }

  /* SSE 订阅 */
  function stream(jobId, handlers) {
    const es = new EventSource(BASE + '/jobs/' + jobId + '/stream');
    const on = (name) => (ev) => {
      try { handlers[name] && handlers[name](JSON.parse(ev.data)); } catch (e) { /* 忽略坏帧 */ }
    };
    es.addEventListener('log', on('log'));
    es.addEventListener('point', on('point'));
    es.addEventListener('fo', on('fo'));
    es.addEventListener('done', on('done'));
    es.addEventListener('error', on('error'));
    es.addEventListener('end', () => { es.close(); handlers.end && handlers.end(); });
    es.onerror = () => { /* 浏览器自动重连，作业结束后由 end 关闭 */ };
    return es;
  }

  window.FE = { api, state, fmt, el, esc, toast, status, download, downloadPost, stream };
})();
