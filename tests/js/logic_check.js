// Checks for the page's pure logic (osint_tracker/site_template/logic.js). Plain node:assert, no framework, so it
// runs under `node tests/js/logic_check.js` (CI) or any compatible runtime. Exit code 1 on the first failure set.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '..', '..', 'osint_tracker', 'site_template', 'logic.js'), 'utf8');
const L = new Function(`${source}\nreturn OsintLogic;`)();

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

const entry = (id, over = {}) => ({
  id, rank: id, full_name: `o/r${id}`, summary: '', description: '', topics: [], domain: 'social-media', form: 'cli',
  ai_role: 'none', sensitivity: 'none', score: 50, stars: 10, age_days: 20, star_rate: 1, group: null, ...over,
});

test('safeUrl only lets http(s) through', () => {
  assert.equal(L.safeUrl('https://github.com/o/r'), 'https://github.com/o/r');
  assert.equal(L.safeUrl('http://example.com/x'), 'http://example.com/x');
  for (const bad of ['javascript:alert(1)', 'JaVaScRiPt:alert(1)', 'data:text/html,<script>', 'vbscript:x', '//evil.com', '/relative', '', null, undefined, 'not a url']) {
    assert.equal(L.safeUrl(bad), '#', String(bad));
  }
});

test('label turns slugs into readable text and upper-cases known acronyms', () => {
  assert.equal(L.label('identity-lookup'), 'Identity lookup');
  assert.equal(L.label('skill-mcp-native'), 'Skill MCP native');
  assert.equal(L.label('infrastructure-asm'), 'Infrastructure ASM');
  assert.equal(L.label('cli'), 'CLI');
  assert.equal(L.label('llm-assisted'), 'LLM assisted');
  assert.equal(L.label(null), '');
});

test('changeInfo shows nothing on the first run, NEW for newcomers, and signed moves otherwise', () => {
  assert.deepEqual(L.changeInfo({ rank_change: 3 }, false), { kind: 'none', n: 0 });
  assert.deepEqual(L.changeInfo({ rank_change: null }, true), { kind: 'new', n: 0 });
  assert.deepEqual(L.changeInfo({ rank_change: 3 }, true), { kind: 'up', n: 3 });
  assert.deepEqual(L.changeInfo({ rank_change: -2 }, true), { kind: 'down', n: 2 });
  assert.deepEqual(L.changeInfo({ rank_change: 0 }, true), { kind: 'same', n: 0 });
});

test('isNew is true up to a week old', () => {
  assert.equal(L.isNew({ age_days: 7 }), true);
  assert.equal(L.isNew({ age_days: 8 }), false);
});

test('matches requires every query word, anywhere in the entry, case-insensitively', () => {
  const e = entry(1, { full_name: 'Alice/GeoSleuth', summary: 'Finds where a photo was taken', topics: ['osint', 'exif'] });
  assert.equal(L.matches(e, { query: 'photo sleuth' }), true);
  assert.equal(L.matches(e, { query: 'EXIF' }), true);
  assert.equal(L.matches(e, { query: 'photo satellite' }), false);
  assert.equal(L.matches(e, { query: '   ' }), true);
});

test('matches applies domain, form, AI role and the caution toggle', () => {
  const e = entry(1, { domain: 'geospatial-imagery', form: 'agent-skill', ai_role: 'agentic', sensitivity: 'caution' });
  assert.equal(L.matches(e, { domains: new Set(['geospatial-imagery']) }), true);
  assert.equal(L.matches(e, { domains: new Set(['social-media']) }), false);
  assert.equal(L.matches(e, { domains: new Set() }), true);
  assert.equal(L.matches(e, { form: 'cli' }), false);
  assert.equal(L.matches(e, { ai: 'agentic' }), true);
  assert.equal(L.matches(e, { hideCaution: true }), false);
});

