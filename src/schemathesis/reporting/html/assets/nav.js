(() => {
  const all = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const byId = (id) => document.getElementById(id);
  const methodOf = (el) => {
    const method = el.querySelector('.method');
    return method ? method.textContent.toLowerCase() : '';
  };
  const label = (el) => {
    const method = el.querySelector('.method');
    const path = el.querySelector('.path');
    return ((method ? `${method.textContent} ` : '') + (path ? path.textContent : '')).toLowerCase();
  };

  const bar = byId('filters');
  const TEXT_ENTRY = 'textarea, select, input:not([type=checkbox]):not([type=radio]):not([type=button]):not([type=submit]):not([type=reset])';
  const slugToKind = new Map();
  const kindToSlug = new Map();
  const codeToKind = new Map();
  let f;
  let rows = [];
  let groups = [];
  let total = 0;
  let timer;
  let query, kindSelect, methodSet, countEl, clearButton, empty;
  const reset = () => { f = {q: '', k: '', m: []}; };
  reset();
  const active = () => Boolean(f.q || f.m.length || f.k);
  const pathMatches = (method, text) => (!f.m.length || f.m.includes(method)) && (!f.q || text.includes(f.q));
  const caseMatches = (c) => (!f.k || c.k.includes(f.k)) && (c.t === null || pathMatches(c.m, c.t));

  const paint = () => {
    const on = active();
    let shown = 0;
    let other = 0;
    for (const g of groups) g.n = 0;
    for (const r of rows) {
      let ok = r.runLevel || pathMatches(r.m, r.t);
      let visible = 0;
      let hidden = 0;
      for (const c of r.cases) {
        const show = ok && (!on || caseMatches(c));
        c.el.hidden = !show;
        if (show) visible++; else hidden++;
      }
      ok = ok && (r.cases.length ? visible > 0 : !f.k);
      r.el.hidden = !ok;
      if (r.detail) r.detail.hidden = !ok;
      if (r.detail && (hidden || r.note)) {
        if (!r.note) {
          r.note = document.createElement('p');
          r.note.className = 'filtered-note';
          r.detail.firstElementChild.prepend(r.note);
        }
        r.note.hidden = !(ok && hidden);
        r.note.textContent = `${hidden} ${hidden === 1 ? 'case' : 'cases'} hidden by filters`;
      }
      if (!ok) continue;
      if (r.runLevel) other++;
      else {
        shown++;
        if (r.group) r.group.n++;
      }
    }
    for (const g of groups) {
      g.count.textContent = on ? `${g.n} of ${g.total}` : g.total;
      g.el.hidden = on && !g.n;
    }
    if (on) {
      const strong = document.createElement('b');
      strong.textContent = shown;
      countEl.replaceChildren(strong, ` of ${total} operations`);
    } else {
      countEl.textContent = `${total} operations`;
    }
    clearButton.hidden = !on;
    empty.hidden = !on || shown + other > 0;
    if (document.activeElement !== query) query.value = f.q;
    if (kindSelect) kindSelect.value = f.k;
    if (methodSet) {
      for (const b of all('[data-m]', methodSet)) b.setAttribute('aria-pressed', String(f.m.includes(b.dataset.m)));
    }
  };

  const writeHash = () => {
    const params = new URLSearchParams();
    if (f.q) params.set('q', f.q);
    if (f.k) params.set('kind', kindToSlug.get(f.k));
    if (f.m.length) params.set('method', f.m.join(','));
    const hash = params.toString().replace(/%2C/g, ',');
    try {
      history.replaceState(null, '', hash ? `#${hash}` : location.pathname + location.search);
    } catch {}
  };
  const update = () => { paint(); writeHash(); };
  const scrollBelowBar = (el) => {
    const offset = bar && getComputedStyle(bar).position === 'sticky' ? bar.offsetHeight + 8 : 8;
    requestAnimationFrame(() => window.scrollTo(0, el.getBoundingClientRect().top + window.pageYOffset - offset));
  };

  const openTo = (id) => {
    if (bar) { clearTimeout(timer); query.value = f.q; }
    const el = byId(id);
    if (!el) return;
    if (bar && active() && el.closest('[hidden]')) { reset(); paint(); query.value = f.q; }
    const group = el.closest('tbody');
    const toggle = group && group.querySelector('.group-toggle input');
    if (toggle) toggle.checked = true;
    const detail = el.closest('.detail-row');
    const row = detail ? detail.previousElementSibling : el.closest('.has-details');
    const box = row && row.querySelector('.row-open');
    if (box) box.checked = true;
    for (let p = el; p; p = p.parentElement) if (p.tagName === 'DETAILS') p.open = true;
    if (bar) scrollBelowBar(el);
  };
  const readHash = () => {
    const hash = location.hash.slice(1);
    if (!hash) {
      if (bar) { clearTimeout(timer); reset(); paint(); query.value = f.q; }
      return;
    }
    if (!hash.includes('=')) {
      let id;
      try { id = decodeURIComponent(hash); } catch { return; }
      openTo(id);
      return;
    }
    if (!bar) return;
    const params = new URLSearchParams(hash);
    clearTimeout(timer);
    reset();
    f.q = (params.get('q') || '').toLowerCase();
    const kind = params.get('kind') || '';
    f.k = slugToKind.get(kind) || codeToKind.get(kind.toUpperCase()) || '';
    f.m = (params.get('method') || '').toLowerCase().split(',').filter(Boolean);
    paint();
    query.value = f.q;
    scrollBelowBar(byId('operations'));
  };

  if (bar) {
    query = byId('fb-q');
    kindSelect = byId('fb-k');
    methodSet = byId('fb-m');
    countEl = byId('fb-count');
    clearButton = byId('fb-clear');
    empty = byId('fb-empty');
    for (const o of all('option[data-slug]', bar)) {
      slugToKind.set(o.dataset.slug, o.value);
      kindToSlug.set(o.value, o.dataset.slug);
      if (o.dataset.code) codeToKind.set(o.dataset.code, o.value);
    }
    groups = all('.ops-table .group-row').map((header) => {
      const count = header.querySelector('.group-count');
      return {el: header.parentNode, count, total: count.textContent, n: 0};
    });
    rows = all('.ops-table tr.op-row, .ops-table tr.run-level-row').map((tr) => {
      let detail = tr.nextElementSibling;
      if (detail && !detail.classList.contains('detail-row')) detail = null;
      return {
        el: tr,
        detail,
        runLevel: tr.classList.contains('run-level-row'),
        m: methodOf(tr),
        t: label(tr),
        note: null,
        group: groups.find((g) => g.el === tr.parentNode),
        cases: detail ? all('.case', detail).map((c) => {
          const owner = c.querySelector('.case-owner');
          return {
            el: c,
            k: (c.dataset.k || '').split(' '),
            m: owner ? methodOf(owner) : '',
            t: owner ? label(owner) : null,
          };
        }) : [],
      };
    });
    total = rows.filter((r) => !r.runLevel).length;
    query.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(() => { f.q = query.value.trim().toLowerCase(); update(); }, 80);
    });
    query.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && query.value) { clearTimeout(timer); query.value = ''; f.q = ''; update(); }
    });
    if (kindSelect) kindSelect.addEventListener('change', () => { f.k = kindSelect.value; update(); });
    if (methodSet) methodSet.addEventListener('click', (e) => {
      const b = e.target.closest('[data-m]');
      if (!b) return;
      const method = b.dataset.m;
      f.m = f.m.includes(method) ? f.m.filter((m) => m !== method) : [...f.m, method];
      update();
    });
    const clearAll = () => { clearTimeout(timer); reset(); query.value = ''; update(); query.focus(); };
    clearButton.addEventListener('click', clearAll);
    empty.querySelector('button').addEventListener('click', clearAll);
    document.addEventListener('keydown', (e) => {
      if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey || e.isComposing || e.defaultPrevented) return;
      const focused = document.activeElement;
      if (focused && (focused.isContentEditable || focused.matches(TEXT_ENTRY))) return;
      e.preventDefault();
      query.focus();
    });
  }
  document.addEventListener('click', (e) => {
    if (e.defaultPrevented || e.button || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const link = e.target.closest('a[href^="#"]');
    if (link && link.hash && link.hash === location.hash) readHash();
  });
  window.addEventListener('hashchange', readHash);
  readHash();
})();
