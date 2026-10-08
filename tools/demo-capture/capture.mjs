#!/usr/bin/env node
/**
 * Scripted captures of the Bulk Access Request UI plugin's demo mode (?demo=<scenario>):
 *   - the doc screenshots (docs/screenshots/plugin-*.png), and
 *   - one captioned demo video (MP4), plus one clip per scene.
 *
 * Demo mode needs no tenant: the plugin answers every API call from made-up fixtures.
 * Start it first (plugin/: `npm run start:demo`), then, here:
 *
 *   npm run capture -- [--out <dir>] [--screenshots-dir <dir>] [--screenshots-only | --video-only] [--base http://localhost:4300]
 *
 * Relative paths are relative to the repository root. Output (default --out tools/demo-capture/out):
 * <out>/screenshots/*.png (or --screenshots-dir, e.g. docs/screenshots), <out>/scenes/NN-<scene>.mp4,
 * <out>/bulk-access-demo.mp4
 */
import { chromium } from 'playwright';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// ── Options ───────────────────────────────────────────────────────────────────
const argv = process.argv.slice(2);
const opt = (name, fallback) => {
  const i = argv.indexOf(`--${name}`);
  return i >= 0 && argv[i + 1] && !argv[i + 1].startsWith('--') ? argv[i + 1] : fallback;
};
const flag = (name) => argv.includes(`--${name}`);
if (flag('help')) {
  console.log('npm run capture -- [--out <dir>] [--screenshots-dir <dir>] [--screenshots-only | --video-only] [--base <url>] [--only <name,...>] [--ffmpeg <path>]');
  process.exit(0);
}
/** The repository root: relative --out and --screenshots-dir paths start here. */
const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const BASE = opt('base', 'http://localhost:4300').replace(/\/$/, '');
const OUT = path.resolve(REPO, opt('out', 'tools/demo-capture/out'));
const SHOTS_DIR = path.resolve(REPO, opt('screenshots-dir', path.join(OUT, 'screenshots')));
const FFMPEG = opt('ffmpeg', process.env.FFMPEG || 'ffmpeg');
const ONLY = opt('only', '').split(',').filter(Boolean); // limit screenshots/scenes by (partial) name
const doShots = !flag('video-only');
const doVideo = !flag('screenshots-only');
const VIEW = { width: 1440, height: 900 };

const url = (scenario) => `${BASE}/?demo=${scenario}`;
const wanted = (name) => !ONLY.length || ONLY.some((o) => name.includes(o));
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

// ── Demo data the script types or pastes (mirrors plugin/src/app/demo/fixtures.ts) ──
const FIRST = ['Ada', 'Ben', 'Chloe', 'Dev', 'Elena', 'Farah', 'Gabe', 'Hana', 'Ivan', 'Jade', 'Kofi', 'Lena', 'Mateo',
  'Nia', 'Omar', 'Priya', 'Quinn', 'Rosa', 'Sam', 'Tara', 'Umar', 'Vera', 'Wes', 'Xin', 'Yara', 'Zane', 'Iris', 'Leo', 'Maya', 'Noah'];
const LAST = ['Abara', 'Brandt', 'Castillo', 'Dubois', 'Eriksen', 'Fischer', 'Garcia', 'Haddad', 'Ito', 'Jensen', 'Kowalski',
  'Laine', 'Moreau', 'Nakamura', 'Okafor', 'Petrov', 'Quint', 'Rossi', 'Silva', 'Tanaka'];
/** The demo's 600-person crowd, as the email addresses someone would paste. */
const CROWD_EMAILS = LAST.flatMap((l) => FIRST.map((f) => `${f}.${l}@example.edu`.toLowerCase()));
const INC_BIG = 'INC0048502';   // Approvals tab: 150 people × 2 items = 300 approvals
const INC_SMALL = 'INC0048466'; // Approvals tab: 40 people × VPN, temporary 30 days

// ── Page helpers ──────────────────────────────────────────────────────────────
/** Wait until the page has loaded its data and stopped moving. */
async function settle(page, ms = 600) {
  await page.waitForLoadState('networkidle', { timeout: 5000 }).catch(() => {});
  await page.waitForFunction(() => !document.querySelector('.p-skeleton')
    && !/Searching…|Checking who already has this access…/.test(document.body.innerText), null, { timeout: 20000 })
    .catch(() => {});
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(ms);
}

