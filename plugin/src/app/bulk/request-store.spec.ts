import { TestBed } from '@angular/core/testing';
import { SailpointPluginService } from '@core';

import { applyScenario } from '../demo/demo';
import { crowdPeople, DEMO_ME, DEMO_NEW_EXECUTION, demoExecutionId, demoPerson } from '../demo/fixtures';
import { providePluginTesting } from '../testing/plugin.testing';
import { BulkApiService } from './bulk-api.service';
import { BulkConfigService } from './bulk-config.service';
import { describeError } from './errors';
import { NavService } from './nav';
import { overallState, partsSummary, RequestStore, type PartState } from './request-store';

describe('RequestStore', () => {
  let store: RequestStore;

  beforeEach(async () => {
    vi.useFakeTimers();
    TestBed.configureTestingModule({ providers: providePluginTesting() });
    await TestBed.inject(BulkConfigService).load();
    store = TestBed.inject(RequestStore);
  });

  afterEach(() => {
    store.reset();
    vi.useRealTimers();
  });

  it('blocks submitting until the request is complete, with the same messages as the core', () => {
    expect(store.problems()).toEqual(expect.arrayContaining([
      'Choose at least one person.', 'Choose at least one access item.', 'Choose an approver.',
      'Enter a business justification.',
    ]));
    store.approver.set(demoPerson('Aisha Bello'));
    store.approver.set({ id: DEMO_ME.id, name: DEMO_ME.name });
    expect(store.problems()).toContain('The approver must be someone other than you.');
  });

  it('takes any number of people unless the config sets a maximum, and skips duplicates', () => {
    const p = demoPerson('Alan Bradley');
    expect(store.addPeople([p, p])).toBe(1);
    expect(store.addPeople([p])).toBe(0);
    expect(store.addPeople(crowdPeople(600))).toBe(600);                 // peopleMax: null
    expect(store.parts().map((x) => x.length)).toEqual([250, 250, 101]);
    store.clearPeople();
    expect(store.addPeople([p])).toBe(1);
    TestBed.inject(BulkConfigService).config.update((c) => ({ ...c, peopleMax: 2 }));
    expect(store.addPeople([demoPerson('Aisha Bello'), demoPerson('Carmen Ruiz')])).toBe(1);
    expect(store.people()).toHaveLength(2);
  });

  /** The workflow test endpoint (ORG_ADMIN) instead of the Launcher (the demo config's default). */
  const testEndpoint = () => TestBed.inject(BulkConfigService).config.update((c) => ({ ...c, submit: 'test-endpoint' as const }));

  it('test-endpoint mode: submits, then reports "Waiting for <approver>" once the approval exists', async () => {
    testEndpoint();
    applyScenario('review', store, TestBed.inject(NavService));
    const plugin = TestBed.inject(SailpointPluginService);
    const post = vi.spyOn(plugin, 'post');
    expect(store.problems()).toEqual([]);

    const done = store.submit();
    await vi.advanceTimersByTimeAsync(1000);
    await done;
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith('/v2025/workflows/demo-workflow/test', {
      input: {
        people: store.people().map((x) => x.id),
        items: store.items().map((o) => o.value),
        approverId: demoPerson('Aisha Bello').id,
        requesterId: DEMO_ME.id,
        inc: 'INC0048391',
        justification: store.justification(),
        part: 1, parts: 1, partLabel: '',
        removeDuration: '', accessLabel: 'Permanent',
      },
    });
    expect(store.submission()).toMatchObject({ state: 'waiting', accessLabel: 'Permanent' });
    expect(store.submission()?.parts.map((p) => p.executionId)).toEqual([DEMO_NEW_EXECUTION]);

    await vi.advanceTimersByTimeAsync(5000);
    expect(store.submission()).toMatchObject({ state: 'waiting', message: 'Waiting for Aisha Bello to approve or deny.' });
    expect(store.submission()?.parts[0].approvalId).toBeTruthy();
  });

  it('test-endpoint mode: sends 600 people as 3 workflow runs, one after another, with the same INC and access choice', async () => {
    testEndpoint();
    applyScenario('parts-review', store, TestBed.inject(NavService));
    const post = vi.spyOn(TestBed.inject(SailpointPluginService), 'post');
    expect(store.problems()).toEqual([]);
    expect(store.accessLabel()).toBe('Temporary: 30 days');

    const done = store.submit();
    await vi.advanceTimersByTimeAsync(700);
    expect(post).toHaveBeenCalledTimes(2);                  // part 1 answered, part 2 started: sequential
    expect(store.submission()?.state).toBe('starting');
    await vi.advanceTimersByTimeAsync(2000);
    await done;

    const inputs = post.mock.calls.map((c) => (c[1] as { input: Record<string, unknown> }).input);
    expect(inputs.map((i) => [i['part'], i['parts'], i['partLabel'], (i['people'] as string[]).length])).toEqual([
      [1, 3, ' (1/3)', 250], [2, 3, ' (2/3)', 250], [3, 3, ' (3/3)', 100],
    ]);
    expect(new Set(inputs.flatMap((i) => i['people'] as string[])).size).toBe(600);
    for (const i of inputs) {
      expect(i).toMatchObject({ inc: 'INC0048391', approverId: demoPerson('Aisha Bello').id, removeDuration: '30d',
        accessLabel: 'Temporary: 30 days' });
    }
    expect(store.submission()?.parts.map((p) => p.executionId)).toEqual([0, 1, 2].map(demoExecutionId));

    await vi.advanceTimersByTimeAsync(5000);
    const s = store.submission()!;
    expect(s.state).toBe('waiting');
    expect(s.parts.every((p) => p.state === 'waiting' && p.approvalId)).toBe(true);
    expect(partsSummary(s.parts)).toBe('3 of 3 waiting');
    expect(s.message).toBe('Sent as 3 approvals with the same INC. Waiting for Aisha Bello to decide each one.');
  });

  it('test-endpoint mode: says which parts failed to start, and retries just those', async () => {
    testEndpoint();
    applyScenario('parts-review', store, TestBed.inject(NavService));
    const plugin = TestBed.inject(SailpointPluginService);
    const real = plugin.post.bind(plugin);
    let calls = 0;
    const post = vi.spyOn(plugin, 'post').mockImplementation((path: string, body: unknown) =>
      ++calls === 2 ? Promise.reject(Object.assign(new Error('boom'), { status: 500, body: { messages: [{ text: 'Server busy' }] } }))
        : real(path, body));

    const done = store.submit();
    await vi.advanceTimersByTimeAsync(3000);
    await done;
    expect(store.submission()?.parts.map((p) => p.state)).toEqual(['waiting', 'start-failed', 'waiting']);
    expect(store.startFailed().map((p) => [p.part, p.message])).toEqual([[2, 'Server busy']]);
    expect(store.submission()?.message).toContain("1 of 3 didn't start.");

    const retried = store.retryFailedParts();
    await vi.advanceTimersByTimeAsync(1000);
    await retried;
    expect(post).toHaveBeenCalledTimes(4);
    expect((post.mock.calls[3][1] as { input: { part: number; partLabel: string } }).input).toMatchObject({ part: 2, partLabel: ' (2/3)' });
    expect(store.startFailed()).toEqual([]);
    expect(store.submission()?.parts.map((p) => p.state)).toEqual(['waiting', 'waiting', 'waiting']);
  });

  it('test-endpoint mode: stops after a permission error instead of trying every part', async () => {
    testEndpoint();
    applyScenario('parts-review', store, TestBed.inject(NavService));
    const post = vi.spyOn(TestBed.inject(SailpointPluginService), 'post')
      .mockImplementation(() => Promise.reject(Object.assign(new Error('no'), { status: 403 })));
    await store.submit();
    expect(post).toHaveBeenCalledTimes(1);
    expect(store.submission()?.state).toBe('error');
    expect(store.startFailed()).toHaveLength(3);
    expect(store.submission()?.message).toContain('ORG_ADMIN');
  });

  it('test-endpoint mode: converts an end date to hours when it submits', async () => {
    vi.setSystemTime(new Date(2026, 9, 8, 10, 0, 0));
    testEndpoint();
    applyScenario('review', store, TestBed.inject(NavService));
    store.accessMode.set('endDate');
    store.endDate.set('2026-10-08');
    expect(store.problems()).toContain('Choose an end date after today.');
    store.endDate.set('2026-10-09');
    expect(store.problems()).toEqual([]);
    const post = vi.spyOn(TestBed.inject(SailpointPluginService), 'post');
    const done = store.submit();
    await vi.advanceTimersByTimeAsync(1000);
    await done;
    expect((post.mock.calls[0][1] as { input: unknown }).input).toMatchObject({
      removeDuration: '38h', accessLabel: 'Temporary: until 2026-10-09', part: 1, parts: 1, partLabel: '',
    });
  });

  describe('through the Launcher (submit: "launcher", CONTRACTS §9)', () => {
    type Ops = { op: string; path: string; value: unknown }[];
    const formData = (patch: ReturnType<typeof vi.spyOn>, call = 0) =>
      ((patch.mock.calls[call][1] as Ops).find((o) => o.path === '/formData')!.value) as Record<string, unknown>;

    it('launches the Launcher, waits for its form, submits it as the user, then waits for the approval', async () => {
      applyScenario('review', store, TestBed.inject(NavService));
      const plugin = TestBed.inject(SailpointPluginService);
      const post = vi.spyOn(plugin, 'post');
      const get = vi.spyOn(plugin, 'get');
      const patch = vi.spyOn(plugin as unknown as { patch: (p: string, d: unknown) => Promise<unknown> }, 'patch');

      const done = store.submit();
      await vi.advanceTimersByTimeAsync(3000);
      await done;
      expect(post.mock.calls.map((c) => c[0])).toEqual(['/v2025/launchers/demo-launcher/launch']);
      expect(post).toHaveBeenCalledWith('/v2025/launchers/demo-launcher/launch', {});
      expect(get.mock.calls.filter((c) => String(c[0]).includes('/blocks')).length).toBe(2);   // polled until the FORM block
      expect(patch).toHaveBeenCalledTimes(1);                                                  // ASSIGNED -> COMPLETED at once
      expect(patch.mock.calls[0][0]).toBe('/v2025/form-instances/f-0');
      expect((patch.mock.calls[0][1] as Ops).map((o) => [o.op, o.path])).toEqual([['replace', '/formData'], ['replace', '/state']]);
      expect((patch.mock.calls[0][1] as Ops)[1].value).toBe('SUBMITTED');
      expect(formData(patch)).toEqual({
        people: store.people().map((x) => x.id),
        items: store.items().map((o) => o.value),
        approver: [demoPerson('Aisha Bello').id],
        inc: 'INC0048391',
        justification: store.justification(),
        accessType: false, duration: '', durationUnit: [],
        partLabel: '',
      });
      expect(store.submission()?.parts[0]).toMatchObject({ state: 'waiting', executionId: DEMO_NEW_EXECUTION });
      expect(store.submission()?.parts[0].processId).toBeTruthy();

      await vi.advanceTimersByTimeAsync(5000);
      expect(store.submission()).toMatchObject({ state: 'waiting', message: 'Waiting for Aisha Bello to approve or deny.' });
      expect(store.submission()?.parts[0].approvalId).toBeTruthy();
      // Nothing a non-admin can't read: no workflow lookups, executions or test runs.
      expect(get.mock.calls.some((c) => /workflow-executions|\/v2025\/workflows/.test(String(c[0])))).toBe(false);
    });

    it('sends 600 people as 3 Launcher runs with the part label and the duration in the form fields', async () => {
      applyScenario('parts-review', store, TestBed.inject(NavService));
      const plugin = TestBed.inject(SailpointPluginService);
      const patch = vi.spyOn(plugin as unknown as { patch: (p: string, d: unknown) => Promise<unknown> }, 'patch');
      const done = store.submit();
      await vi.advanceTimersByTimeAsync(10000);
      await done;
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
      expect(s.parts.map((p) => p.executionId)).toEqual([0, 1, 2].map(demoExecutionId));
      expect(s.parts.every((p) => p.state === 'waiting' && p.approvalId)).toBe(true);
      expect(s.message).toBe('Sent as 3 approvals with the same INC. Waiting for Aisha Bello to decide each one.');
    });

    it('sends an end date as hours', async () => {
      vi.setSystemTime(new Date(2026, 9, 8, 10, 0, 0));
      applyScenario('review', store, TestBed.inject(NavService));
      store.accessMode.set('endDate');
      store.endDate.set('2026-10-09');
      const patch = vi.spyOn(TestBed.inject(SailpointPluginService) as unknown as { patch: (p: string, d: unknown) => Promise<unknown> }, 'patch');
      const done = store.submit();
      await vi.advanceTimersByTimeAsync(3000);
      await done;
      expect(formData(patch)).toMatchObject({ accessType: true, duration: '38', durationUnit: ['h'], partLabel: '' });
    });

    it.each([
      ['403', Object.assign(new Error('Forbidden'), { status: 403 })],
      ['500 insufficient authorization', Object.assign(new Error('API request failed with status 500.'),
        { status: 500, body: { messages: [{ text: 'insufficient authorization' }] } })],
    ])('tells a user without the Launcher access what to request (%s), and stops', async (_name, error) => {
      applyScenario('parts-review', store, TestBed.inject(NavService));
      const post = vi.spyOn(TestBed.inject(SailpointPluginService), 'post').mockImplementation(() => Promise.reject(error));
      await store.submit();
      expect(post).toHaveBeenCalledTimes(1);
      expect(store.submission()?.state).toBe('error');
      expect(store.startFailed()).toHaveLength(3);
      const message = 'You need the ACME Bulk Access Request - Launcher Access access to submit; request it in the Request Center.';
      expect(store.startFailed().map((p) => p.message)).toEqual([message, message, message]);
      expect(store.submission()?.message).toContain(message);
    });

    it('shows the form\'s own errors when it refuses the request', async () => {
      applyScenario('review', store, TestBed.inject(NavService));
      vi.spyOn(TestBed.inject(BulkApiService), 'submitLauncherForm').mockResolvedValue({
        state: 'IN_PROGRESS', errors: [{ key: 'inc', messages: ['Enter a ServiceNow incident number.'] }], executionId: null,
      });
      const done = store.submit();
      await vi.advanceTimersByTimeAsync(3000);
      await done;
      expect(store.submission()?.state).toBe('error');
      expect(store.startFailed()[0].message).toBe(
        'The Launcher form refused the request (inc: Enter a ServiceNow incident number.). Nothing was sent for approval.');
    });

    it('says when the Launcher\'s form never appears', async () => {
      applyScenario('review', store, TestBed.inject(NavService));
      vi.spyOn(TestBed.inject(BulkApiService), 'launcherBlocks').mockResolvedValue([]);
      const done = store.submit();
      await vi.advanceTimersByTimeAsync(40000);
      await done;
      expect(store.startFailed()[0].message).toContain("its form didn't appear within 30 seconds");
    });

    it('reports a run the workflow stopped before the approval (an ERROR message block)', async () => {
      applyScenario('review', store, TestBed.inject(NavService));
      const api = TestBed.inject(BulkApiService);
      const real = api.launcherBlocks.bind(api);
      let stopped = false;
      vi.spyOn(api, 'launcherBlocks').mockImplementation(async (id) => [
        ...await real(id),
        ...(stopped ? [{ type: 'MESSAGE', config: { category: 'ERROR' }, data: { title: 'Invalid INC number', message: '<p>Use INC + 7 digits.</p>' } }] : []),
      ]);
      vi.spyOn(api, 'approvals').mockResolvedValue([]);
      const done = store.submit();
      await vi.advanceTimersByTimeAsync(3000);
      await done;
      stopped = true;
      await vi.advanceTimersByTimeAsync(3000);
      expect(store.submission()?.parts[0]).toMatchObject({ state: 'failed', done: true,
        message: 'The workflow stopped before the approval: Invalid INC number: Use INC + 7 digits.' });
    });
  });

  it('only offers temporary access the config allows', () => {
    const cfg = TestBed.inject(BulkConfigService).config;
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
    expect(overallState(of('waiting'))).toBe('waiting');
    expect(overallState(of('approved', 'starting'))).toBe('starting');
    expect(overallState(of('approved', 'approved'))).toBe('approved');
    expect(overallState(of('approved', 'denied'))).toBe('mixed');
    expect(overallState(of('approved', 'waiting', 'start-failed'))).toBe('waiting');
    expect(overallState(of('start-failed', 'start-failed'))).toBe('error');
    expect(overallState(of('denied', 'denied'))).toBe('denied');
    expect(overallState(of('approved', 'still-waiting'))).toBe('still-waiting');
    expect(partsSummary(of('approved', 'approved', 'waiting'))).toBe('2 of 3 approved · 1 waiting');
    expect(partsSummary(of('approved', 'start-failed'))).toBe('1 of 2 approved · 1 not started');
  });

  it('explains permission errors in terms of ORG_ADMIN and the Launcher', () => {
    expect(describeError({ status: 403 })).toContain('ORG_ADMIN');
    expect(describeError({ status: 400, body: { messages: [{ text: 'bad input' }] } })).toBe('bad input');
  });
});
