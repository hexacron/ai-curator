// DOM layer for the OSINT radar page. Repository text is untrusted: it is only ever set through textContent /
// text nodes, never parsed as markup, and links go through OsintLogic.safeUrl.
(function () {
  'use strict';
  const L = OsintLogic;
  const SVG = 'http://www.w3.org/2000/svg';

  const data = JSON.parse(document.getElementById('osint-data').textContent);
  const app = document.getElementById('app');
  const state = { board: L.parseBoard(location.hash, 'rising'), query: '', sort: 'rank', form: '', ai: '', hideCaution: false, domains: new Set() };
  const ui = {};

  const BOARD_HELP = {
    rising: `Created in the last ${data.window_days} days and not a mirror or re-upload of older work, integrity checks passed, enough history to trust. Projects that handle personal data are included and labelled; use the filter to hide them. Momentum-weighted score.`,
    new: `Created in the last ${L.NEW_DAYS} days. Early signals only: lower confidence, ranked by the same momentum-weighted score.`,
    established: 'Past the emerging window, or carrying a long history from before the repository was created. Quality-weighted score.',
    adjacent: 'Infrastructure and attack-surface recon, threat intelligence, broader tools where OSINT is one feature among many, and projects the classifier could not place. Scored at 0.6 relevance.',
    domains: 'The best projects in each domain, emerging and established. Quality-weighted score.',
  };

  function el(tag, props, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key === 'class') node.className = value;
      else if (key === 'text') node.textContent = value;
      else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? '' : value);
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  function chip(text, extra) {
    return el('span', { class: `chip ${extra || ''}`.trim(), text });
  }

  function sparklineNode(entry) {
    const geometry = L.sparkline(data.history[String(entry.id)], 96, 28, 2);
    if (!geometry) return el('span', { class: 'muted small', text: 'tracking just started' });
    const svg = document.createElementNS(SVG, 'svg');
    svg.setAttribute('class', 'spark');
    svg.setAttribute('width', '96');
    svg.setAttribute('height', '28');
    svg.setAttribute('viewBox', '0 0 96 28');
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', `Stars went from ${geometry.from} to ${geometry.to} over ${geometry.days} days`);
    const title = document.createElementNS(SVG, 'title');
    title.textContent = `Stars: ${geometry.from} \u2192 ${geometry.to} over ${geometry.days} days`;
    const line = document.createElementNS(SVG, 'polyline');
    line.setAttribute('points', geometry.points);
    line.setAttribute('fill', 'none');
    line.setAttribute('stroke', 'currentColor');
    line.setAttribute('stroke-width', '1.6');
    line.setAttribute('stroke-linejoin', 'round');
    line.setAttribute('stroke-linecap', 'round');
    const dot = document.createElementNS(SVG, 'circle');
    dot.setAttribute('cx', geometry.last[0].toFixed(1));
    dot.setAttribute('cy', geometry.last[1].toFixed(1));
    dot.setAttribute('r', '2.2');
    dot.setAttribute('fill', 'currentColor');
    svg.append(title, line, dot);
    return svg;
  }

  function changeNode(entry) {
    const info = L.changeInfo(entry, data.has_previous);
    if (info.kind === 'none') return null;
    const text = { up: `\u25B2${info.n}`, down: `\u25BC${info.n}`, same: '\u2013', new: 'on board' }[info.kind];
    const title = { up: `Up ${info.n} since the last run`, down: `Down ${info.n} since the last run`,
      same: 'Unchanged since the last run', new: 'Was not on this board at the last run' }[info.kind];
    return el('span', { class: `change change-${info.kind}`, title, text });
  }

  function breakdown(entry) {
    const bars = L.COMPONENTS.map(([key, name]) => {
      const value = entry.components[key];
      const rated = value !== null && value !== undefined;
      return el('div', { class: 'bar-row' },
        el('span', { class: 'bar-label', text: name }),
        el('span', { class: 'bar' }, rated ? el('span', { class: 'bar-fill', style: `width:${Math.round(value * 100)}%` }) : null),
        el('span', { class: 'bar-value', text: rated ? `${Math.round(value * 100)}` : 'not rated' }));
    });
    const flags = (entry.integrity.flags || []).map((flag) => chip(L.FLAG_LABELS[flag] || flag, 'chip-warn'));
    const lists = [['Strengths', entry.strengths], ['Risks', entry.risks]]
      .filter(([, items]) => items && items.length)
      .map(([title, items]) => el('div', { class: 'notes' }, el('strong', { text: title }),
        el('ul', {}, items.map((item) => el('li', { text: item })))));
    return el('details', { class: 'more' },
      el('summary', { text: 'Score breakdown' }),
      el('div', { class: 'bars' }, bars),
      el('p', { class: 'small muted' },
        `Confidence ${Math.round(entry.confidence * 100)}%` + (entry.confidence < 0.5 ? ' (little history yet)' : '') +
        ` \u00B7 integrity \u00D7${entry.integrity.mult}` +
        (entry.reviewed ? ' \u00B7 reviewed by a language model' : ' \u00B7 classified by rules only')),
      flags.length ? el('div', { class: 'chips' }, flags) : null,
      lists,
      entry.domain_secondary ? el('p', { class: 'small muted', text: `Also: ${L.label(entry.domain_secondary)}` }) : null,
      (entry.topics || []).length ? el('p', { class: 'small muted', text: `Topics: ${entry.topics.join(', ')}` }) : null);
  }

  function card(entry) {
    const text = entry.summary || entry.description || 'No description available.';
    return el('article', { class: 'card' },
      el('div', { class: 'card-head' },
        el('span', { class: 'rank', text: `#${entry.rank}` }),
        changeNode(entry),
        L.isNew(entry) ? chip('NEW', 'chip-new') : null,
        el('span', { class: 'score', title: 'Score for this board', text: entry.score.toFixed(1) })),
      el('h3', {}, el('a', { href: L.safeUrl(entry.html_url), target: '_blank', rel: 'noopener noreferrer', text: entry.full_name })),
      el('p', { class: 'desc', text }),
      el('div', { class: 'chips' },
        entry.domain ? chip(L.label(entry.domain), 'chip-domain') : null,
        entry.form && entry.form !== 'unknown' ? chip(L.label(entry.form)) : null,
        entry.ai_role && entry.ai_role !== 'none' ? chip(L.label(entry.ai_role)) : null,
        entry.sensitivity === 'caution' ? chip('Caution: handles personal data', 'chip-caution') : null,
        entry.status === 'established' ? chip('Established') : null),
      el('div', { class: 'stats' },
        el('span', { text: `\u2605 ${L.formatNumber(entry.stars)}` }),
        el('span', { text: `\u2442 ${L.formatNumber(entry.forks)}` }),
        el('span', { text: L.formatAge(entry.age_days) }),
        entry.language ? el('span', { class: 'lang', text: entry.language }) : null,
        sparklineNode(entry)),
      breakdown(entry));
  }

  function currentEntries() {
    const all = L.boardEntries(data, state.board);
    const visible = all.filter((entry) => L.matches(entry, state));
    return { all, visible: L.sortEntries(visible, state.sort) };
  }

  function renderChips(all) {
    ui.chips.replaceChildren(...L.countBy(all, 'domain').map(([domain, count]) =>
      el('button', {
        type: 'button', class: 'chip chip-toggle', 'aria-pressed': state.domains.has(domain) ? 'true' : 'false',
        onclick: () => { state.domains.has(domain) ? state.domains.delete(domain) : state.domains.add(domain); render(); },
        text: `${L.label(domain)} (${count})`,
      })));
  }

  function renderOptions(select, entries, key, placeholder) {
    const current = select.value;
    select.replaceChildren(el('option', { value: '', text: placeholder }),
      ...L.countBy(entries, key).map(([value]) => el('option', { value, text: L.label(value) })));
    select.value = [...select.options].some((option) => option.value === current) ? current : '';
    return select.value;
  }

  function renderList(visible, total) {
    ui.list.replaceChildren();
    if (!visible.length) {
      ui.list.append(el('p', { class: 'empty', text: total
        ? 'No repositories match these filters.' : 'Nothing on this board yet.' }));
      return;
    }
    let group;
    for (const entry of visible) {
      if (state.board === 'domains' && entry.group !== group) {
        group = entry.group;
        ui.list.append(el('h2', { class: 'group', text: L.label(group) }));
      }
      ui.list.append(card(entry));
    }
  }

  function render() {
    const { all, visible } = currentEntries();
    state.form = renderOptions(ui.form, all, 'form', 'Any form');
    state.ai = renderOptions(ui.ai, all, 'ai_role', 'Any AI role');
    [...ui.tabs.children].forEach((tab) => tab.setAttribute('aria-selected', tab.dataset.board === state.board ? 'true' : 'false'));
    ui.help.textContent = BOARD_HELP[state.board];
    ui.count.textContent = `${visible.length} of ${all.length} shown`;
    renderChips(all);
    renderList(visible, all.length);
  }

  function selectBoard(board) {
    state.board = board;
    state.domains.clear();
    history.replaceState(null, '', `#${board}`);
    render();
  }

  function build() {
    const counts = {};
    for (const board of L.BOARDS) counts[board.id] = L.boardEntries(data, board.id).length;

    ui.tabs = el('div', { class: 'tabs', role: 'tablist', 'aria-label': 'Boards' },
      L.BOARDS.map((board) => el('button', {
        type: 'button', role: 'tab', class: 'tab', 'data-board': board.id,
        onclick: () => selectBoard(board.id), text: `${board.label} (${counts[board.id]})`,
      })));
    ui.help = el('p', { class: 'help muted' });
    ui.query = el('input', { type: 'search', placeholder: 'Search name, summary, topics\u2026', 'aria-label': 'Search',
      oninput: (event) => { state.query = event.target.value; render(); } });
    ui.sort = el('select', { 'aria-label': 'Sort by', onchange: (event) => { state.sort = event.target.value; render(); } },
      L.SORTS.map(([value, name]) => el('option', { value, text: `Sort: ${name}` })));
    ui.form = el('select', { 'aria-label': 'Form', onchange: (event) => { state.form = event.target.value; render(); } });
    ui.ai = el('select', { 'aria-label': 'AI role', onchange: (event) => { state.ai = event.target.value; render(); } });
    ui.caution = el('input', { type: 'checkbox', id: 'hide-caution',
      onchange: (event) => { state.hideCaution = event.target.checked; render(); } });
    ui.count = el('span', { class: 'muted small' });
    ui.chips = el('div', { class: 'chips filter-chips', role: 'group', 'aria-label': 'Filter by domain' });
    ui.list = el('div', { class: 'list' });

    app.replaceChildren(
      ui.tabs, ui.help,
      el('div', { class: 'controls' }, ui.query, ui.sort, ui.form, ui.ai,
        el('label', { class: 'check', for: 'hide-caution' }, ui.caution, ' Hide caution-labelled'), ui.count),
      ui.chips, ui.list);
  }

  function header() {
    const stats = data.stats || {};
    document.getElementById('stats').textContent =
      `${L.formatNumber(stats.candidates)} candidates seen \u00B7 ${L.formatNumber(stats.tracked)} tracked \u00B7 ` +
      `${L.formatNumber(stats.reviewed)} reviewed by a model \u00B7 updated ${data.generated ? data.generated.slice(0, 10) : 'never'}`;
    document.getElementById('window').textContent = String(data.window_days);
  }

  document.getElementById('theme-toggle').addEventListener('click', () => {
    const next = document.documentElement.classList.contains('dark') ? 'light' : 'dark';
    localStorage.setItem('theme', next);
    document.documentElement.classList.toggle('dark', next === 'dark');
  });

  header();
  build();
  render();
  window.addEventListener('hashchange', () => {
    const board = L.parseBoard(location.hash, state.board);
    if (board !== state.board) selectBoard(board);
  });
})();
