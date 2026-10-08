import { TestBed } from '@angular/core/testing';
import { SailpointPluginService } from '@core';

import { DEMO_CONFIG } from '../demo/fixtures';
import { routedPlugin } from '../testing/plugin.testing';
import { batches, BulkApiService, escapeQuery, pool, retry, splitPasted, type BulkInput } from './bulk-api.service';

function setup(routes: Record<string, unknown>) {
  const plugin = routedPlugin(routes);
  TestBed.configureTestingModule({ providers: [{ provide: SailpointPluginService, useValue: plugin }] });
  return { api: TestBed.inject(BulkApiService), plugin };
}

const ALAN = '0123456789abcdef0123456789abcdef';

describe('BulkApiService', () => {
  it('starts the workflow through the test endpoint with the trigger contract', async () => {
    const { api, plugin } = setup({ '/v3/workflows/wf-1/test': { workflowExecutionId: 'exec-1' } });
    const input: BulkInput = {
      people: [ALAN], items: [{ id: 'ap-1', type: 'ACCESS_PROFILE' as const, name: 'ACME Bulk Test Access' }],
      approverId: 'boss', requesterId: 'me', inc: 'INC0012345', justification: 'why',
      part: 1, parts: 1, partLabel: '', removeDuration: '', accessLabel: 'Permanent',
    };
    await expect(api.submit('wf-1', input)).resolves.toBe('exec-1');
    expect(plugin.post).toHaveBeenCalledWith('/v3/workflows/wf-1/test', { input });
  });

  it('says so when the workflow does not start', async () => {
    const { api } = setup({ '/v3/workflows/': {} });
    await expect(api.submit('wf-1', {} as never)).rejects.toThrow('did not start');
  });

  it('finds the workflow by name unless the config names its ID', async () => {
    const { api } = setup({ '/v3/workflows': [{ id: 'other', name: 'Something else' }, { id: 'wf-9', name: DEMO_CONFIG.workflowName }] });
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

  it('loads the catalog with repeated `types`, the name filter, and sources', async () => {
    const { api, plugin } = setup({
      '/v3/requestable-objects': [
        { id: 'ap', type: 'ACCESS_PROFILE', name: 'ACME Bulk Test Access' },
        { id: 'r', type: 'ROLE', name: 'ACME Role' },
        { id: 'x', type: 'ACCESS_PROFILE', name: 'Other' },
      ],
      '/v3/search': [{ id: 'ap', source: { name: 'ACME SaaS' } }],
    });
    const options = await api.catalog({ ...DEMO_CONFIG, nameStartsWith: 'ACME' });
    expect(options.map((o) => o.subLabel)).toEqual(['Access profile · ACME SaaS', 'Role']);
    const url = plugin.get.mock.calls[0][0] as string;
    expect(url).toContain('types=ACCESS_PROFILE&types=ROLE&types=ENTITLEMENT');
    expect(decodeURIComponent(url)).toContain('name sw "ACME"');
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

  it('splits pasted lists and escapes search terms', () => {
    expect(splitPasted(' a@x.edu\nb, c;a@x.edu\t\n')).toEqual(['a@x.edu', 'b', 'c']);
    expect(escapeQuery('o"brien (x)')).toBe('o\\"brien \\(x\\)');
  });
});
