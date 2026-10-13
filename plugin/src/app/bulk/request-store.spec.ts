import { TestBed } from '@angular/core/testing';
import { SailpointPluginService } from '@core';

import { applyScenario, DemoPluginService } from '../demo/demo';
import { crowdPeople, DEMO_CONFIG, DEMO_LAUNCHER_ACCESS, DEMO_ME, demoItem, demoPerson } from '../demo/fixtures';
import type { DemoScenario } from '../demo/scenario';
import { providePluginTesting } from '../testing/plugin.testing';
import { BulkApiService } from './bulk-api.service';
import { BulkConfigService } from './bulk-config.service';
import { describeError, describeSubmitError } from './errors';
import { NavService } from './nav';
import { overallState, partsSummary, RequestStore, type PartState } from './request-store';

describe('RequestStore', () => {
  let store: RequestStore;
  let plugin: DemoPluginService;

  beforeEach(async () => {
    vi.useFakeTimers();
    TestBed.configureTestingModule({ providers: providePluginTesting() });
    await TestBed.inject(BulkConfigService).load();
    store = TestBed.inject(RequestStore);
    plugin = TestBed.inject(SailpointPluginService) as unknown as DemoPluginService;
  });

  afterEach(() => {
    store.reset();
    vi.useRealTimers();
  });

  const config = () => TestBed.inject(BulkConfigService).config;
  const scenario = (name: DemoScenario) =>
    applyScenario(name, store, TestBed.inject(NavService), { plugin, config: TestBed.inject(BulkConfigService) });
  /** Run a submission (or retry) to the end, letting the demo's delays pass. */
  async function run(work: Promise<void>, ms = 3000): Promise<void> {
    await vi.advanceTimersByTimeAsync(ms);
    await work;
  }

  it('blocks submitting until the request is complete, with the same messages as the core', () => {
    expect(store.problems()).toEqual([
      'Choose at least one person.', 'Choose at least one access item.', DEMO_CONFIG.incMessage,
      'Enter a business justification.',
    ]);
    store.people.set([{ id: DEMO_ME.id, name: DEMO_ME.name }]);   // requesting for yourself is fine
    store.items.set([demoItem('VPN - Remote Access')]);
    store.inc.set('INC0012345');
    store.justification.set('x'.repeat(1001));
    expect(store.problems()).toEqual(['Keep the justification to 1000 characters or fewer.']);
  });

  it('takes any number of people unless the config sets a maximum, and skips duplicates', () => {
    const p = demoPerson('Alan Bradley');
    expect(store.addPeople([p, p])).toBe(1);
    expect(store.addPeople([p])).toBe(0);
    expect(store.addPeople(crowdPeople(600))).toBe(600);                 // peopleMax: null
    expect(store.partSize()).toBe(10);                                   // direct: 10 people per access request
    expect(store.parts()).toHaveLength(61);
    config().update((c) => ({ ...c, submit: 'launcher' as const }));
    expect(store.parts().map((x) => x.length)).toEqual([250, 250, 101]);  // launcher: partSize per run
    store.clearPeople();
    expect(store.addPeople([p])).toBe(1);
    config().update((c) => ({ ...c, peopleMax: 2 }));
    expect(store.addPeople([demoPerson('Aisha Bello'), demoPerson('Carmen Ruiz')])).toBe(1);
    expect(store.people()).toHaveLength(2);
  });

  it('knows which chosen items SailPoint grants without approval, and how many requests it files', () => {
    scenario('review');
    expect(store.grantedAtOnce().map((o) => o.label)).toEqual(['ACME Bulk Test Access']);
    expect(store.requestCount()).toBe(10);                                 // 5 people × 2 items
  });

  describe('directly (submit: "direct", the default)', () => {
    it('checks the Launcher Access, then files every item for every person as the user, with the bulk comment', async () => {
      scenario('review');
      const get = vi.spyOn(plugin, 'get');
      const post = vi.spyOn(plugin, 'post');
      await run(store.submit());

      expect(get.mock.calls.map((c) => String(c[0])).filter((p) => p.includes(DEMO_LAUNCHER_ACCESS))).toEqual([
        `/requestable-objects/v1?identity-id=${DEMO_ME.id}&types=ACCESS_PROFILE&limit=1`
          + `&filters=${encodeURIComponent(`id eq "${DEMO_LAUNCHER_ACCESS}"`)}`,
      ]);
      const comment = `INC0048391 | Bulk access request by ${DEMO_ME.name} | Permanent | ${store.justification()}`;
      expect(post.mock.calls).toEqual([['/access-requests/v1', {
        requestedFor: store.people().map((p) => p.id),
        requestType: 'GRANT_ACCESS',
        requestedItems: store.items().map((o) => ({ type: o.value.type, id: o.value.id, comment })),
      }]]);
      // Beatriz Santos already has the PACS request pending: SailPoint skips that one.
      expect(store.submission()).toMatchObject({
        state: 'requested', submit: 'direct', dryRun: false, inc: 'INC0048391', accessLabel: 'Permanent',
        message: 'Filed 9 access requests (1 already pending, so not requested again). Each is approved the way '
          + 'it\'s set up in SailPoint; follow them on My bulk requests.',
      });
      expect(store.submission()?.parts[0]).toMatchObject({ state: 'requested', created: 9, existing: 1, done: true });
    });

    it('sends 10 people per request, a few at a time, with the removal date of temporary access', async () => {
      vi.setSystemTime(new Date(Date.UTC(2026, 9, 8, 10, 0, 0)));
      scenario('temporary');
      store.people.set(crowdPeople(25));
      const post = vi.spyOn(plugin, 'post');
      const done = store.submit();
      await vi.advanceTimersByTimeAsync(300);
      expect(post).toHaveBeenCalledTimes(3);                                // 3 in flight at once
      await run(done);
      const bodies = post.mock.calls.map((c) => c[1] as { requestedFor: string[]; requestedItems: { removeDate?: string; comment: string }[] });
      expect(bodies.map((b) => b.requestedFor.length)).toEqual([10, 10, 5]);
      expect(new Set(bodies.flatMap((b) => b.requestedFor)).size).toBe(25);
      for (const b of bodies) {
        for (const item of b.requestedItems) {
          expect(item.removeDate).toMatch(/^2026-11-07T10:00:00\.\d{3}Z$/);   // 30 days after it was sent
          expect(item.comment).toContain('| Temporary: 30 days |');
        }
      }
      const s = store.submission()!;
      expect(s.state).toBe('requested');
      expect(partsSummary(s.parts)).toBe('3 of 3 sent');
      expect(s.message).toMatch(/^Filed 50 access requests\. /);
    });

    it('says which requests failed, and sends just those again', async () => {
      scenario('review');
      store.people.set(crowdPeople(25));
      const real = plugin.post.bind(plugin);
      let calls = 0;
      const post = vi.spyOn(plugin, 'post').mockImplementation((path: string, body: unknown) =>
        ++calls === 2 ? Promise.reject(Object.assign(new Error('boom'), { status: 500, body: { messages: [{ text: 'Server busy' }] } }))
          : real(path, body));
      await run(store.submit());
      expect(store.submission()?.parts.map((p) => p.state)).toEqual(['requested', 'failed', 'requested']);
      expect(store.sendFailed().map((p) => [p.part, p.message])).toEqual([[2, 'Server busy']]);
      expect(store.submission()?.state).toBe('partial');
      expect(store.submission()?.message).toContain("1 of 3 requests weren't sent; see below.");

      await run(store.retryFailedParts());
      expect(post).toHaveBeenCalledTimes(4);
      expect((post.mock.calls[3][1] as { requestedFor: string[] }).requestedFor).toEqual(crowdPeople(20).slice(10).map((p) => p.id));
      expect(store.sendFailed()).toEqual([]);
      expect(store.submission()?.state).toBe('requested');
    });

    it('stops after a permission error instead of sending every request', async () => {
      scenario('review');
      store.people.set(crowdPeople(50));
      const post = vi.spyOn(plugin, 'post').mockImplementation(() => Promise.reject(Object.assign(new Error('no'), { status: 403 })));
      await run(store.submit());
      expect(post).toHaveBeenCalledTimes(3);                                // only the ones already in flight
      expect(store.submission()?.state).toBe('error');
      expect(store.sendFailed()).toHaveLength(5);
      expect(store.submission()?.message).toContain('"request for others" settings');
    });

    it('requests nothing in dry-run mode', async () => {
      scenario('review');
      config().update((c) => ({ ...c, mode: 'dry-run' as const }));
      const post = vi.spyOn(plugin, 'post');
      await run(store.submit());
      expect(post).not.toHaveBeenCalled();
      expect(store.submission()).toMatchObject({ state: 'requested', dryRun: true,
        message: 'Dry-run mode: everything checks out, but nothing was requested.' });
    });

    it('turns away a user without the Launcher Access profile before sending anything', async () => {
      scenario('launcher-denied');
      const post = vi.spyOn(plugin, 'post');
      await run(store.submit());
      expect(post).not.toHaveBeenCalled();
      expect(store.submission()?.state).toBe('error');
      expect(store.sendFailed()[0].message)
        .toBe("To submit, you need 'ACME Bulk Access Request - Launcher Access'. Request it in the Request Center.");
    });

    it('says when it could not check the access, and skips the check when no profile is installed', async () => {
      scenario('review');
      const get = vi.spyOn(plugin, 'get').mockImplementationOnce(() => Promise.reject(Object.assign(new Error('x'), { status: 403 })));
      await run(store.submit());
      expect(store.sendFailed()[0].message).toMatch(/^Couldn't check that you may submit: SailPoint refused the call \(HTTP 403\)/);

      store.reset();
      scenario('review');
      config().update((c) => ({ ...c, launcherAccessId: null }));
      get.mockClear();
      await run(store.submit());
      expect(get.mock.calls.some((c) => String(c[0]).startsWith('/requestable-objects/v1'))).toBe(false);
      expect(store.submission()?.state).toBe('requested');
    });
  });

  describe('through the Launcher (submit: "launcher", CONTRACTS §9)', () => {
    type Ops = { op: string; path: string; value: unknown }[];
    const formData = (patch: ReturnType<typeof vi.spyOn>, call = 0) =>
      ((patch.mock.calls[call][1] as Ops).find((o) => o.path === '/formData')!.value) as Record<string, unknown>;
    const viaLauncher = () => config().update((c) => ({ ...c, submit: 'launcher' as const }));

    it('launches the Launcher, waits for its form, submits it as the user, then reads the workflow\'s answer', async () => {
      scenario('review');
      viaLauncher();
      const post = vi.spyOn(plugin, 'post');
      const get = vi.spyOn(plugin, 'get');
      const patch = vi.spyOn(plugin, 'patch');

      await run(store.submit());
      expect(post.mock.calls).toEqual([['/launchers/v1/demo-launcher/launch', {}]]);
      expect(patch).toHaveBeenCalledTimes(1);                                                  // ASSIGNED -> COMPLETED at once
      expect(patch.mock.calls[0][0]).toBe('/form-instances/v1/f-0');
      expect((patch.mock.calls[0][1] as Ops).map((o) => [o.op, o.path])).toEqual([['replace', '/formData'], ['replace', '/state']]);
      expect((patch.mock.calls[0][1] as Ops)[1].value).toBe('SUBMITTED');
      expect(formData(patch)).toEqual({
        people: store.people().map((x) => x.id),
        items: store.items().map((o) => o.value),
        inc: 'INC0048391',
        justification: store.justification(),
        accessType: false, duration: '', durationUnit: [],
        partLabel: '',
      });
      expect(store.submission()?.parts[0].processId).toBeTruthy();

      await vi.advanceTimersByTimeAsync(5000);
      expect(store.submission()).toMatchObject({ state: 'requested', submit: 'launcher',
        message: '10 access requests filed. Each is approved the way it\'s set up in SailPoint.' });
      // Nothing a non-admin can't read, and no workflow calls.
      expect(get.mock.calls.some((c) => /workflow|identities\/v1/.test(String(c[0])))).toBe(false);
    });

    it('sends 600 people as 3 Launcher runs with the part label and the duration in the form fields', async () => {
      scenario('parts-review');
      expect(config()().submit).toBe('launcher');
      const patch = vi.spyOn(plugin, 'patch');
      await run(store.submit(), 10000);
      const sent = [0, 1, 2].map((i) => formData(patch, i));
      expect(sent.map((d) => [d['partLabel'], (d['people'] as string[]).length])).toEqual([
        [' (1/3)', 250], [' (2/3)', 250], [' (3/3)', 100],
      ]);
      expect(new Set(sent.flatMap((d) => d['people'] as string[])).size).toBe(600);
      for (const d of sent) {
        expect(d).toMatchObject({ inc: 'INC0048391', accessType: true, duration: '30', durationUnit: ['d'] });
      }
      await vi.advanceTimersByTimeAsync(5000);
      const s = store.submission()!;
      expect(s.parts.every((p) => p.state === 'requested')).toBe(true);
      expect(s.message).toBe('Submitted as 3 Launcher runs with the same INC. The Launcher files the requests, and each '
        + 'item is approved the way it\'s set up in SailPoint; you\'ll get an email for each run.');
    });

    it('sends an end date as hours', async () => {
      vi.setSystemTime(new Date(2026, 9, 8, 10, 0, 0));
      scenario('review');
      viaLauncher();
      store.accessMode.set('endDate');
      store.endDate.set('2026-10-09');
      const patch = vi.spyOn(plugin, 'patch');
      await run(store.submit());
      expect(formData(patch)).toMatchObject({ accessType: true, duration: '38', durationUnit: ['h'], partLabel: '' });
    });

    it.each([
      ['403', Object.assign(new Error('Forbidden'), { status: 403 })],
      ['500 insufficient authorization', Object.assign(new Error('API request failed with status 500.'),
        { status: 500, body: { messages: [{ text: 'insufficient authorization' }] } })],
    ])('tells a user the Launcher refuses what to request (%s), and stops', async (_name, error) => {
      scenario('parts-review');
      const post = vi.spyOn(plugin, 'post').mockImplementation(() => Promise.reject(error));
      await run(store.submit());
      expect(post).toHaveBeenCalledTimes(1);
      expect(store.submission()?.state).toBe('error');
      const message = "To submit, you need 'ACME Bulk Access Request - Launcher Access'. Request it in the Request Center.";
      expect(store.sendFailed().map((p) => p.message)).toEqual([message, message, message]);
      expect(store.submission()?.message).toContain(message);
    });

    it('refuses to start without a Launcher ID', async () => {
      scenario('review');
      config().update((c) => ({ ...c, submit: 'launcher' as const, launcherId: null }));
      const post = vi.spyOn(plugin, 'post');
      await run(store.submit());
      expect(post).not.toHaveBeenCalled();
      expect(store.sendFailed()[0].message).toContain('plugin/install.py');
    });

    it('shows the form\'s own errors when it refuses the request', async () => {
      scenario('review');
      viaLauncher();
      vi.spyOn(TestBed.inject(BulkApiService), 'submitLauncherForm').mockResolvedValue({
        state: 'IN_PROGRESS', errors: [{ key: 'inc', messages: ['Enter a ServiceNow incident number.'] }],
      });
      await run(store.submit());
      expect(store.submission()?.state).toBe('error');
      expect(store.sendFailed()[0].message).toBe(
        'The Launcher form refused the request (inc: Enter a ServiceNow incident number.). Nothing was requested.');
    });

    it('says when the Launcher\'s form never appears', async () => {
      scenario('review');
      viaLauncher();
      vi.spyOn(TestBed.inject(BulkApiService), 'launcherBlocks').mockResolvedValue([]);
      await run(store.submit(), 40000);
      expect(store.sendFailed()[0].message).toContain("its form didn't appear within 30 seconds");
    });

    it('reports a run the workflow stopped (an ERROR message block)', async () => {
      scenario('review');
      viaLauncher();
      const api = TestBed.inject(BulkApiService);
      vi.spyOn(api, 'launcherBlocks').mockResolvedValue([
        { type: 'FORM', config: { formInstanceId: 'f-0' } },
        { type: 'MESSAGE', config: { category: 'ERROR' }, data: { title: 'Invalid INC number', message: '<p>Use INC + 7 digits.</p>' } },
      ]);
      await run(store.submit(), 6000);
      expect(store.submission()?.parts[0]).toMatchObject({ state: 'stopped', done: true,
        message: 'The Launcher stopped it: Invalid INC number: Use INC + 7 digits.' });
      expect(store.submission()?.state).toBe('error');
    });

    it('stops waiting for an answer after two minutes, and says an email follows', async () => {
      scenario('review');
      viaLauncher();
      vi.spyOn(TestBed.inject(BulkApiService), 'launcherBlocks').mockResolvedValue([{ type: 'FORM', config: { formInstanceId: 'f-0' } }]);
      await run(store.submit());
      expect(store.submission()?.state).toBe('sending');
      await vi.advanceTimersByTimeAsync(125_000);
      expect(store.submission()).toMatchObject({ state: 'requested',
        message: 'Submitted to the Launcher, which hasn\'t answered yet. You\'ll get an email with the result.' });
    });
  });

  it('only offers temporary access the config allows', () => {
    const cfg = config();
    expect(store.temporaryModes()).toEqual(['duration', 'endDate']);
    store.accessMode.set('duration');
    store.durationN.set(2);
    expect(store.unit()).toBe('DAYS');
    expect(store.accessProblems()).toEqual([]);
    cfg.update((c) => ({ ...c, temporary: { ...c.temporary, units: ['HOURS', 'WEEKS'] } }));
    expect(store.unit()).toBe('HOURS');                     // DAYS isn't offered any more
    cfg.update((c) => ({ ...c, temporary: { ...c.temporary, enabled: false } }));
    expect(store.temporaryModes()).toEqual([]);
    expect(store.problems()).toContain("Temporary access isn't available.");
  });

  it('sums up the parts: overall state and "k of n" text', () => {
    const of = (...states: PartState[]) => states.map((state) => ({ state }));
    expect(overallState(of('requested'))).toBe('requested');
    expect(overallState(of('requested', 'sending'))).toBe('sending');
    expect(overallState(of('requested', 'queued', 'failed'))).toBe('sending');
    expect(overallState(of('requested', 'failed'))).toBe('partial');
    expect(overallState(of('requested', 'stopped'))).toBe('partial');
    expect(overallState(of('failed', 'stopped'))).toBe('error');
    expect(partsSummary(of('requested', 'requested', 'sending'))).toBe('2 of 3 sent · 1 sending');
    expect(partsSummary(of('requested', 'failed', 'stopped'))).toBe('1 of 3 sent · 1 not sent · 1 stopped');
    expect(partsSummary([])).toBe('');
  });

  it('words a refused call for what was being done and for the submit mode', () => {
    const cfg = { submit: 'launcher' as const, launcherAccessName: 'ACME Bulk Access Request - Launcher Access' };
    // Reads (people, catalog, history) say what couldn't be loaded, never blame ORG_ADMIN.
    const read = describeError({ status: 403 }, 'read', 'the catalog');
    expect(read).toContain("so the catalog couldn't be loaded");
    expect(read).not.toContain('ORG_ADMIN');
    expect(describeError({ status: 400, body: { messages: [{ text: 'bad input' }] } })).toBe('bad input');
    // Launcher mode: the missing access, by its configured name.
    expect(describeSubmitError({ status: 403 }, cfg))
      .toBe("To submit, you need 'ACME Bulk Access Request - Launcher Access'. Request it in the Request Center.");
    expect(describeSubmitError({ status: 500, body: { messages: [{ text: 'insufficient authorization' }] } }, cfg))
      .toContain("you need 'ACME Bulk Access Request - Launcher Access'");
    expect(describeSubmitError({ status: 500, body: { messages: [{ text: 'boom' }] } }, cfg)).toBe('boom');
    // Direct mode: SailPoint may not let this user request for others.
    const direct = describeSubmitError({ status: 403 }, { ...cfg, submit: 'direct' });
    expect(direct).toContain('"request for others" settings');
    expect(direct).not.toContain('ORG_ADMIN');
    expect(describeSubmitError({ status: 400, body: { messages: [{ text: 'bad item' }] } }, { ...cfg, submit: 'direct' })).toBe('bad item');
  });
});
