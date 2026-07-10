// SVG icon sprite — referenced via <svg><use href="#icon-*"/></svg>
(function () {
  const sprite = `
<svg width="0" height="0" style="position:absolute" aria-hidden="true">
  <defs>
    <symbol id="icon-search" viewBox="0 0 16 16">
      <circle cx="7" cy="7" r="5" fill="none" stroke="currentColor" stroke-width="1.5"/>
      <path d="M11 11l3.5 3.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
    </symbol>
    <symbol id="icon-chev-right" viewBox="0 0 16 16">
      <path d="M6 3l5 5-5 5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
    </symbol>
    <symbol id="icon-arrow-left" viewBox="0 0 16 16">
      <path d="M13 8H3M7 4L3 8l4 4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
    </symbol>
    <symbol id="icon-copy" viewBox="0 0 16 16">
      <rect x="5" y="5" width="9" height="9" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.4"/>
      <path d="M11 5V3.5A1.5 1.5 0 0 0 9.5 2H3.5A1.5 1.5 0 0 0 2 3.5v6A1.5 1.5 0 0 0 3.5 11H5" fill="none" stroke="currentColor" stroke-width="1.4"/>
    </symbol>
    <symbol id="icon-check" viewBox="0 0 16 16">
      <path d="M3 8.5l3.5 3.5L13 5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
    </symbol>
    <symbol id="icon-warning" viewBox="0 0 16 16">
      <path d="M8 2L1.5 13.5h13L8 2z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/>
      <path d="M8 6.5v3M8 11.5v.01" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>
    </symbol>
    <symbol id="icon-logo" viewBox="0 0 24 24">
      <rect x="2" y="4.5" width="20" height="3" rx="1.5" fill="#51cd78"/>
      <rect x="2" y="10.5" width="14" height="3" rx="1.5" fill="#f5b74c"/>
      <rect x="2" y="16.5" width="9" height="3" rx="1.5" fill="#f76f61"/>
    </symbol>
    <symbol id="icon-terminal" viewBox="0 0 16 16">
      <rect x="1.5" y="3" width="13" height="10" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.4"/>
      <path d="M4.5 6.5L7 8l-2.5 1.5M8 10h4" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>
    </symbol>
    <symbol id="icon-info" viewBox="0 0 16 16">
      <circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" stroke-width="1.4"/>
      <path d="M8 7v4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>
      <circle cx="8" cy="4.8" r="0.9" fill="currentColor"/>
    </symbol>
  </defs>
</svg>`;
  document.addEventListener('DOMContentLoaded', () => {
    document.body.insertAdjacentHTML('afterbegin', sprite);
  });
})();

// Index page search wiring. Status / severity filters were intentionally
// dropped: the section bands already label state, and the rendered row
// order already puts the worst offenders first.
(function () {
  document.addEventListener('DOMContentLoaded', () => {
    const search = document.querySelector('[data-filter="search"]');
    const result = document.querySelector('.filter-result');
    const rows = Array.from(document.querySelectorAll('.ops-table tbody tr.op-row'));
    const allCount = rows.length;
    if (!search) return;

    function apply() {
      const q = search.value.trim().toLowerCase();
      let visible = 0;
      rows.forEach((tr) => {
        const text = tr.dataset.searchText || tr.textContent;
        const show = !q || text.toLowerCase().includes(q);
        tr.style.display = show ? '' : 'none';
        if (show) visible++;
      });

      // hide empty group headers
      document.querySelectorAll('tbody.group').forEach((tb) => {
        const live = tb.querySelectorAll('tr.op-row');
        let any = false;
        live.forEach((r) => { if (r.style.display !== 'none') any = true; });
        const header = tb.querySelector('tr.group-header-row');
        if (header) header.style.display = any ? '' : 'none';
      });

      if (result) {
        result.textContent = q === '' ? '' : `showing ${visible} of ${allCount} operations`;
      }
    }

    search.addEventListener('input', apply);
  });
})();

// Middle-truncate long operation paths. Splits a path at its last "/" into
// a collapsible head + a protected tail so narrow columns render
// "/api/v2/…/{memberId}" rather than clipping the most specific segment.
(function () {
  function splitPath(el) {
    if (el.dataset.mt) return;
    const full = el.textContent;
    const i = full.lastIndexOf('/');
    if (i <= 0) return;
    el.dataset.mt = '1';
    el.setAttribute('title', full);
    el.textContent = '';
    const head = document.createElement('span');
    head.className = 'p-head';
    head.textContent = full.slice(0, i);
    const tail = document.createElement('span');
    tail.className = 'p-tail';
    tail.textContent = full.slice(i);
    el.append(head, tail);
    el.classList.add('mt');
  }
  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.row-link .path, .op-hero-row .path, .rt-pop-op .path').forEach(splitPath);
  });
})();

// Copy-curl + cmd-pill button (anything with [data-copy-target]).
(function () {
  // navigator.clipboard is unavailable in insecure contexts (plain http, some file:// setups),
  // so fall back to a hidden textarea + execCommand instead of silently doing nothing.
  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise((resolve, reject) => {
      const area = document.createElement('textarea');
      area.value = text;
      area.style.position = 'fixed';
      area.style.opacity = '0';
      document.body.appendChild(area);
      area.select();
      try {
        document.execCommand('copy') ? resolve() : reject(new Error('copy rejected'));
      } catch (err) {
        reject(err);
      } finally {
        document.body.removeChild(area);
      }
    });
  }
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-copy-target]');
    if (!btn) return;
    const targetSel = btn.dataset.copyTarget;
    const target = targetSel ? document.querySelector(targetSel) : null;
    if (!target) return;
    copyText(target.textContent.trim()).then(() => {
      const labelEl = btn.querySelector('.label');
      const orig = labelEl ? labelEl.textContent : undefined;
      btn.classList.add('copied');
      if (labelEl) labelEl.textContent = 'copied';
      setTimeout(() => {
        btn.classList.remove('copied');
        if (labelEl && orig !== undefined) labelEl.textContent = orig;
      }, 1400);
    }).catch(() => {});
  });
})();
