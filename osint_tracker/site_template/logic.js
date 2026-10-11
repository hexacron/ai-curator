// Pure logic for the OSINT radar page: no DOM access, so it can be unit-tested outside a browser.
const OsintLogic = (() => {
  'use strict';

  const BOARDS = [
    { id: 'rising', label: 'Rising' },
    { id: 'new', label: 'New this week' },
    { id: 'established', label: 'Established' },
    { id: 'adjacent', label: 'Adjacent' },
    { id: 'domains', label: 'By domain' },
  ];
  const SORTS = [
    ['rank', 'Board rank'],
    ['score', 'Score'],
    ['stars', 'Stars'],
    ['newest', 'Newest'],
    ['growth', 'Fastest growing'],
  ];
  const COMPONENTS = [
    ['momentum', 'Momentum'],
    ['activity', 'Activity'],
    ['maturity', 'Maturity'],
    ['community', 'Community'],
    ['utility', 'Utility (model-rated)'],
  ];
  const FLAG_LABELS = {
    batch_publisher: 'Owner mass-publishes repositories',
    new_owner_account: 'Owner account created around the same time',
    stars_without_forks: 'Many stars, almost no forks',
    watchers_outlier: 'Watchers out of proportion to stars',
    unclosed_issue_pile: 'Many issues, none ever closed',
    clone: 'Copy of an earlier repository',
  };
  const NEW_DAYS = 7;

  /** Only absolute http(s) URLs may become links; anything else (javascript:, data:, ...) becomes '#'. */
  function safeUrl(url) {
    try {
      const parsed = new URL(String(url));
      return parsed.protocol === 'https:' || parsed.protocol === 'http:' ? parsed.href : '#';
    } catch (error) {
      return '#';
    }
  }

  const ACRONYMS = { mcp: 'MCP', ai: 'AI', cli: 'CLI', llm: 'LLM', asm: 'ASM', ui: 'UI', osint: 'OSINT', geoint: 'GEOINT' };

  /** 'skill-mcp-native' -> 'Skill MCP native': slugs become sentence case with known acronyms upper-cased. */
  function label(slug) {
    if (!slug) return '';
    const words = String(slug).split('-').map((word) => ACRONYMS[word] || word);
    const text = words.join(' ');
    return text.charAt(0).toUpperCase() + text.slice(1);
  }

  function formatAge(days) {
    if (days < 1) return 'today';
    if (days === 1) return '1 day old';
    return `${days} days old`;
  }

  function formatNumber(value) {
    return Number(value || 0).toLocaleString('en-US');
  }

  /** How to show a rank change: nothing on the very first run, NEW for an entry that was not on the board before. */
  function changeInfo(entry, hasPrevious) {
    if (!hasPrevious) return { kind: 'none', n: 0 };
    if (entry.rank_change === null || entry.rank_change === undefined) return { kind: 'new', n: 0 };
    if (entry.rank_change > 0) return { kind: 'up', n: entry.rank_change };
    if (entry.rank_change < 0) return { kind: 'down', n: -entry.rank_change };
    return { kind: 'same', n: 0 };
  }

  function isNew(entry) {
    return entry.age_days <= NEW_DAYS;
  }

  /** The entries of one board. "domains" flattens every domain board, tagging each entry with its group. */
  function boardEntries(data, board) {
    if (board !== 'domains') return (data.boards[board] || []).map((entry) => ({ ...entry, group: null }));
    const out = [];
    for (const domain of Object.keys(data.boards.domains || {}).sort()) {
      for (const entry of data.boards.domains[domain]) out.push({ ...entry, group: domain });
    }
    return out;
  }

  function haystack(entry) {
    return [entry.full_name, entry.summary, entry.description, (entry.topics || []).join(' '),
      label(entry.domain), entry.form, entry.language].filter(Boolean).join(' ').toLowerCase();
  }

  /** filters: { query, domains:Set, form, ai, hideCaution }. Every query word must appear somewhere in the entry. */
  function matches(entry, filters) {
    if (filters.hideCaution && entry.sensitivity === 'caution') return false;
    if (filters.domains && filters.domains.size && !filters.domains.has(entry.domain)) return false;
    if (filters.form && entry.form !== filters.form) return false;
    if (filters.ai && entry.ai_role !== filters.ai) return false;
    const words = String(filters.query || '').toLowerCase().split(/\s+/).filter(Boolean);
    if (!words.length) return true;
    const text = haystack(entry);
    return words.every((word) => text.includes(word));
  }

  const SORT_KEYS = {
    rank: (a, b) => a.rank - b.rank,
    score: (a, b) => b.score - a.score,
    stars: (a, b) => b.stars - a.stars,
    newest: (a, b) => a.age_days - b.age_days,
    growth: (a, b) => b.star_rate - a.star_rate,
  };

  /** A new array; ties keep their board order (Array.prototype.sort is stable). Within "domains", groups stay together. */
  function sortEntries(entries, sort) {
    const compare = SORT_KEYS[sort] || SORT_KEYS.rank;
    return [...entries].sort((a, b) => {
      if (a.group !== b.group) return String(a.group).localeCompare(String(b.group));
      return compare(a, b);
    });
  }

  /** [[value, count], ...] most frequent first (ties alphabetical), skipping empty values. */
  function countBy(entries, key) {
    const counts = new Map();
    for (const entry of entries) {
      const value = entry[key];
      if (value) counts.set(value, (counts.get(value) || 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1] || String(a[0]).localeCompare(String(b[0])));
  }

  /**
   * Geometry for a star-count sparkline from { start, stars:[...] } (null = day not observed).
   * Returns null if fewer than two observations. Points are in a width x height box with `pad` margin.
   */
  function sparkline(history, width, height, pad) {
    if (!history || !Array.isArray(history.stars)) return null;
    const points = [];
    history.stars.forEach((value, index) => {
      if (value !== null && value !== undefined) points.push([index, value]);
    });
    if (points.length < 2) return null;
    const first = points[0][0];
    const span = points[points.length - 1][0] - first || 1;
    const values = points.map((point) => point[1]);
    const low = Math.min(...values);
    const high = Math.max(...values);
    const range = high - low;
    const scaled = points.map(([index, value]) => [
      pad + ((index - first) / span) * (width - 2 * pad),
      range === 0 ? height / 2 : height - pad - ((value - low) / range) * (height - 2 * pad),
    ]);
    return {
      points: scaled.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' '),
      last: scaled[scaled.length - 1],
      from: values[0],
      to: values[values.length - 1],
      days: span + 1,
    };
  }

  function parseBoard(hash, fallback) {
    const id = String(hash || '').replace(/^#/, '');
    return BOARDS.some((board) => board.id === id) ? id : fallback;
  }

  return {
    BOARDS, SORTS, COMPONENTS, FLAG_LABELS, NEW_DAYS,
    safeUrl, label, formatAge, formatNumber, changeInfo, isNew,
    boardEntries, matches, sortEntries, countBy, sparkline, parseBoard,
  };
})();