async function openDemo(page, scenario) {
  await page.goto(url(scenario));
  await page.locator('h1.page__title').waitFor();
  await settle(page);
}

/** The header button of a group (My bulk requests / Approvals) whose text starts with the INC or label. */
const groupHead = (page, text) => page.locator('button.group__head', { hasText: text }).first();
async function expand(page, text, open = true) {
  const head = groupHead(page, text);
  if ((await head.getAttribute('aria-expanded')) !== String(open)) await head.click();
}
const resultButton = (page, name, label) =>
  page.locator('li.result', { hasText: name }).getByRole('button', { name: label });
/**
 * Wait until the Approvals run panel shows a finished run. Its heading says how it went
 * ("Approved", "Partly done", …), so wait for the panel's state rather than a word.
 */
async function runDone(page, timeout = 150000) {
  await page.locator('section.run:not(.run--done)').waitFor({ timeout: 5000 }).catch(() => {});
  await page.locator('section.run.run--done').waitFor({ timeout });
}

// ── Screenshots ───────────────────────────────────────────────────────────────
/**
 * One entry per doc image. `demo` is the scenario; `run` gets the page to the state the image shows.
 * `full: false` keeps it to the viewport (dialogs and the run panel, which sit at the top).
 */
const SHOTS = [
  { file: 'plugin-1-people.png', demo: 'people' },
  { file: 'plugin-2-items.png', demo: 'items' },
  { file: 'plugin-3-approver-inc.png', demo: 'approver' },
  { file: 'plugin-3b-inc-validation-error.png', demo: 'approver-error' },
  { file: 'plugin-4-review.png', demo: 'review' },
  {
    file: 'plugin-5-submitted-waiting.png', demo: 'submitted',
    run: (p) => p.getByRole('heading', { name: /Waiting for Aisha Bello/ }).waitFor({ timeout: 15000 }),
  },
  {
    file: 'plugin-6-my-bulk-requests.png', demo: 'history',
    run: async (p) => { await expand(p, 'INC0048213'); await expand(p, 'INC0048120'); },
  },
  { file: 'plugin-7-temporary-access.png', demo: 'temporary' },
  { file: 'plugin-8-parts-review.png', demo: 'parts-review' },
  // The Approvals tab, as an ordinary (non-admin) item approver.
  {
    file: 'plugin-9-approvals-groups.png', demo: 'approvals',
    run: (p) => expand(p, INC_BIG, false),
  },
  {
    file: 'plugin-10-approvals-drilldown.png', demo: 'approvals',
    run: async (p) => {
      await expand(p, INC_BIG);
      await p.getByRole('checkbox', { name: /^Include Chloe Abara, PACS/ }).click();
      await p.getByLabel(/Comment for all/).fill(`Checked against ${INC_BIG} and the radiology staff list.`);
    },
  },
  {
    file: 'plugin-11-approvals-confirm.png', demo: 'approvals', full: false,
    run: async (p) => {
      await expand(p, INC_BIG);
      await p.getByRole('checkbox', { name: /^Include Chloe Abara, PACS/ }).click();
      await p.getByLabel(/Comment for all/).fill(`Checked against ${INC_BIG} and the radiology staff list.`);
      await p.getByRole('button', { name: 'Approve 299' }).click();
      await p.getByRole('dialog').waitFor();
    },
  },
  {
    file: 'plugin-12a-approvals-progress.png', demo: 'approvals', full: false,
    run: async (p) => {
      await approveGroup(p, INC_BIG, 'Approve 300');
      await p.evaluate(() => scrollTo(0, 0));
      await p.waitForFunction(() => /\b(1[2-9]\d) of 300 sent/.test(document.body.innerText), null, { timeout: 60000 });
    },
  },
  {
    file: 'plugin-12-approvals-progress-result.png', demo: 'approvals',
    run: async (p) => {
      await expand(p, INC_SMALL);
      await p.getByRole('checkbox', { name: /^Include Ada Fischer/ }).click();
      await approveGroup(p, INC_SMALL, 'Approve 39');
      await runDone(p, 90000);
    },
  },
  {
    file: 'plugin-13-approvals-partial-retry.png', demo: 'approvals-partial',
    run: async (p) => {
      await approveGroup(p, INC_BIG, 'Approve 300');
      await runDone(p);
    },
  },
];

