// Offline browser-controller regression tests. Run with Node >=18; no app requests.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../app/templates/partials/catalog_export_script.html'), 'utf8')
    .replace(/^<script>\s*/, '').replace(/<\/script>\s*$/, '');

function deferred() {
    let resolve;
    const promise = new Promise(done => { resolve = done; });
    return {promise, resolve};
}
function response(data, code = 200, retry = null) {
    return {ok: code < 400, status: code, json: async () => data,
        headers: {get: name => name === 'Retry-After' ? retry : null}};
}
const approved = {included_families: 1, matching_rows: 1, errors: [], warnings: [],
    families: [], missing: [], can_export: true};
const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };

function browser(savedJob = null, admin = false) {
    class Element {
        constructor() { this.value = ''; this.checked = false; this.dataset = {}; this.children = []; this.handlers = {}; this.hidden = false; this.disabled = false; this.textContent = ''; }
        addEventListener(name, fn) { this.handlers[name] = fn; }
        appendChild(child) { this.children.push(child); }
        replaceChildren(...children) { this.children = children; }
    }
    const elements = new Map();
    const ids = ['catalog-export-form','export-family-codes','export-status','export-pdf-button',
        'export-stock-mode','export-min-stock','export-cancel','export-downloads','preflight-results',
        'export-preflight','export-cooldown','preflight-remove-missing','export-show-stock',
        'export-show-prices','export-output-mode','selection-count','select-page','clear-selection'];
    ids.forEach(id => elements.set(id, new Element()));
    const el = id => elements.get(id);
    el('export-show-stock').dataset.canExportQuantities = String(admin);
    el('export-stock-mode').value = 'available'; el('export-min-stock').value = '1';
    el('export-output-mode').value = 'single';
    const storage = new Map([['catalog-pdf-selection-v1', '["FAMILY"]']]);
    if (savedJob) storage.set('catalog-pdf-active-job-v1', JSON.stringify(savedJob));
    let now = 1000000, nextTimer = 0;
    const timers = new Map(), requests = [], replies = [];
    const context = {
        document: {getElementById: id => el(id) || null, querySelectorAll: () => [], createElement: () => new Element()},
        sessionStorage: {getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value)},
        Date: class extends Date { static now() { return now; } },
        setTimeout: (fn, delay) => { const id = ++nextTimer; timers.set(id, {fn, at: now + delay}); return id; },
        clearTimeout: id => timers.delete(id),
        fetch: (url, options) => {
            requests.push({url, options});
            assert.ok(replies.length, `Unexpected fetch: ${url}`);
            return Promise.resolve(replies.shift());
        }
    };
    return {
        el, requests, replies,
        start() { vm.runInNewContext(source, context); },
        async fire(id, name = 'click') { await el(id).handlers[name]({preventDefault() {}}); await flush(); },
        async advance(seconds) {
            now += seconds * 1000;
            const due = [...timers].filter(([, timer]) => timer.at <= now);
            for (const [id, timer] of due) { if (timers.delete(id)) timer.fn(); }
            await flush();
        }
    };
}

test('awaits guest bootstrap, shares token, and preserves downloads on rejected submission', async () => {
    const b = browser(), boot = deferred();
    b.replies.push(boot.promise, response(approved)); b.start();
    const check = b.fire('export-preflight');
    await flush();
    assert.equal(b.requests.length, 1);
    assert.equal(b.requests[0].url, '/linked-products/export-session');
    boot.resolve(response({csrf_token: 'guest-csrf'})); await check;
    assert.equal(b.requests[1].options.headers['X-Export-CSRF'], 'guest-csrf');
    const download = {}; b.el('export-downloads').appendChild(download);
    b.replies.push(response({detail: '<quota>', reason: 'session_quota', retry_after: 5}, 429, '5'));
    await b.fire('catalog-export-form', 'submit');
    assert.equal(b.requests[2].options.headers['X-Export-CSRF'], 'guest-csrf');
    assert.equal(b.el('export-downloads').children[0], download);
    assert.equal(b.el('export-pdf-button').disabled, true);
    assert.match(b.el('export-cooldown').textContent, /<quota>.*5s/);
    assert.equal(b.el('export-preflight').disabled, false);
    await b.fire('catalog-export-form', 'submit');
    assert.equal(b.requests.length, 3);
    await b.advance(5);
    assert.equal(b.el('export-pdf-button').disabled, false);
});

test('preflight throttle is separate and polling cannot override cooldown', async () => {
    const b = browser({id: 'a'.repeat(32), token: 'token'});
    b.replies.push(response({status: 'running', progress: 0, total: 1})); b.start(); await flush();
    b.replies.push(response({csrf_token: 'csrf'}), response({detail: 'Slow down', reason: 'preflight', retry_after: 10}, 429));
    await b.fire('export-preflight');
    assert.equal(b.el('export-preflight').disabled, true);
    assert.equal(b.el('export-cancel').disabled, false);
    b.replies.push(response({status: 'cancelled', error: 'Cancelled'}));
    await b.advance(3);
    assert.equal(b.el('export-pdf-button').disabled, true);
    assert.equal(b.el('export-preflight').disabled, true);
    await b.advance(7);
    assert.equal(b.el('export-pdf-button').disabled, false);
    assert.equal(b.el('export-preflight').disabled, false);
});

test('accepted job replaces downloads and running cancellation waits for terminal state', async () => {
    const b = browser();
    b.replies.push(response({csrf_token: 'csrf'}), response(approved)); b.start();
    await b.fire('export-preflight');
    b.el('export-downloads').appendChild({});
    b.replies.push(response({id: 'b'.repeat(32), token: 'token'}), response({status: 'running', progress: 0, total: 1}));
    await b.fire('catalog-export-form', 'submit');
    assert.equal(b.el('export-downloads').children.length, 0);
    b.replies.push(response({status: 'running', cancel_requested: true}), response({status: 'running', cancel_requested: true}));
    await b.fire('export-cancel');
    assert.equal(b.el('export-pdf-button').disabled, true);
    assert.equal(b.el('export-cancel').textContent, 'Cancelling…');
    assert.match(b.el('export-status').textContent, /Waiting for processing to stop/);
    b.replies.push(response({status: 'cancelled'})); await b.advance(3);
    assert.equal(b.el('export-pdf-button').disabled, false);
});

test('guest stock quantity tampering stays false; admin checkbox is explicit opt-in', async () => {
    for (const admin of [false, true]) {
        const b = browser(null, admin);
        b.replies.push(response({csrf_token:'csrf',can_export_quantities:admin}), response(approved));
        b.start();
        assert.equal(b.el('export-show-stock').checked, false);
        assert.equal(b.el('export-show-stock').disabled, !admin);
        await b.fire('export-preflight');
        assert.equal(JSON.parse(b.requests[1].options.body).show_stock, false);
        b.el('export-show-stock').checked = true;
        b.replies.push(response(approved));
        await b.fire('export-preflight');
        assert.equal(JSON.parse(b.requests[2].options.body).show_stock, admin);
    }
});
