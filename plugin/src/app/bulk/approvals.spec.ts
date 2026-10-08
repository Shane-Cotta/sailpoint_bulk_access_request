import { DEMO_CONFIG, DEMO_PENDING, DEMO_PENDING_INCS, DEMO_SERVICE } from '../demo/fixtures';
import {
  bulkCommentOf, classify, deciderOf, expectedStatus, groupApprovals, groupMatches, OTHER_KEY, schemeLabel, selectionCounts,
  toApprovalRow,
} from './approvals';
import type { GenericApproval } from './bulk-api.service';

const row = (over: Partial<GenericApproval> = {}): GenericApproval => ({ ...structuredClone(DEMO_PENDING[0]), ...over });

describe('approvals (pure)', () => {
  it('reads the person, item, schemes and bulk comment of an access-request approval', () => {
    const r = toApprovalRow(DEMO_CONFIG, DEMO_PENDING[0]);
    expect(r).toMatchObject({
      id: DEMO_PENDING[0].id, person: 'Ada Abara', item: 'PACS Radiologist Workstation', itemType: 'ACCESS_PROFILE',
      requestType: 'GRANT_ACCESS', requester: DEMO_SERVICE.name, schemes: ['ACCESS_PROFILE_OWNER'], removalDate: null,
    });
    // The justification keeps its own " | ".
    expect(r.bulk).toEqual({
      inc: DEMO_PENDING_INCS.big, requester: 'Carmen Ruiz', approver: 'Aisha Bello', accessLabel: 'Permanent',
      justification: 'Hospital-wide move to the new PACS on 14 Oct | cutover weekend',
    });
  });

  it('finds the bulk comment among several, and ignores plain or missing comments', () => {
    const bulk = DEMO_PENDING[0].comments![0];
    expect(bulkCommentOf(DEMO_CONFIG, row({ comments: [{ comment: 'looks fine' }, bulk] }))?.inc).toBe(DEMO_PENDING_INCS.big);
    expect(bulkCommentOf(DEMO_CONFIG, row({ comments: [{ comment: 'INC0047001 please' }] }))).toBeNull();
    expect(bulkCommentOf(DEMO_CONFIG, row({ comments: undefined }))).toBeNull();
    // A comment whose INC doesn't match the configured pattern is not ours.
    expect(bulkCommentOf({ incPattern: '^RITM\\d{7}$' }, DEMO_PENDING[0])).toBeNull();
  });

  it('copes with rows that miss optional fields', () => {
    const r = toApprovalRow(DEMO_CONFIG, { id: 'x', status: 'PENDING', name: [{ value: 'Some item' }] });
    expect(r).toMatchObject({ person: '?', personId: null, item: 'Some item', schemes: [], bulk: null, due: null });
  });

  it('groups by INC, oldest first, with Other last and only when asked for', () => {
    const groups = groupApprovals(DEMO_CONFIG, DEMO_PENDING, true);
    expect(groups.map((g) => g.key)).toEqual([DEMO_PENDING_INCS.small, DEMO_PENDING_INCS.big, OTHER_KEY]);
    const [small, big, other] = groups;
    expect(big).toMatchObject({
      inc: DEMO_PENDING_INCS.big, approvals: 300, people: 150, items: 2,
      itemNames: ['Epic - Clinician Read Only', 'PACS Radiologist Workstation'],
      requester: 'Carmen Ruiz', bulkApprover: 'Aisha Bello', accessLabel: 'Permanent',
      schemes: ['ACCESS_PROFILE_OWNER'], oldest: '2026-10-07T13:15:00Z', earliestDue: '2026-10-14T13:15:00Z',
    });
    expect(small).toMatchObject({ approvals: 40, people: 40, items: 1, accessLabel: 'Temporary: 30 days', schemes: ['MANAGER'] });
    expect(small.rows[0].removalDate).toBe('2026-11-05T09:40:00Z');
    expect(other).toMatchObject({ inc: null, approvals: 3, requester: '', accessLabel: '' });
    expect(other.schemes).toEqual(['ROLE_OWNER', 'ENTITLEMENT_OWNER', 'SOURCE_OWNER']);
    expect(big.rows.map((r) => r.created)).toEqual([...big.rows.map((r) => r.created)].sort());

    expect(groupApprovals(DEMO_CONFIG, DEMO_PENDING, false).map((g) => g.key)).toEqual([DEMO_PENDING_INCS.small, DEMO_PENDING_INCS.big]);
  });

  it('skips decided and duplicate rows', () => {
    const list = [DEMO_PENDING[0], DEMO_PENDING[0], { ...DEMO_PENDING[1], status: 'APPROVED' }];
    const groups = groupApprovals(DEMO_CONFIG, list, true);
    expect(groups).toHaveLength(1);
    expect(groups[0].approvals).toBe(1);
  });

  it('joins differing requesters or labels within one INC instead of hiding them', () => {
    const a = row();
    const b = row({ id: 'other', comments: [{ comment: `${DEMO_PENDING_INCS.big} | Bulk access request by Ann | Approved by Bo | Temporary: 1 day | x` }] });
    const [g] = groupApprovals(DEMO_CONFIG, [a, b], false);
    expect(g.requester).toBe('Carmen Ruiz, Ann');
    expect(g.accessLabel).toBe('Permanent, Temporary: 1 day');
  });

  it('filters groups by INC, person, item, requester or bulk approver', () => {
    const [small, big] = groupApprovals(DEMO_CONFIG, DEMO_PENDING, false);
    expect(groupMatches(big, '')).toBe(true);
    expect(groupMatches(big, 'inc0048502')).toBe(true);
    expect(groupMatches(big, 'epic')).toBe(true);
    expect(groupMatches(big, 'ada abara')).toBe(true);
    expect(groupMatches(small, 'ada abara')).toBe(false);
    expect(groupMatches(small, 'diego')).toBe(true);
  });

  it('labels schemes and counts a selection', () => {
    expect(schemeLabel('ACCESS_PROFILE_OWNER')).toBe('Access profile owner');
    expect(schemeLabel('MANAGER')).toBe('Manager');
    const [, big] = groupApprovals(DEMO_CONFIG, DEMO_PENDING, false);
    expect(selectionCounts(big.rows.slice(0, 3))).toEqual({ approvals: 3, people: 2, items: 2 });
  });

  it('classifies each approval from what was sent and what the re-read shows', () => {
    const sent = { ok: true } as const;
    const failed = { ok: false, status: 400, message: 'nope' } as const;
    const read = (status: string) => ({ id: 'a', status });
    expect(expectedStatus('approve')).toBe('APPROVED');
    expect(expectedStatus('reject')).toBe('REJECTED');
    expect(classify('approve', sent, read('APPROVED'), 'me')).toBe('confirmed');
    expect(classify('reject', sent, read('REJECTED'), 'me')).toBe('confirmed');
    expect(classify('approve', sent, read('PENDING'), 'me')).toBeNull();          // too early: check again
    expect(classify('approve', failed, read('PENDING'), 'me')).toBe('failed');
    expect(classify('approve', sent, read('REJECTED'), 'me')).toBe('elsewhere');  // someone denied it first
    expect(classify('approve', sent, undefined, 'me')).toBe('elsewhere');         // gone from view
    expect(classify('approve', failed, read('APPROVED'), 'me')).toBe('elsewhere');
    // The detail call names the decider: a retried call that did go through is still ours.
    expect(classify('approve', failed, read('APPROVED'), 'me', 'me')).toBe('confirmed');
    expect(classify('approve', failed, read('APPROVED'), 'me', 'colleague')).toBe('elsewhere');
    expect(classify('approve', sent, read('CANCELLED'), 'me')).toBe('elsewhere');
  });

  it('reads the decider from a detail row', () => {
    expect(deciderOf({ id: 'a', status: 'APPROVED', approvedBy: [{ identityID: 'x' }] })).toBe('x');
    expect(deciderOf({ id: 'a', status: 'REJECTED', rejectedBy: [{ identityID: 'y' }] })).toBe('y');
    expect(deciderOf({ id: 'a', status: 'APPROVED', approvedBy: [] })).toBeNull();
    expect(deciderOf(null)).toBeNull();
  });
});
