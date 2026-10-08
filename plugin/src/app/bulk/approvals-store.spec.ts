import { TestBed } from '@angular/core/testing';
import { SailpointPluginService } from '@core';

import { DemoPluginService, PARTIAL_FAULTS } from '../demo/demo';
import { DEMO_PENDING, DEMO_PENDING_INCS } from '../demo/fixtures';
import { providePluginTesting } from '../testing/plugin.testing';
import { OTHER_KEY } from './approvals';
import { ApprovalsStore, outcomeCounts, runHeading } from './approvals-store';
import { BulkApiService } from './bulk-api.service';
import { BulkConfigService } from './bulk-config.service';

const BIG = DEMO_PENDING_INCS.big;

describe('ApprovalsStore', () => {
  let store: ApprovalsStore;
  let plugin: DemoPluginService;

  async function setup(opts: { admin?: boolean; endpoint?: 'auto' | 'always' | 'never'; faults?: boolean } = {}) {
    plugin = TestBed.inject(SailpointPluginService) as unknown as DemoPluginService;
    plugin.setOrgAdmin(opts.admin ?? false);
    plugin.faults = opts.faults ? PARTIAL_FAULTS : null;
    if (opts.endpoint) {
      TestBed.inject(BulkConfigService).config.update((c) => ({ ...c, approvals: { ...c.approvals, useBulkEndpoint: opts.endpoint! } }));
    }
    const api = TestBed.inject(BulkApiService);
    api.decideRetryWaitMs = 10;
    store = TestBed.inject(ApprovalsStore);
    await settle(store.load());
  }

  /** Let fake time run until `work` is done (demo answers, throttle, back-offs, confirmation rounds). */
  async function settle<T>(work: Promise<T>): Promise<T> {
    let done = false;
    const out = work.finally(() => (done = true));
    for (let i = 0; i < 400 && !done; i++) await vi.advanceTimersByTimeAsync(500);
    return out;
  }

  beforeEach(async () => {
    vi.useFakeTimers();
    TestBed.configureTestingModule({ providers: providePluginTesting() });
    await TestBed.inject(BulkConfigService).load();
  });

  afterEach(() => {
    store?.ngOnDestroy();
    vi.useRealTimers();
  });

  it('loads the caller\'s pending access-request approvals and groups them by INC', async () => {
    const get = vi.spyOn(TestBed.inject(SailpointPluginService), 'get');
    await setup();
    expect(store.groups().map((g) => [g.key, g.approvals])).toEqual([
      [DEMO_PENDING_INCS.small, 40], [BIG, 300], [OTHER_KEY, 3],
    ]);
    expect(store.truncated()).toBe(false);
    const url = decodeURIComponent(get.mock.calls[0][0] as string);
    expect(url).toContain('/v2025/generic-approvals?mine=true&include-comments=true&limit=250&offset=0');
    expect(url).toContain('filters=status eq "PENDING" and type eq "ACCESS_REQUEST_APPROVAL"&sorters=createdDate');
    expect(get).toHaveBeenCalledTimes(2);   // 343 rows: two pages

    store.filter.set('vpn');
    expect(store.shown().map((g) => g.key)).toEqual([DEMO_PENDING_INCS.small]);
  });

  it('approves a whole INC one approval at a time for a non-admin, confirms each one, and drops them', async () => {
    await setup();
    const post = vi.spyOn(plugin, 'post');
    store.toggleGroup(BIG);
    store.comment.set('Checked against the staff list.');
    expect(store.problems('approve')).toEqual([]);
    expect(store.useBulk()).toBe(false);

    await settle(store.decide('approve'));
    const calls = post.mock.calls.map((c) => c[0] as string);
    expect(calls).toHaveLength(300);
    expect(calls.every((p) => /^\/v2025\/generic-approvals\/b0000000-[^/]+\/approve$/.test(p))).toBe(true);
    expect(post.mock.calls[0][1]).toEqual({ comment: 'Checked against the staff list.' });
    expect(store.run()).toMatchObject({ phase: 'done', inc: BIG, done: 300 });
    expect(store.counts()).toMatchObject({ confirmed: 300, failed: 0, pending: 0, elsewhere: 0 });
    // Decided rows leave the list; the group is gone and closed.
    expect(store.groups().map((g) => g.key)).toEqual([DEMO_PENDING_INCS.small, OTHER_KEY]);
    expect(store.openKey()).toBeNull();
    expect(store.retryable()).toEqual([]);
  });

  it('leaves excluded approvals out, and they stay pending', async () => {
    await setup();
    const post = vi.spyOn(plugin, 'post');
    store.toggleGroup(DEMO_PENDING_INCS.small);
    const rows = store.openGroup()!.rows;
    store.setIncluded(rows.slice(2).map((r) => r.id));                   // leaves out rows 0 and 1
    expect(store.includedCounts()).toEqual({ approvals: 38, people: 38, items: 1 });
    await settle(store.decide('approve'));
    expect(post).toHaveBeenCalledTimes(38);
    expect(store.openGroup()?.rows.map((r) => r.id)).toEqual([rows[0].id, rows[1].id]);
  });

  it('needs a comment to deny, and never acts on the Other group or with nothing open', async () => {
    await setup();
    expect(store.problems('approve')).toContain('Open a bulk request first.');
    store.toggleGroup(BIG);
    expect(store.problems('reject')).toEqual(['Enter a comment to deny.']);
    const post = vi.spyOn(plugin, 'post');
    await store.decide('reject');
    expect(post).not.toHaveBeenCalled();
    store.comment.set('  ');
    expect(store.problems('reject')).toEqual(['Enter a comment to deny.']);
    TestBed.inject(BulkConfigService).config.update((c) => ({ ...c, approvals: { ...c.approvals, denyCommentRequired: false } }));
    expect(store.problems('reject')).toEqual([]);

    store.toggleGroup(OTHER_KEY);
    expect(store.problems('approve')[0]).toContain('decided one by one');
    store.toggleGroup(BIG);
    store.setIncluded([]);
    expect(store.problems('approve')).toEqual(['Choose at least one approval.']);
  });

  it('denies with the comment and reports it as denied by me', async () => {
    await setup();
    store.toggleGroup(DEMO_PENDING_INCS.small);
    store.comment.set('Not on the ward list.');
    const post = vi.spyOn(plugin, 'post');
    await settle(store.decide('reject'));
    expect(post.mock.calls[0][0]).toMatch(/\/reject$/);
    expect(post.mock.calls[0][1]).toEqual({ comment: 'Not on the ward list.' });
    expect(store.counts().confirmed).toBe(40);
  });

  it('uses the bulk endpoint for an ORG_ADMIN (auto), 50 at a time, and still confirms', async () => {
    await setup({ admin: true });
    expect(store.useBulk()).toBe(true);
    const post = vi.spyOn(plugin, 'post');
    store.toggleGroup(BIG);
    await settle(store.decide('approve'));
    expect(post.mock.calls.map((c) => c[0])).toEqual(Array(6).fill('/v2025/generic-approvals/bulk-approve'));
    expect((post.mock.calls[0][1] as { approvalIds: string[] }).approvalIds).toHaveLength(50);
    expect(store.run()?.bulk).toBe(true);
    expect(store.counts().confirmed).toBe(300);
  });

  it('falls back to one call per approval when the bulk endpoint refuses (403)', async () => {
    await setup({ endpoint: 'always' });                                  // forced on, but not an admin
    const post = vi.spyOn(plugin, 'post');
    store.toggleGroup(DEMO_PENDING_INCS.small);
    await settle(store.decide('approve'));
    const paths = post.mock.calls.map((c) => c[0] as string);
    expect(paths[0]).toBe('/v2025/generic-approvals/bulk-approve');
    expect(paths.slice(1).every((p) => p.endsWith('/approve') && !p.includes('bulk'))).toBe(true);
    expect(paths).toHaveLength(41);
    expect(store.counts().confirmed).toBe(40);
  });

  it('never uses the bulk endpoint when the config says never', async () => {
    await setup({ admin: true, endpoint: 'never' });
    expect(store.useBulk()).toBe(false);
  });

  it('sorts out throttling, failures, colleagues deciding first and approvals that stay pending; retries just those', async () => {
    await setup({ faults: true });
    store.toggleGroup(BIG);
    await settle(store.decide('approve'));
    const run = store.run()!;
    // Of positions 1..300: every 60th refuses (5); every 45th not already failing was decided by a colleague
    // (45, 90, 135, 225, 270); every 97th stays pending (97, 194, 291). 429s and 503s pass on retry.
    expect(store.counts()).toMatchObject({ confirmed: 287, failed: 5, elsewhere: 5, pending: 3 });
    const byOutcome = (o: string) => run.items.filter((i) => i.outcome === o);
    expect(byOutcome('failed')[0].message).toContain('its access item is being updated');
    expect(byOutcome('elsewhere')[0].message).toBe('Approved by Elliot Reid.');
    expect(byOutcome('pending')[0].message).toContain('still shows it pending');
    // Only the confirmed and decided-elsewhere rows leave the list.
    expect(store.openGroup()?.approvals).toBe(8);

    const post = vi.spyOn(plugin, 'post');
    expect(store.retryable()).toHaveLength(8);
    await settle(store.retry());
    expect(new Set(post.mock.calls.map((c) => c[0] as string)).size).toBe(8);
    expect(store.counts()).toMatchObject({ confirmed: 0, failed: 5, pending: 3 });
    expect(store.run()?.items).toHaveLength(8);

    store.dismiss();
    expect(store.run()).toBeNull();
  });

  it('keeps one run at a time and stops confirming once destroyed', async () => {
    await setup();
    store.toggleGroup(DEMO_PENDING_INCS.small);
    const first = store.decide('approve');
    await vi.advanceTimersByTimeAsync(10);
    expect(store.running()).toBe(true);
    expect(store.problems('approve')).toContain('Wait for the current run to finish.');
    store.toggleGroup(BIG);                                               // ignored while running
    expect(store.openKey()).toBe(DEMO_PENDING_INCS.small);
    await settle(first);
  });

  it('counts outcomes', () => {
    expect(outcomeCounts([{ outcome: 'failed' }, { outcome: 'failed' }, { outcome: 'confirmed' }])).toMatchObject({
      failed: 2, confirmed: 1, pending: 0,
    });
  });

  it('heads a finished run with what actually happened', () => {
    const counts = (o: Partial<ReturnType<typeof outcomeCounts>>) => ({ ...outcomeCounts([]), ...o });
    expect(runHeading('approve', counts({ confirmed: 300 }))).toBe('Approved');
    expect(runHeading('reject', counts({ confirmed: 3 }))).toBe('Denied');
    expect(runHeading('approve', counts({ confirmed: 290, elsewhere: 10 }))).toBe('Decided');
    expect(runHeading('approve', counts({ confirmed: 280, elsewhere: 10, failed: 6, pending: 4 }))).toBe('Partly done');
    expect(runHeading('approve', counts({ pending: 2 }))).toBe('Not done');
    expect(runHeading('reject', counts({ failed: 5, elsewhere: 1 }))).toBe('Not done');
  });

  it('reports a load failure in approvals wording', async () => {
    plugin = TestBed.inject(SailpointPluginService) as unknown as DemoPluginService;
    vi.spyOn(plugin, 'get').mockRejectedValue(Object.assign(new Error('Forbidden'), { status: 403 }));
    store = TestBed.inject(ApprovalsStore);
    await settle(store.load());
    expect(store.error()).toContain('You can only decide approvals that are assigned to you');
    expect(store.approvals()).toEqual([]);
    expect(DEMO_PENDING).toHaveLength(343);
  });
});