/** Comment, Approve N, confirm in the dialog. */
async function approveGroup(page, inc, label) {
  await expand(page, inc);
  await page.getByLabel(/Comment for all/).fill(`Checked against ${inc} and the staff list.`);
  await page.getByRole('button', { name: label }).click();
  await page.getByRole('dialog').getByRole('button', { name: label }).click();
}

async function screenshots(browser) {
  const dir = SHOTS_DIR;
  fs.mkdirSync(dir, { recursive: true });
  for (const shot of SHOTS.filter((s) => wanted(s.file))) {
    const ctx = await browser.newContext({ viewport: VIEW, deviceScaleFactor: 2, colorScheme: 'light', reducedMotion: 'reduce' });
    const page = await ctx.newPage();
    try {
      await openDemo(page, shot.demo);
      if (shot.run) await shot.run(page);
      await page.mouse.move(0, 0);
      await page.evaluate(() => (document.activeElement instanceof HTMLElement) && document.activeElement.blur());
      await settle(page, 800);
      const file = path.join(dir, shot.file);
      await page.screenshot({ path: file, fullPage: shot.full !== false, animations: 'disabled', caret: 'hide' });
      log('screenshot', shot.file);
    } catch (err) {
      log('FAILED', shot.file, err.message.split('\n')[0]);
      process.exitCode = 1;
    } finally {
      await ctx.close();
    }
  }
}

// ── Video: overlay, human-paced actions, sessions ─────────────────────────────
/**
 * Injected into every app page of the video: a caption banner (window.__demo.caption) and a visible
 * mouse pointer (headless browsers don't draw one) that follows Playwright's mouse.
 */
const OVERLAY = () => {
  const ready = (f) => (document.body ? f() : document.addEventListener('DOMContentLoaded', f));
  ready(() => {
    const style = document.createElement('style');
    style.textContent = `
      body { padding-bottom: 140px !important; }
      #demo-caption { position: fixed; left: 0; right: 0; bottom: 0; z-index: 2147483646; display: none;
        background: rgba(15, 23, 42, .94); color: #fff; padding: 18px 40px 20px; box-shadow: 0 -4px 24px rgba(0,0,0,.25);
        font: 500 26px/1.3 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
      #demo-caption .step { display: inline-block; background: #3b82f6; color: #fff; border-radius: 999px;
        padding: 2px 14px; margin-right: 14px; font-size: 20px; font-weight: 700; vertical-align: 3px; }
      #demo-caption .sub { display: block; margin-top: 6px; font-size: 19px; font-weight: 400; color: #cbd5e1; }
      #demo-cursor { position: fixed; left: 0; top: 0; z-index: 2147483647; pointer-events: none; width: 22px; height: 22px;
        transform: translate(-100px, -100px); transition: none; }
      #demo-cursor svg { filter: drop-shadow(0 1px 2px rgba(0,0,0,.4)); }
      #demo-cursor.down::after { content: ''; position: absolute; left: -14px; top: -14px; width: 28px; height: 28px;
        border-radius: 50%; background: rgba(59,130,246,.35); }`;
    document.head.appendChild(style);
    const cap = document.createElement('div');
    cap.id = 'demo-caption';
    const cur = document.createElement('div');
    cur.id = 'demo-cursor';
    cur.innerHTML = '<svg width="22" height="22" viewBox="0 0 22 22"><path d="M2 1 L2 18 L6.5 13.8 L9.6 20.5 L12.4 19.2 L9.4 12.6 L15.5 12.6 Z" fill="#111" stroke="#fff" stroke-width="1.5"/></svg>';
    document.body.append(cap, cur);
    addEventListener('mousemove', (e) => { cur.style.transform = `translate(${e.clientX - 2}px, ${e.clientY - 1}px)`; }, true);
    addEventListener('mousedown', () => cur.classList.add('down'), true);
    addEventListener('mouseup', () => setTimeout(() => cur.classList.remove('down'), 150), true);
    window.__demo = {
      caption(step, text, sub) {
        cap.innerHTML = '';
        if (step) cap.insertAdjacentHTML('beforeend', `<span class="step">${step}</span>`);
        cap.append(text);
        if (sub) { const s = document.createElement('span'); s.className = 'sub'; s.textContent = sub; cap.append(s); }
        cap.style.display = 'block';
      },
    };
  });
};