test('sortEntries is stable, does not mutate its input and respects every sort key', () => {
  const entries = [entry(1, { score: 5, stars: 50, age_days: 30, star_rate: 1 }), entry(2, { score: 9, stars: 50, age_days: 3, star_rate: 7 }),
    entry(3, { score: 9, stars: 10, age_days: 3, star_rate: 7 })];
  const before = entries.map((e) => e.id);
  assert.deepEqual(L.sortEntries(entries, 'score').map((e) => e.id), [2, 3, 1]);   // tie keeps board order
  assert.deepEqual(L.sortEntries(entries, 'stars').map((e) => e.id), [1, 2, 3]);
  assert.deepEqual(L.sortEntries(entries, 'newest').map((e) => e.id), [2, 3, 1]);
  assert.deepEqual(L.sortEntries(entries, 'growth').map((e) => e.id), [2, 3, 1]);
  assert.deepEqual(L.sortEntries(entries, 'rank').map((e) => e.id), [1, 2, 3]);
  assert.deepEqual(L.sortEntries(entries, 'nonsense').map((e) => e.id), [1, 2, 3]);
  assert.deepEqual(entries.map((e) => e.id), before);
});

test('sorting keeps domain groups together', () => {
  const entries = [entry(1, { group: 'b', score: 1 }), entry(2, { group: 'a', score: 1 }), entry(3, { group: 'b', score: 9 }), entry(4, { group: 'a', score: 5 })];
  assert.deepEqual(L.sortEntries(entries, 'score').map((e) => e.id), [4, 2, 3, 1]);
});

test('boardEntries flattens the domain boards in alphabetical group order and tags each entry', () => {
  const data = { boards: { rising: [entry(1)], domains: { zeta: [entry(2)], alpha: [entry(3), entry(4)] } } };
  assert.deepEqual(L.boardEntries(data, 'domains').map((e) => [e.id, e.group]), [[3, 'alpha'], [4, 'alpha'], [2, 'zeta']]);
  assert.deepEqual(L.boardEntries(data, 'rising').map((e) => e.group), [null]);
  assert.deepEqual(L.boardEntries(data, 'new'), []);
});

test('countBy ranks values by frequency, ties alphabetically, skipping empties', () => {
  const entries = [entry(1, { form: 'cli' }), entry(2, { form: 'web-app' }), entry(3, { form: 'cli' }), entry(4, { form: '' }), entry(5, { form: 'bot' })];
  assert.deepEqual(L.countBy(entries, 'form'), [['cli', 2], ['bot', 1], ['web-app', 1]]);
});

test('sparkline needs two observations, skips gaps and scales into the box', () => {
  assert.equal(L.sparkline(null, 100, 20, 2), null);
  assert.equal(L.sparkline({ start: 'x', stars: [5] }, 100, 20, 2), null);
  assert.equal(L.sparkline({ start: 'x', stars: [null, 5, null] }, 100, 20, 2), null);

  const rising = L.sparkline({ start: 'x', stars: [0, null, 10, 20] }, 100, 20, 0);
  assert.equal(rising.points, '0.0,20.0 66.7,10.0 100.0,0.0');
  assert.deepEqual([rising.from, rising.to, rising.days], [0, 20, 4]);

  const flat = L.sparkline({ start: 'x', stars: [7, 7, 7] }, 100, 20, 0);
  assert.equal(flat.points, '0.0,10.0 50.0,10.0 100.0,10.0');
});

test('parseBoard accepts only known boards', () => {
  assert.equal(L.parseBoard('#new', 'rising'), 'new');
  assert.equal(L.parseBoard('#<script>', 'rising'), 'rising');
  assert.equal(L.parseBoard('', 'adjacent'), 'adjacent');
});

let failed = 0;
for (const [name, fn] of tests) {
  try {
    fn();
    console.log(`ok   ${name}`);
  } catch (error) {
    failed += 1;
    console.log(`FAIL ${name}\n${error.message}`);
  }
}
console.log(`${tests.length - failed}/${tests.length} passed`);
process.exit(failed ? 1 : 0);
