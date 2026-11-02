/** Offline regression for the real extension importer; no accounts or browser state. */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../extension/popup.js', import.meta.url), 'utf8');
const start = source.indexOf('async function grabCookies()');
const end = source.indexOf('\nasync function run()', start);
assert(start >= 0 && end > start, 'real cookie importer must be present');

function harness({ tab = { id: 7, incognito: false }, stores, selected = [], fallback = [] } = {}) {
  const calls = [];
  const context = {
    t: text => text,
    chrome: {
      tabs: { query: async details => {
        assert.equal(details.active, true);
        assert.equal(details.currentWindow, true);
        return tab ? [tab] : [];
      } },
      cookies: {
        getAllCookieStores: async () => stores ?? [{ id: 'current-store', tabIds: [7] }],
        getAll: async details => {
          calls.push(JSON.parse(JSON.stringify(details)));
          return details.url && details.storeId ? selected : fallback;
        },
      },
    },
  };
  vm.createContext(context);
  const grab = vm.runInContext(source.slice(start, end) + '\ngrabCookies', context);
  return { grab: async () => JSON.parse(JSON.stringify(await grab())), calls };
}

const normal = harness({
  selected: [{ name: 'hatch_sess', value: 'normal-current', domain: '.muse.ai', expirationDate: 123.9 }],
  fallback: [{ name: 'hatch_sess', value: 'wrong-subdomain', domain: 'other.muse.ai' }],
});
const result = await normal.grab();
assert.deepEqual(normal.calls, [{ url: 'https://muse.ai/api/session', storeId: 'current-store' }],
  'import must use current tab store and the exact session URL');
assert.equal(result.cookies.hatch_sess, 'normal-current');
assert.equal(result.expires.hatch_sess, 123);

const incognito = harness({
  tab: { id: 42, incognito: true },
  stores: [{ id: 'regular', tabIds: [7] }, { id: 'private-window', tabIds: [42] }],
  selected: [{ name: 'hatch_vml', value: 'private-current', domain: 'muse.ai' }],
  fallback: [{ name: 'hatch_vml', value: 'wrong-normal-window', domain: 'muse.ai' }],
});
assert.equal((await incognito.grab()).cookies.hatch_vml, 'private-current');
assert.equal(incognito.calls[0].storeId, 'private-window');

// Chrome orders URL-matching cookies by longest path, then earliest creation.
// Preserve the first matching name, rather than overwriting it with a root cookie.
const duplicates = harness({ selected: [
  { name: 'hatch_sess', value: 'api-path', domain: 'muse.ai', path: '/api', expirationDate: 900 },
  { name: 'hatch_sess', value: 'root-path', domain: '.muse.ai', path: '/', expirationDate: 800 },
  { name: 'hatch_vml', value: 'current-vml', domain: 'muse.ai' },
] });
const chosen = await duplicates.grab();
assert.equal(chosen.cookies.hatch_sess, 'api-path');
assert.equal(chosen.expires.hatch_sess, 900);
assert.equal(chosen.cookies.hatch_vml, 'current-vml');

// Invoking the popup from the admin page must remain supported: store selection
// depends on the current tab, not on requiring the active URL to be muse.ai.
const admin = harness({ tab: { id: 7, url: 'https://my-service.example/admin' }, selected: [] });
assert.deepEqual((await admin.grab()).cookies, {});

for (const options of [{ tab: null }, { stores: [] }, { stores: [{ id: 'other', tabIds: [99] }] }]) {
  const missing = harness(options);
  await assert.rejects(missing.grab, /窗口|标签页|存储/);
  assert.equal(missing.calls.length, 0, 'must not silently import a different cookie store');
}
console.log('PASS: active regular/incognito store; URL scope; cookie precedence; admin entry preserved');