const TOTAL = 8; // numbered scenes (title and closing cards aren't numbered)
const caption = (page, n, text, sub = '') =>
  page.evaluate(([step, t, s]) => window.__demo?.caption(step, t, s), [n ? `${n}/${TOTAL}` : '', text, sub]);
const pause = (page, ms = 2000) => page.waitForTimeout(ms);

/** Scroll smoothly so the element is centred, unless it's already comfortably visible. */
async function bring(page, loc, block = 'center') {
  const box = await loc.boundingBox();
  if (box && box.y > 60 && box.y + box.height < VIEW.height - 160) return;
  await loc.evaluate((el, b) => el.scrollIntoView({ behavior: 'smooth', block: b }), block);
  await page.waitForTimeout(900);
}
async function glide(page, loc) {
  await bring(page, loc);
  const b = await loc.boundingBox();
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 30 });
  await page.waitForTimeout(250);
}
async function click(page, loc, after = 900) {
  await glide(page, loc);
  await loc.click();
  await page.waitForTimeout(after);
}
async function type(page, loc, text, delay = 90) {
  await click(page, loc, 200);
  await loc.pressSequentially(text, { delay });
  await page.waitForTimeout(500);
}
async function clear(page, loc) {
  await loc.focus();
  await page.keyboard.press('ControlOrMeta+a');
  await page.keyboard.press('Backspace');
  await page.waitForTimeout(400);
}
async function scrollTop(page) {
  await page.evaluate(() => scrollTo({ top: 0, behavior: 'smooth' }));
  await page.waitForTimeout(900);
}

/**
 * One recorded browser page. `mark(id)` starts a new scene at the current moment; the page's
 * recording is cut at the marks afterwards. Returns [{ id, file, start, end }] in seconds.
 */
async function session(browser, tmp, script) {
  const ctx = await browser.newContext({
    viewport: VIEW, deviceScaleFactor: 1, colorScheme: 'light', recordVideo: { dir: tmp, size: VIEW },
  });
  await ctx.addInitScript(OVERLAY);
  const page = await ctx.newPage();
  const t0 = Date.now();
  const marks = [];
  const mark = (id) => marks.push({ id, t: (Date.now() - t0) / 1000 });
  try {
    await script(page, mark);
  } finally {
    marks.push({ id: null, t: (Date.now() - t0) / 1000 });
    await ctx.close();
  }
  const file = await page.video().path();
  return marks.slice(0, -1).map((m, i) => ({ id: m.id, file, start: m.t, end: marks[i + 1].t }));
}

/** A full-screen title or closing card (plain HTML, no app). */
const card = (body) => `<!doctype html><html><head><meta charset="utf-8"><style>
  html, body { margin: 0; height: 100%; }
  body { display: flex; align-items: center; justify-content: center; background: linear-gradient(135deg, #0f172a, #1e3a8a);
    color: #f8fafc; font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
  .card { width: 1120px; }
  .kicker { color: #93c5fd; font-size: 22px; letter-spacing: .12em; text-transform: uppercase; font-weight: 600; }
  h1 { font-size: 54px; margin: 10px 0 8px; line-height: 1.1; }
  .lead { font-size: 26px; color: #cbd5e1; margin: 0 0 34px; }
  .grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 18px; }
  .box { background: rgba(255,255,255,.08); border: 1px solid rgba(255,255,255,.16); border-radius: 14px; padding: 20px 22px; }
  .box h2 { font-size: 23px; margin: 0 0 8px; color: #fff; }
  .box p, .box li { font-size: 18px; color: #cbd5e1; line-height: 1.45; margin: 0; }
  ul { margin: 0; padding-left: 20px; }
  .foot { margin-top: 30px; font-size: 18px; color: #94a3b8; }
  code { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: .92em; color: #e2e8f0; }
</style></head><body><div class="card">${body}</div></body></html>`;

