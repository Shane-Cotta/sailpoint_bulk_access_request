import { TestBed } from '@angular/core/testing';
import { SailpointPluginService } from '@core';

import { DEMO_CONFIG } from '../demo/fixtures';
import { routedPlugin } from '../testing/plugin.testing';
import {
  batches, BulkApiService, escapeQuery, isTransient, launcherStopMessage, pool, retry, splitPasted, Throttle, type BulkInput,
  type LauncherFormData,
} from './bulk-api.service';

function setup(routes: Record<string, unknown>, isOrgAdmin = true) {
  const plugin = routedPlugin(routes, { id: 'me', displayName: 'Me', isOrgAdmin });
  TestBed.configureTestingModule({ providers: [{ provide: SailpointPluginService, useValue: plugin }] });
  return { api: TestBed.inject(BulkApiService), plugin };
}

const ALAN = '0123456789abcdef0123456789abcdef';

describe('BulkApiService', () => {
  it('starts the workflow through the test endpoint with the trigger contract', async () => {
    const { api, plugin } = setup({ '/v2025/workflows/wf-1/test': { workflowExecutionId: 'exec-1' } });
    const input: BulkInput = {
      people: [ALAN], items: [{ id: 'ap-1', type: 'ACCESS_PROFILE' as const, name: 'ACME Bulk Test Access' }],
      approverId: 'boss', requesterId: 'me', inc: 'INC0012345', justification: 'why',
      part: 1, parts: 1, partLabel: '', removeDuration: '', accessLabel: 'Permanent',
    };
    await expect(api.submit('wf-1', input)).resolves.toBe('exec-1');
    expect(plugin.post).toHaveBeenCalledWith('/v2025/workflows/wf-1/test', { input });
  });

  it('says so when the workflow does not start', async () => {
    const { api } = setup({ '/v2025/workflows/': {} });
    await expect(api.submit('wf-1', {} as never)).rejects.toThrow('did not start');
  });

  it('finds the workflow by name unless the config names its ID', async () => {
    const { api } = setup({ '/v2025/workflows': [{ id: 'other', name: 'Something else' }, { id: 'wf-9', name: DEMO_CONFIG.workflowName }] });
    await expect(api.workflowId({ ...DEMO_CONFIG, workflowId: null })).resolves.toBe('wf-9');
    await expect(api.workflowId({ ...DEMO_CONFIG, workflowId: 'fixed' })).resolves.toBe('fixed');
    await expect(api.workflowId({ ...DEMO_CONFIG, workflowId: null, workflowName: 'Missing' })).rejects.toThrow('install.py');
  });

  it('resolves pasted IDs, usernames and emails, and reports the rest', async () => {
    const { api } = setup({
      [`/v2025/identities/${ALAN}`]: { id: ALAN, name: 'Alan Bradley', attributes: { displayName: 'Alan Bradley' } },
      '/v2025/identities?': [{ id: 'id-mei', name: 'mei.lin', alias: 'mei.lin', emailAddress: 'mei.lin@example.edu' }],
      '/v3/search': [
        { id: 'id-k1', name: 'andrea.kim', displayName: 'Andrea Kim', email: 'andrea.kim@example.edu' },
        { id: 'id-k2', name: 'akim', displayName: 'Andrea Kim', email: 'ANDREA.KIM@example.edu' },
      ],
      // Identities missing from the search index are found through their accounts.
      '/v3/accounts': [{ identityId: 'id-andrei', name: 'Andrei Popescu', nativeIdentity: 'usr_example01',
        identity: { name: 'Andrei Popescu' }, attributes: { email: 'andrei@example.edu' }, sourceName: 'ACME SaaS' }],
    });
    const result = await api.resolvePeople([ALAN, 'Mei.Lin@example.edu', 'usr_example01', 'andrea.kim@example.edu', 'nobody']);
    expect(result.resolved.map((p) => p.id)).toEqual([ALAN, 'id-mei', 'id-andrei']);
    expect(result.unresolved).toEqual(['nobody']);
    expect(result.ambiguous).toHaveLength(1);
    expect(result.ambiguous[0].token).toBe('andrea.kim@example.edu');
    expect(result.ambiguous[0].matches.map((m) => m.id).sort()).toEqual(['id-k1', 'id-k2']);
  });

  it('resolves a pasted list of 1,000+ in batches of about 50 per /v2025/identities call, with progress', async () => {
    const ids = Array.from({ length: 600 }, (_, i) => i.toString(16).padStart(32, '0'));
    const users = Array.from({ length: 500 }, (_, i) => `user${i}@example.edu`);
    const known = (path: string) => {
      const values = [...decodeURIComponent(path).matchAll(/"([^"]+)"/g)].map((m) => m[1]);
      return values.filter((v) => v.endsWith('@example.edu') || /^[0-9a-f]{32}$/.test(v))
        .map((v) => (v.includes('@') ? { id: `id-${v}`, alias: v.split('@')[0], emailAddress: v } : { id: v, name: v }));
    };
    const { api, plugin } = setup({ '/v2025/identities?': known, '/v3/search': [], '/v3/accounts': [] });
    const progress: number[] = [];
    const result = await api.resolvePeople([...ids, ...users, 'nobody'], (done) => progress.push(done));
    expect(result.resolved).toHaveLength(1100);
    expect(result.unresolved).toEqual(['nobody']);
    const listCalls = plugin.get.mock.calls.map((c) => c[0] as string).filter((u) => u.startsWith('/v2025/identities?'));
    expect(listCalls).toHaveLength(12 + 10 + 1);           // 600 IDs, 500 words, 1 word: at most 50 per call
    for (const url of listCalls) expect(url.length).toBeLessThan(8000);
    const idCall = decodeURIComponent(listCalls[0]);
    expect(idCall).toContain('filters=id in ("');
    expect(decodeURIComponent(listCalls[12])).toContain('alias eq "user0@example.edu" or email eq "user0@example.edu" or alias eq');
    expect(progress[0]).toBe(0);
    expect(progress.at(-1)).toBe(1101);
    expect(progress).toEqual([...progress].sort((a, b) => a - b));   // only goes up
  });

  it('finds pasted IDs the identities list has not indexed yet through accounts, then one by one', async () => {
    const fresh = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
    const lonely = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';
    const { api, plugin } = setup({
      '/v2025/identities?': [],
      '/v3/accounts': (path: string) => (decodeURIComponent(path).includes('identityId in')
        ? [{ identityId: fresh, name: 'Yuki Tanaka', identity: { name: 'Yuki Tanaka' }, sourceName: 'ACME SaaS' }] : []),
      [`/v2025/identities/${lonely}`]: { id: lonely, name: 'lonely', attributes: { displayName: 'Lonely One' } },
      '/v2025/identities/': () => Promise.reject(Object.assign(new Error('nope'), { status: 404 })),
    });
    const result = await api.resolvePeople([fresh, lonely]);
    expect(result.resolved.map((p) => [p.id, p.name])).toEqual([[fresh, 'Yuki Tanaka'], [lonely, 'Lonely One']]);
    const singles = plugin.get.mock.calls.map((c) => c[0] as string).filter((u) => /^\/v2025\/identities\/[0-9a-f]/.test(u));
    expect(singles).toEqual([`/v2025/identities/${lonely}`]);   // the account found the other one
  });

  it('cuts batches by count and by filter length, and runs jobs with limited concurrency', async () => {
    expect(batches(Array.from({ length: 120 }, (_, i) => i), String, ',').map((b) => b.length)).toEqual([50, 50, 20]);
    const long = Array.from({ length: 50 }, (_, i) => `${'x'.repeat(190)}${i}@example.edu`);
    const cut = batches(long, (v) => `email eq "${v}"`, ' or ');
    expect(cut.length).toBeGreaterThan(1);
    expect(cut.flat()).toEqual(long);
    for (const b of cut) expect(b.map((v) => `email eq "${v}"`).join(' or ').length).toBeLessThanOrEqual(4000);

    let running = 0;
    let peak = 0;
    await pool(Array.from({ length: 10 }, () => async () => {
      peak = Math.max(peak, ++running);
      await new Promise((r) => setTimeout(r, 1));
      running--;
    }), 3);
    expect(peak).toBe(3);
  });

  it('retries throttled calls (HTTP 429), and nothing else', async () => {
    let n = 0;
    await expect(retry(async () => {
      if (++n < 3) throw Object.assign(new Error('slow down'), { status: 429 });
      return 'ok';
    }, 3, 1)).resolves.toBe('ok');
    let m = 0;
    await expect(retry(async () => {
      m++;
      throw Object.assign(new Error('bad'), { status: 400 });
    }, 3, 1)).rejects.toThrow('bad');
    expect(m).toBe(1);
  });

  it('pages through access requests so a 600-person request fits', async () => {
    const row = (i: number) => ({ id: `r${i}`, name: 'X', type: 'ACCESS_PROFILE', state: 'REQUEST_COMPLETED' });
    const { api, plugin } = setup({
      '/v3/access-request-status': (path: string) => {
        const offset = Number(new URLSearchParams(path.split('?')[1]).get('offset'));
        return Array.from({ length: Math.max(0, Math.min(250, 600 - offset)) }, (_, i) => row(offset + i));
      },
    });
    expect(await api.myAccessRequests('me')).toHaveLength(600);
    expect(plugin.get).toHaveBeenCalledTimes(3);
  });

  it('searches identities and accounts, de-duplicated and sorted', async () => {
    const { api, plugin } = setup({
      '/v3/search': [{ id: 'a', displayName: 'Zed', email: 'z@x' }],
      '/v3/accounts': [{ identityId: 'a', name: 'Zed' }, { identityId: 'b', name: 'Amy', identity: { name: 'Amy' } }],
    });
    expect((await api.searchPeople('am')).map((p) => p.name)).toEqual(['Amy', 'Zed']);
    expect(await api.searchPeople('a')).toEqual([]);                      // too short: no call
    expect(plugin.post).toHaveBeenCalledTimes(1);
  });

  it('loads access profiles and roles from requestable-objects and entitlements from the entitlements API', async () => {
    const { api, plugin } = setup({
      '/v3/requestable-objects': [
        { id: 'ap', type: 'ACCESS_PROFILE', name: 'ACME Bulk Test Access' },
        { id: 'r', type: 'ROLE', name: 'ACME Role' },
        { id: 'x', type: 'ACCESS_PROFILE', name: 'Other' },
      ],
      // Entitlement rows have no `type` and carry their source.
      '/v2025/entitlements': [{ id: 'e', name: 'ACME Group', requestable: true, source: { name: 'Active Directory' } }],
      '/v3/search': [{ id: 'ap', source: { name: 'ACME SaaS' } }],
    });
    const options = await api.catalog({ ...DEMO_CONFIG, nameStartsWith: 'ACME' });
    expect(options.map((o) => o.subLabel)).toEqual(['Access profile · ACME SaaS', 'Entitlement · Active Directory', 'Role']);
    expect(options[1].value).toEqual({ id: 'e', type: 'ENTITLEMENT', name: 'ACME Group' });
    const [objects, entitlements] = plugin.get.mock.calls.map((c) => decodeURIComponent(c[0] as string));
    expect(objects).toContain('/v3/requestable-objects?identity-id=me&types=ACCESS_PROFILE&types=ROLE&');
    expect(objects).not.toContain('ENTITLEMENT');
    expect(objects).toContain('name sw "ACME"');
    expect(entitlements).toContain('/v2025/entitlements?filters=requestable eq true and name sw "ACME"');
    // Only access profiles need a source lookup (the stub ignores the name filter, so 'x' is asked too).
    expect((plugin.post.mock.calls[0][1] as { query: { query: string } }).query.query).toBe('id:(ap OR x)');
  });

  it('skips requestable-objects when only entitlements are offered', async () => {
    const { api, plugin } = setup({ '/v2025/entitlements': [{ id: 'e', name: 'Group' }] });
    const options = await api.catalog({ ...DEMO_CONFIG, catalogTypes: ['ENTITLEMENT'], nameStartsWith: null });
    expect(options.map((o) => o.value.type)).toEqual(['ENTITLEMENT']);
    expect(plugin.get).toHaveBeenCalledTimes(1);
  });

  it('reports which chosen people already hold or have requested an item', async () => {
    const { api } = setup({
      '/v3/requestable-objects?identity-id=p1': [{ id: 'ap', requestStatus: 'ASSIGNED' }],
      '/v3/requestable-objects?identity-id=p2': [{ id: 'ap', requestStatus: 'AVAILABLE' }],
      '/v3/requestable-objects?identity-id=p3': [{ id: 'ap', requestStatus: 'PENDING' }],
    });
    const found = await api.existingAccess(['p1', 'p2', 'p3'], [{ id: 'ap', type: 'ACCESS_PROFILE', name: 'X' }]);
    expect(found).toEqual(expect.arrayContaining([
      { personId: 'p1', itemId: 'ap', status: 'ASSIGNED' },
      { personId: 'p3', itemId: 'ap', status: 'PENDING' },
    ]));
    expect(found).toHaveLength(2);
  });

  it('checks entitlements through identity search (held) and access-request-status (pending)', async () => {
    const { api, plugin } = setup({
      '/v3/search': [
        { id: 'p1', access: [{ id: 'ent', type: 'ENTITLEMENT' }, { id: 'other', type: 'ENTITLEMENT' }] },
        { id: 'p2', access: [{ id: 'ap', type: 'ACCESS_PROFILE' }] },
      ],
      '/v3/access-request-status?requested-for=p1': [{ id: 'ent', type: 'ENTITLEMENT', state: 'EXECUTING', requestType: 'GRANT_ACCESS' }],
      '/v3/access-request-status?requested-for=p2': [
        { id: 'ent', type: 'ENTITLEMENT', state: 'EXECUTING', requestType: 'GRANT_ACCESS' },
        { id: 'ent2', type: 'ENTITLEMENT', state: 'EXECUTING', requestType: 'GRANT_ACCESS' },   // not chosen
      ],
      '/v3/access-request-status?requested-for=p3': [{ id: 'ent', type: 'ENTITLEMENT', state: 'EXECUTING', requestType: 'REVOKE_ACCESS' }],
    });
    const found = await api.existingAccess(['p1', 'p2', 'p3'], [{ id: 'ent', type: 'ENTITLEMENT', name: 'Group' }]);
    expect(found).toEqual(expect.arrayContaining([
      { personId: 'p1', itemId: 'ent', status: 'ASSIGNED' },     // held wins over its open request
      { personId: 'p2', itemId: 'ent', status: 'PENDING' },
    ]));
    expect(found).toHaveLength(2);
    expect(plugin.get.mock.calls.map((c) => String(c[0])).filter((u) => u.includes('requestable-objects'))).toEqual([]);
    expect(plugin.get.mock.calls.every((c) => String(c[0]).includes('&request-state=EXECUTING&'))).toBe(true);
    const search = plugin.post.mock.calls[0][1] as { indices: string[]; query: { query: string } };
    expect(search.indices).toEqual(['identities']);
    expect(search.query.query).toBe('id:(p1 OR p2 OR p3)');
  });

  describe('as a non-admin (only calls an sp:user session may make; verified live)', () => {
    const urls = (plugin: ReturnType<typeof routedPlugin>) =>
      [...plugin.get.mock.calls, ...plugin.post.mock.calls].map((c) => String(c[0]));
    const publicRow = (id: string, name: string, email: string, department = 'Radiology') =>
      ({ id, name, alias: name, email, attributes: [{ key: 'department', name: 'Department', value: department }] });

    it('searches people through public identities (search and accounts are 403)', async () => {
      const { api, plugin } = setup({ '/v3/public-identities': [publicRow('id-1', 'Mei.Lin', 'mei@example.edu')] }, false);
      expect(await api.searchPeople('mei"')).toEqual([{ id: 'id-1', name: 'Mei.Lin', email: 'mei@example.edu', detail: 'Radiology' }]);
      expect(urls(plugin)).toHaveLength(1);
      const params = new URLSearchParams(urls(plugin)[0].split('?')[1]);
      expect(params.get('filters')).toBe('displayName sw "mei\\"" or alias sw "mei\\"" or email sw "mei\\"" or firstname sw "mei\\"" '
        + 'or lastname sw "mei\\""');
      expect(params.get('sorters')).toBe('name');
    });

    it('resolves pasted IDs, usernames and emails through public identities, with no admin-only fallbacks', async () => {
      const { api, plugin } = setup({
        '/v3/public-identities': (path: string) => {
          const f = new URLSearchParams(path.split('?')[1]).get('filters') ?? '';
          return f.startsWith('id in') ? [publicRow(ALAN, 'Alan.Bradley', 'alan@example.edu')]
            : [publicRow('id-mei', 'mei.lin', 'Mei.Lin@example.edu')];
        },
      }, false);
      const result = await api.resolvePeople([ALAN, 'MEI.LIN@example.edu', 'nobody']);
      expect(result.resolved.map((p) => p.id)).toEqual([ALAN, 'id-mei']);
      expect(result.unresolved).toEqual(['nobody']);
      expect(urls(plugin).every((u) => u.startsWith('/v3/public-identities?'))).toBe(true);
      const filters = urls(plugin).map((u) => new URLSearchParams(u.split('?')[1]).get('filters'));
      expect(filters).toContain(`id in ("${ALAN}")`);
      expect(filters).toContain('alias eq "MEI.LIN@example.edu" or email eq "MEI.LIN@example.edu" or alias eq "nobody" or email eq "nobody"');
    });

    it('asks for the catalog as the signed-in user (403 without identity-id) and skips source names (search)', async () => {
      const { api, plugin } = setup({
        '/v3/requestable-objects': [{ id: 'ap-1', name: 'ACME Bulk Test Access', type: 'ACCESS_PROFILE' }],
        '/v2025/entitlements': [],
      }, false);
      const options = await api.catalog({ ...DEMO_CONFIG, nameStartsWith: null });
      expect(options.map((o) => o.value.id)).toEqual(['ap-1']);
      expect(urls(plugin)[0]).toMatch(/^\/v3\/requestable-objects\?identity-id=me&types=ACCESS_PROFILE&types=ROLE&/);
      expect(urls(plugin).some((u) => u.startsWith('/v3/search'))).toBe(false);
    });

    it('checks access profiles for other people, and leaves entitlements out', async () => {
      const { api, plugin } = setup({ '/v3/requestable-objects?identity-id=p1': [{ id: 'ap', requestStatus: 'ASSIGNED' }] }, false);
      const found = await api.existingAccess(['p1'], [
        { id: 'ap', type: 'ACCESS_PROFILE', name: 'X' }, { id: 'ent', type: 'ENTITLEMENT', name: 'Group' }]);
      expect(found).toEqual([{ personId: 'p1', itemId: 'ap', status: 'ASSIGNED' }]);
      expect(api.entitlementsChecked()).toBe(false);
      expect(urls(plugin).every((u) => u.startsWith('/v3/requestable-objects?identity-id=p1'))).toBe(true);
    });
  });

  it('asks for the catalog with the signed-in admin\'s identity-id too', async () => {
    const { api, plugin } = setup({ '/v3/requestable-objects': [], '/v2025/entitlements': [] });
    await api.catalog({ ...DEMO_CONFIG, nameStartsWith: null });
    expect(String(plugin.get.mock.calls[0][0])).toContain('/v3/requestable-objects?identity-id=me&');
    expect(api.entitlementsChecked()).toBe(true);
  });

  describe('submitting through the Launcher (CONTRACTS §9)', () => {
    const formData: LauncherFormData = {
      people: [ALAN], items: [{ id: 'ap-1', type: 'ACCESS_PROFILE', name: 'ACME Bulk Test Access' }], approver: ['boss'],
      inc: 'INC0012345', justification: 'why', accessType: true, duration: '720', durationUnit: ['h'], partLabel: ' (2/3)',
    };

    it('launches the Launcher with an empty body and returns the interactive process', async () => {
      const answers = [{ interactiveProcessId: '01PROCESS' }, {}];
      const { api, plugin } = setup({ '/v2025/launchers/ln-1/launch': () => answers.shift() });
      await expect(api.launch('ln-1')).resolves.toBe('01PROCESS');
      expect(plugin.post).toHaveBeenCalledWith('/v2025/launchers/ln-1/launch', {});
      await expect(api.launch('ln-1')).rejects.toThrow('did not start');
    });

    it('polls the process blocks until its FORM block names the form instance', async () => {
      vi.useFakeTimers();
      let calls = 0;
      const { api, plugin } = setup({
        '/beta/interactive-processes/01P/blocks': () => (++calls < 3 ? { items: [] }
          : { items: [{ type: 'FORM', config: { formInstanceId: 'fi-1' }, data: { title: 'ACME' } }] }),
      });
      const found = api.launcherFormInstance('01P');
      await vi.advanceTimersByTimeAsync(5000);
      await expect(found).resolves.toBe('fi-1');
      expect(plugin.get).toHaveBeenCalledTimes(3);
      vi.useRealTimers();
    });

    it('gives up with a clear message when the form never appears', async () => {
      vi.useFakeTimers();
      const { api } = setup({ '/beta/interactive-processes/': { items: [] } });
      const found = api.launcherFormInstance('01P', 3000);
      const check = expect(found).rejects.toThrow("its form didn't appear within 3 seconds");
      await vi.advanceTimersByTimeAsync(5000);
      await check;
      vi.useRealTimers();
    });

    it('submits the form with one JSON Patch, repeating until it is SUBMITTED, and returns the run behind it', async () => {
      const states = ['IN_PROGRESS', 'SUBMITTED'];
      const { api, plugin } = setup({
        'PATCH /v2025/form-instances/fi-1': {},
        '/v2025/form-instances/fi-1': () => ({ state: states.shift(), formErrors: [],
          createdBy: { type: 'WORKFLOW_EXECUTION', id: 'run-1' } }),
      });
      await expect(api.submitLauncherForm('fi-1', formData)).resolves.toEqual({ state: 'SUBMITTED', errors: [], executionId: 'run-1' });
      expect(plugin.patch).toHaveBeenCalledTimes(2);
      expect(plugin.patch).toHaveBeenCalledWith('/v2025/form-instances/fi-1', [
        { op: 'replace', path: '/formData', value: formData },
        { op: 'replace', path: '/state', value: 'SUBMITTED' },
      ]);
    });

    it("stops at the form's own validation errors and returns them", async () => {
      const { api, plugin } = setup({
        'PATCH /v2025/form-instances/': {},
        '/v2025/form-instances/fi-1': { state: 'IN_PROGRESS', createdBy: { type: 'WORKFLOW_EXECUTION', id: 'run-1' },
          formErrors: [{ key: 'inc', messages: [{ text: 'Enter a ServiceNow incident number.' }] }] },
      });
      await expect(api.submitLauncherForm('fi-1', formData)).resolves.toEqual({
        state: 'IN_PROGRESS', errors: [{ key: 'inc', messages: ['Enter a ServiceNow incident number.'] }], executionId: 'run-1',
      });
      expect(plugin.patch).toHaveBeenCalledTimes(1);
    });

    it('finds an ERROR message among the blocks (the workflow stopped before the approval)', () => {
      expect(launcherStopMessage([{ type: 'FORM', config: { formInstanceId: 'x' } }])).toBeNull();
      expect(launcherStopMessage([{ type: 'MESSAGE', config: { category: 'INFO' }, data: { title: 'Sent' } }])).toBeNull();
      expect(launcherStopMessage([
        { type: 'FORM', config: { formInstanceId: 'x' } },
        { type: 'MESSAGE', data: { category: 'ERROR', title: 'Choose a different approver', message: '<p>Not <b>you</b>.</p>' } },
      ])).toBe('Choose a different approver: Not you .');
    });

    it("lists the caller's own approvals with the requesterId query parameter (the filter is empty for non-admins)", async () => {
      const { api, plugin } = setup({ '/v2025/generic-approvals?': [] });
      await api.approvals('me-1');
      const params = new URLSearchParams(String(plugin.get.mock.calls[0][0]).split('?')[1]);
      expect(params.get('requesterId')).toBe('me-1');
      expect(params.get('filters')).toBeNull();
      expect(params.get('sorters')).toBe('-createdDate');
    });
  });

  it("loads the user's own bulk approvals with approver details the list leaves out", async () => {
    const row = (id: string, name: string, requester: string, created: string) =>
      ({ id, name: [{ value: name }], status: 'PENDING', requester: { identityID: requester }, createdDate: created });
    const { api, plugin } = setup({
      '/v2025/generic-approvals?': [
        row('a1', 'Bulk access INC0000001', 'me', '2026-10-01'),
        row('a2', 'Bulk access INC0000002', 'someone-else', '2026-10-02'),
        row('a3', 'Quarterly review', 'me', '2026-10-03'),
        row('a4', 'Bulk access INC0000004', 'me', '2026-10-04'),
      ],
      '/v2025/generic-approvals/a1': { approvers: [{ name: 'Aisha Bello' }] },
      '/v2025/generic-approvals/a4': Promise.reject(new Error('boom')),
    });
    const mine = await api.myBulkApprovals('me');
    expect(mine.map((a) => a.id)).toEqual(['a4', 'a1']);                 // newest first, ours only
    expect(mine[1].approvers).toEqual([{ name: 'Aisha Bello' }]);
    expect(mine[0].name?.[0].value).toBe('Bulk access INC0000004');     // detail failed: list row kept
    expect(plugin.get).toHaveBeenCalledTimes(3);
  });

  describe('Approvals tab', () => {
    const fail = (status: number, text = 'boom') => Object.assign(new Error(text), { status, body: { messages: [{ text }] } });

    function quick(routes: Record<string, unknown>) {
      const s = setup(routes);
      s.api.approvalsThrottle.intervalMs = 0;
      s.api.decideRetryWaitMs = 1;
      return s;
    }

    it('pages through pending approvals up to maxRows, oldest first, and says when it stopped early', async () => {
      const { api, plugin } = quick({
        '/v2025/generic-approvals?': (path: string) => {
          const limit = Number(new URLSearchParams(path.split('?')[1]).get('limit'));
          return Array.from({ length: limit }, (_, i) => ({ id: `a${i}`, status: 'PENDING' }));
        },
      });
      const { approvals, truncated } = await api.pendingAccessApprovals(600);
      expect(approvals).toHaveLength(600);
      expect(truncated).toBe(true);
      const urls = plugin.get.mock.calls.map((c) => decodeURIComponent(c[0] as string));
      expect(urls.map((u) => new URLSearchParams(u.split('?')[1]).get('limit'))).toEqual(['250', '250', '100']);
      expect(urls[1]).toContain('mine=true&include-comments=true&limit=250&offset=250');
      expect(urls[0]).toContain('filters=status eq "PENDING" and type eq "ACCESS_REQUEST_APPROVAL"&sorters=createdDate');
    });

    it('stops at a short page without calling it truncated', async () => {
      const { api, plugin } = quick({ '/v2025/generic-approvals?': [{ id: 'a', status: 'PENDING' }] });
      expect(await api.pendingAccessApprovals(5000)).toEqual({ approvals: [{ id: 'a', status: 'PENDING' }], truncated: false });
      expect(plugin.get).toHaveBeenCalledTimes(1);
    });

    it('decides one by one with limited concurrency, retrying 429 and 5xx but not 4xx', async () => {
      const seen = new Map<string, number>();
      let running = 0;
      let peak = 0;
      const { api, plugin } = quick({
        '/v2025/generic-approvals/': async (path: string) => {
          const id = path.split('/')[3];
          const n = (seen.get(id) ?? 0) + 1;
          seen.set(id, n);
          peak = Math.max(peak, ++running);
          await new Promise((r) => setTimeout(r, 2));
          running--;
          if (id === 'throttled' && n === 1) throw fail(429);
          if (id === 'flaky' && n < 3) throw fail(503);
          if (id === 'bad') throw fail(400, 'The approval is not pending.');
          if (id === 'gone') throw fail(403);
          return id === 'rejected-200' ? { id, status: 'REJECTED' } : null;   // approve 200 with a body, reject may be empty
        },
      });
      const ids = ['a', 'b', 'throttled', 'flaky', 'bad', 'gone', 'c', 'rejected-200'];
      const progress: number[] = [];
      const results = await api.decideApprovals('approve', ids, '  ok  ', {
        concurrency: 3, useBulk: false, onProgress: (done) => progress.push(done),
      });
      expect(peak).toBeLessThanOrEqual(3);
      expect([...results.entries()].filter(([, r]) => r.ok).map(([id]) => id).sort())
        .toEqual(['a', 'b', 'c', 'flaky', 'rejected-200', 'throttled']);
      expect(results.get('bad')).toEqual({ ok: false, status: 400, message: 'The approval is not pending.' });
      expect(results.get('gone')).toMatchObject({ ok: false, status: 403 });
      expect((results.get('gone') as { message: string }).message).toContain('assigned to you');
      expect(seen.get('bad')).toBe(1);
      expect(seen.get('flaky')).toBe(3);
      expect(plugin.post).toHaveBeenCalledWith('/v2025/generic-approvals/a/approve', { comment: 'ok' });
      expect(progress[0]).toBe(0);
      expect(progress.at(-1)).toBe(8);
    });

    it('sends no comment field when the comment is empty', async () => {
      const { api, plugin } = quick({ '/v2025/generic-approvals/': null });
      await api.decideApprovals('reject', ['x'], '', { concurrency: 1, useBulk: false });
      expect(plugin.post).toHaveBeenCalledWith('/v2025/generic-approvals/x/reject', {});
    });

    it('uses the bulk endpoint in batches of 50, and falls back to one by one after a 403', async () => {
      const ids = Array.from({ length: 120 }, (_, i) => `id${i}`);
      const ok = quick({ '/v2025/generic-approvals/bulk-reject': {} });
      const results = await ok.api.decideApprovals('reject', ids, 'no', { concurrency: 4, useBulk: true });
      expect(ok.plugin.post.mock.calls.map((c) => (c[1] as { approvalIds: string[] }).approvalIds.length)).toEqual([50, 50, 20]);
      expect(ok.plugin.post.mock.calls[0][1]).toMatchObject({ comment: 'no' });
      expect([...results.values()].every((r) => r.ok)).toBe(true);

      TestBed.resetTestingModule();
      const refused = quick({
        '/v2025/generic-approvals/bulk-': () => Promise.reject(fail(403, 'Forbidden')),
        '/v2025/generic-approvals/': {},
      });
      const after = await refused.api.decideApprovals('approve', ids, '', { concurrency: 4, useBulk: true });
      const paths = refused.plugin.post.mock.calls.map((c) => c[0] as string);
      expect(paths.filter((p) => p.includes('bulk-'))).toHaveLength(1);    // the first refusal switches the rest over
      expect(paths.filter((p) => /\/id\d+\/approve$/.test(p))).toHaveLength(120);
      expect([...after.values()].every((r) => r.ok)).toBe(true);
    });

    it('re-reads states 50 IDs per call (any assignee)', async () => {
      const ids = Array.from({ length: 120 }, (_, i) => `id${i}`);
      const { api, plugin } = quick({
        '/v2025/generic-approvals?': (path: string) => [...decodeURIComponent(path).matchAll(/"([^"]+)"/g)]
          .map((m) => ({ id: m[1], status: m[1] === 'id7' ? 'APPROVED' : 'PENDING' }))
          .filter((r) => r.id !== 'id9'),
      });
      const states = await api.approvalStatuses(ids);
      expect(plugin.get).toHaveBeenCalledTimes(3);
      const url = decodeURIComponent(plugin.get.mock.calls[0][0] as string);
      expect(url).toContain('filters=approvalId in ("id0","id1",');
      expect(url).not.toContain('mine=true');
      expect(states.get('id7')?.status).toBe('APPROVED');
      expect(states.has('id9')).toBe(false);
      expect(states.size).toBe(119);
    });

    it('spaces calls out with the throttle and retries what isTransient allows', async () => {
      vi.useFakeTimers();
      try {
        const throttle = new Throttle(125);
        const at: number[] = [];
        const start = Date.now();
        const all = Promise.all([1, 2, 3, 4].map(() => throttle.wait().then(() => at.push(Date.now() - start))));
        await vi.advanceTimersByTimeAsync(1000);
        await all;
        expect(at).toEqual([0, 125, 250, 375]);
      } finally {
        vi.useRealTimers();
      }
      expect(isTransient(fail(502))).toBe(true);
      expect(isTransient(fail(429))).toBe(true);
      expect(isTransient(fail(409))).toBe(false);
      expect(isTransient(new Error('network'))).toBe(false);
      let n = 0;
      await expect(retry(async () => {
        if (++n < 2) throw fail(500);
        return 'ok';
      }, 3, 1, isTransient)).resolves.toBe('ok');
    });
  });

  it('splits pasted lists and escapes search terms', () => {
    expect(splitPasted(' a@x.edu\nb, c;a@x.edu\t\n')).toEqual(['a@x.edu', 'b', 'c']);
    expect(escapeQuery('o"brien (x)')).toBe('o\\"brien \\(x\\)');
  });
});