const TITLE_CARD = card(`
  <div class="kicker">Demo mode · made-up data · no tenant</div>
  <h1>Bulk Access Request for SailPoint ISC</h1>
  <p class="lead">Many people × many Request Center items, one chosen approver, tracked by a ServiceNow INC number.</p>
  <div class="grid">
    <div class="box"><h2>Launcher + Form + Workflows</h2><p>Any user starts a bulk request from the Launchpad: an ISC Form collects
      people, items, approver and INC; ISC Workflows run the approval and request the access.</p></div>
    <div class="box"><h2>UI plugin: New request</h2><p>Admins search or paste any number of people, pick items, set temporary
      access, and follow every request on <em>My bulk requests</em>.</p></div>
    <div class="box"><h2>UI plugin: Approvals tab</h2><p>Item approvers (owners, managers) decide a whole bulk request at once,
      grouped by INC, instead of one approval per person and item.</p></div>
  </div>
  <p class="foot">Both deployments share one core and one config file. This video shows the UI plugin in its standalone demo mode.</p>`);

const CLOSING_CARD = card(`
  <div class="kicker">What it uses in ISC</div>
  <h1>Standard ISC building blocks, no custom backend</h1>
  <div class="grid" style="margin-top:28px">
    <div class="box"><h2>Workflows</h2><ul>
      <li>Generic Approval: one chosen approver per run</li>
      <li>Loop (≤ 250 people per run, so parts)</li>
      <li>Manage Access v2 with <code>removeDuration</code> for temporary access</li></ul></div>
    <div class="box"><h2>Forms + Launchers</h2><ul>
      <li>Form with REGEX-validated INC</li>
      <li>Launcher on the Launchpad, gated by a requestable “Launcher Access” profile</li></ul></div>
    <div class="box"><h2>UI Plugins</h2><ul>
      <li>Angular + PrimeNG page inside ISC</li>
      <li>Starts the workflow via the workflow test endpoint (ORG_ADMIN)</li>
      <li>Deployed with the <code>sail</code> CLI</li></ul></div>
    <div class="box"><h2>Unified Approvals API</h2><ul>
      <li><code>/v2025/generic-approvals</code> (mine, comments)</li>
      <li>Per-item <code>/{id}/approve</code> · <code>/reject</code> as the approver</li>
      <li>bulk-approve / bulk-reject for admins (≤ 50 IDs)</li></ul></div>
    <div class="box"><h2>Request status</h2><ul>
      <li><code>/v3/access-request-status</code> for <em>My bulk requests</em></li>
      <li><code>/v3/requestable-objects</code> catalog</li></ul></div>
    <div class="box"><h2>Install</h2><ul>
      <li><code>bulkaccess.py apply</code> from one config file</li>
      <li>New installs start in dry-run mode</li></ul></div>
  </div>`);

/**
 * The scenes: recording sessions (a function driving one page, cut at its marks) or still cards
 * (rendered once and held; a static page gives the screen recorder almost no frames). IDs become clip names.
 */
const SESSIONS = [
  { card: TITLE_CARD, id: '00-title', seconds: 9 },

  // Scenes 1–6: one continuous request, from people to My bulk requests.
  async (page, mark) => {
    await openDemo(page, 'new');
    mark('01-people');
    await caption(page, 1, 'New request: pick the people. Search by name, username or email…');
    await pause(page, 2200);
    const search = page.getByLabel('Search for people');
    await type(page, search, 'br', 160);
    await page.locator('li.result', { hasText: 'Bruno Marchetti' }).waitFor();
    await pause(page, 1200);
    await click(page, resultButton(page, 'Alan Bradley', 'Add'));
    await click(page, resultButton(page, 'Bruno Marchetti', 'Add'), 1200);
    for (const [term, name] of [['amelia', 'Amelia Thornton'], ['beatriz', 'Beatriz Santos']]) {
      await clear(page, search);
      await type(page, search, term);
      await page.locator('li.result', { hasText: name }).waitFor();
      await click(page, resultButton(page, name, 'Add'), 1000);
    }
    await caption(page, 1, '…or paste a list of any length: identity IDs, usernames or emails',
      'Here 597 lines. They are looked up in batches; anything not found stays in the box.');
    const paste = page.getByLabel('Or paste a list');
    await click(page, paste, 300);
    await paste.pressSequentially(`${CROWD_EMAILS[0]}\n${CROWD_EMAILS[1]}\n`, { delay: 45 });
    await paste.fill([...CROWD_EMAILS.slice(0, 596), 'j.doe@example.edu'].join('\n'));
    await pause(page, 1500);
    await click(page, page.getByRole('button', { name: 'Find these people' }), 300);
    await page.getByText(/Found 596 people/).waitFor({ timeout: 20000 });
    await pause(page, 2500);
    const chosen = page.locator('section.chosen');
    await bring(page, chosen.locator('.chosen__head'), 'start');
    await caption(page, 1, 'No people limit: 600 people go out as 3 approvals of up to 250, same INC',
      "SailPoint's workflow Loop step handles at most 250 items per run, so the plugin sends parts.");
    await pause(page, 3500);
    await click(page, page.getByRole('button', { name: 'Next ›' }), 1800);
    await click(page, page.getByRole('button', { name: 'Next: access' }), 600);
    await scrollTop(page);

    mark('02-items');
    await caption(page, 2, 'Pick the access from the Request Center catalog',
      'Access profiles, roles and entitlements (up to 25 per request); every person gets every item.');
    await pause(page, 2200);
    const filter = page.getByLabel('Filter the catalog');
    await type(page, filter, 'pacs');
    await pause(page, 900);
    await click(page, page.getByRole('checkbox', { name: 'Choose PACS Radiologist Workstation' }), 1200);
    await clear(page, filter);
    await type(page, filter, 'acme');
    await click(page, page.getByRole('checkbox', { name: 'Choose ACME Bulk Test Access' }), 1200);
    await clear(page, filter);
    await bring(page, page.locator('section.chosen'));
    await pause(page, 2000);
    await click(page, page.getByRole('button', { name: 'Next: approver' }), 600);
    await scrollTop(page);

    mark('03-temporary');
    const temp = page.locator('section.temporary');
    await bring(page, temp);
    await caption(page, 3, 'Temporary access: for a duration or until a date',
      'Sent to Manage Access v2 as removeDuration; SailPoint removes the access automatically.');
    await pause(page, 2200);
    await click(page, temp.getByText('For a duration', { exact: true }), 1200);
    const n = page.getByLabel('Duration');
    await clear(page, n);
    await type(page, n, '30', 200);
    await click(page, page.getByRole('combobox', { name: 'Unit' }), 900);
    await click(page, page.getByRole('option', { name: 'days' }), 1500);
    await pause(page, 2000);
    await scrollTop(page);

    mark('04-approver-inc');
    await caption(page, 4, 'One approver decides the whole request, and it can’t be you',
      'SailPoint would silently hand a self-approval to another admin, so the page blocks it up front.');
    await pause(page, 1800);
    const approver = page.getByRole('searchbox', { name: 'Approver', exact: true });
    await type(page, approver, 'jordan', 120);
    await page.locator('li.result', { hasText: 'Jordan Lee' }).waitFor();
    await glide(page, resultButton(page, 'Jordan Lee', 'Choose'));
    await pause(page, 2500);
    await clear(page, approver);
    await type(page, approver, 'aisha', 120);
    await click(page, resultButton(page, 'Aisha Bello', 'Choose'), 1500);
    await caption(page, 4, 'The ServiceNow INC number is validated as you type',
      'Same rule as the Launcher form (INC + 7 digits); it goes on every access item’s comment.');
    const inc = page.getByLabel('ServiceNow incident (INC) number');
    await type(page, inc, 'INC48391', 160);
    await pause(page, 3000);
    await clear(page, inc);
    await type(page, inc, 'INC0048391', 120);
    await pause(page, 1800);
    await type(page, page.getByLabel('Business justification'),
      'Hospital-wide move to the new PACS on 14 Oct: clinical readers need access for the cutover.', 30);
    await pause(page, 1800);
    await click(page, page.getByRole('button', { name: 'Next: review' }), 600);
    await scrollTop(page);

    mark('05-review-submit');
    await caption(page, 5, 'Review: 600 people × 2 items, temporary for 30 days, approved by Aisha Bello',
      'The request goes out as 3 approvals with the same INC; only approved parts are requested.');
    await pause(page, 3500);
    await bring(page, page.locator('.parts-card'));
    await pause(page, 3500);
    await caption(page, 5, 'Submit: the plugin starts the ISC workflow once per part',
      'Demo mode: the workflow and the approver are simulated in the browser; nothing reaches a tenant.');
    await click(page, page.getByRole('button', { name: 'Submit 3 approvals' }), 600);
    await scrollTop(page);
    await page.locator('table.parts').waitFor();
    await pause(page, 5500);

    mark('06-my-requests');
    await caption(page, 6, 'My bulk requests: every request by INC, part by part',
      'Approvals come from /v2025/generic-approvals; filed access requests from /v3/access-request-status.');
    await click(page, page.getByRole('button', { name: 'My bulk requests' }), 600);
    await settle(page, 1500);
    await click(page, groupHead(page, 'INC0048391'), 2500);
    await click(page, groupHead(page, 'INC0048391'), 800);
    await click(page, groupHead(page, 'INC0048120'), 1500);
    await caption(page, 6, 'An earlier 600-person request: 2 of 3 parts approved, access temporary until Nov 6',
      'Each filed access request shows its status and its removal date.');
    await bring(page, page.getByText('Access requests', { exact: true }).nth(0));
    await pause(page, 4000);
    await scrollTop(page);
    await pause(page, 800);
  },

  // Scene 7: the Approvals tab as an ordinary item approver.
  async (page, mark) => {
    await openDemo(page, 'approvals');
    await expand(page, INC_BIG, false);
    await settle(page, 300);
    mark('07-approvals');
    await caption(page, 7, 'Approvals tab: item approvers decide a bulk request at once',
      'Each person × item has its own ISC approval (owner, manager…). Here: 340 waiting, grouped by the INC in their comment.');
    await pause(page, 4500);
    await click(page, groupHead(page, INC_BIG), 1500);
    await caption(page, 7, 'Drill down: items, justification, and every person × item with its due date');
    await page.mouse.wheel(0, 500);
    await pause(page, 2500);
    await page.mouse.wheel(0, -500);
    await pause(page, 1200);
    await click(page, groupHead(page, INC_BIG), 600);
    await click(page, groupHead(page, INC_SMALL), 1500);
    await caption(page, 7, `${INC_SMALL}: VPN for 40 people, temporary for 30 days`,
      'Leave out anyone you don’t want to decide now; the rest are approved or denied together.');
    await click(page, page.getByRole('checkbox', { name: /^Include Ada Fischer/ }), 1500);
    await type(page, page.getByLabel(/Comment for all/), `Checked against ${INC_SMALL} and the ward roster.`, 35);
    await pause(page, 1200);
    await click(page, page.getByRole('button', { name: 'Approve 39' }), 1200);
    await caption(page, 7, 'Confirm: recorded as your own decision on each approval');
    await pause(page, 3000);
    await click(page, page.getByRole('dialog').getByRole('button', { name: 'Approve 39' }), 300);
    await scrollTop(page);
    await caption(page, 7, 'One /v2025/generic-approvals/{id}/approve call per item, about 8 a second',
      'Non-admins can’t use bulk-approve (403), so the page paces single calls, then re-reads each to confirm.');
    await runDone(page, 60000);
    await pause(page, 4500);
    await click(page, page.locator('section.run').getByRole('button', { name: 'Close' }), 2000);
  },

  // Scene 8: the same, when SailPoint pushes back.
  async (page, mark) => {
    await openDemo(page, 'approvals-partial');
    mark('08-approvals-partial');
    await caption(page, 8, 'When things go wrong: 300 approvals with throttling and failures',
      'This demo makes some calls answer 429/503, some fail, and has a colleague decide some first.');
    await pause(page, 3000);
    await type(page, page.getByLabel(/Comment for all/), `Checked against ${INC_BIG}.`, 40);
    await click(page, page.getByRole('button', { name: 'Approve 300' }), 1200);
    await click(page, page.getByRole('dialog').getByRole('button', { name: 'Approve 300' }), 300);
    await scrollTop(page);
    await caption(page, 8, '429 Too Many Requests and 503 are retried automatically',
      'Calls are paced to stay under the rate limit; progress and outcomes update live.');
    await page.waitForFunction(() => /\b1[5-9]\d of 300 sent/.test(document.body.innerText), null, { timeout: 60000 });
    await caption(page, 8, 'Every approval is re-read afterwards: approved by you, by someone else, failed, or still pending',
      '“Decided by someone else” is not an error: a colleague in the same approval group got there first.');
    await page.getByRole('heading', { name: /Checking with SailPoint/ }).waitFor({ timeout: 90000 });
    await pause(page, 2500);
    await caption(page, 8, 'Anything still pending is re-checked after 1, 2, 4 and 8 seconds',
      'Only then is it reported as “still pending”, so nothing is silently counted as done.');
    await runDone(page);
    await caption(page, 8, '“Partly done”: the heading says how the run went, the counts and table say why',
      '“Approved” only appears when every approval is confirmed as your decision.');
    await pause(page, 1500);
    await bring(page, page.locator('section.run table'), 'start');
    await pause(page, 5000);
    await scrollTop(page);
    await caption(page, 8, 'Retry sends only the failed and still-pending ones again',
      'Same action and comment. Approvals that keep failing stay listed with SailPoint’s reason.');
    await pause(page, 1500);
    await click(page, page.locator('section.run').getByRole('button', { name: /^Retry \d+/ }), 300);
    await runDone(page, 90000);
    await bring(page, page.locator('section.run'), 'start');
    await pause(page, 4000);
  },

  { card: CLOSING_CARD, id: '09-closing', seconds: 14 },
];

/** Render a card to a PNG and return it as a scene of `seconds`. */
async function cardScene(browser, tmp, { card: html, id, seconds }) {
  const page = await browser.newPage({ viewport: VIEW, deviceScaleFactor: 1 });
  await page.setContent(html);
  await page.evaluate(() => document.fonts.ready);
  const file = path.join(tmp, `${id}.png`);
  await page.screenshot({ path: file });
  await page.close();
  return { id, file, still: true, start: 0, end: seconds };
}

/** Cut, fade and encode one scene; return the clip path. */
function encodeScene(scene, dir) {
  const out = path.join(dir, `${scene.id}.mp4`);
  const dur = Math.max(0.5, scene.end - scene.start);
  const fade = Math.min(0.4, dur / 4);
  const input = scene.still
    ? ['-loop', '1', '-framerate', '30', '-i', scene.file, '-t', dur.toFixed(3)]
    : ['-ss', scene.start.toFixed(3), '-i', scene.file, '-t', dur.toFixed(3)];
  execFileSync(FFMPEG, ['-y', '-loglevel', 'error', ...input,
    '-vf', `fps=30,scale=${VIEW.width}:${VIEW.height}:flags=lanczos,fade=t=in:st=0:d=${fade},fade=t=out:st=${(dur - fade).toFixed(3)}:d=${fade},format=yuv420p`,
    '-c:v', 'libx264', '-preset', 'slow', '-crf', '18', '-tune', 'stillimage', '-r', '30', '-an', out]);
  return out;
}

async function video(browser) {
  const tmp = path.join(OUT, '.raw');
  const scenesDir = path.join(OUT, 'scenes');
  fs.rmSync(tmp, { recursive: true, force: true });
  fs.mkdirSync(tmp, { recursive: true });
  fs.mkdirSync(scenesDir, { recursive: true });
  const scenes = [];
  for (const [i, entry] of SESSIONS.entries()) {
    log(`scene group ${i + 1}/${SESSIONS.length}…`);
    if (entry.card) scenes.push(await cardScene(browser, tmp, entry));
    else scenes.push(...await session(browser, tmp, entry));
  }
  const clips = [];
  let at = 0;
  for (const s of scenes.filter((x) => wanted(x.id))) {
    clips.push(encodeScene(s, scenesDir));
    const dur = s.end - s.start;
    log(`scene ${s.id}  ${fmt(at)}  (${dur.toFixed(1)} s)`);
    at += dur;
  }
  const list = path.join(tmp, 'concat.txt');
  fs.writeFileSync(list, clips.map((c) => `file '${c.replace(/'/g, "'\\''")}'`).join('\n'));
  const final = path.join(OUT, 'bulk-access-demo.mp4');
  execFileSync(FFMPEG, ['-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', list, '-c', 'copy', '-movflags', '+faststart', final]);
  fs.rmSync(tmp, { recursive: true, force: true });
  log(`video ${final} (${fmt(at)})`);
}
const fmt = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;

// ── Main ──────────────────────────────────────────────────────────────────────
try {
  const res = await fetch(BASE, { signal: AbortSignal.timeout(5000) });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
} catch (err) {
  console.error(`The demo server isn't answering at ${BASE} (${err.message}). In plugin/, run: npm run start:demo`);
  process.exit(2);
}
if (doVideo) execFileSync(FFMPEG, ['-version'], { stdio: 'ignore' });

const browser = await chromium.launch();
try {
  if (doShots) await screenshots(browser);
  if (doVideo) await video(browser);
} finally {
  await browser.close();
}
log(`done → ${OUT}`);
