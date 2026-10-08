import { DEMO_APPROVALS, DEMO_CONFIG, DEMO_ME, DEMO_REQUESTS } from '../demo/fixtures';
import { combinedStatus, executionIdOf, groupByInc, isBulkApproval, latestSubmission, statusLabel, type Submission } from './my-requests';

describe('My bulk requests grouping', () => {
  const groups = groupByInc(DEMO_CONFIG, DEMO_ME.id, DEMO_APPROVALS, DEMO_REQUESTS);

  it('groups approvals and access requests by INC, newest first', () => {
    expect(groups.map((g) => [g.inc, g.status])).toEqual([
      ['INC0048213', 'PENDING'],
      ['INC0048120', 'PENDING'],
      ['INC0047950', 'APPROVED'],
      ['INC0047711', 'REJECTED'],
    ]);
  });

  it('keeps every submission of a re-submitted INC, latest decision first', () => {
    const resubmitted = groups.find((g) => g.inc === 'INC0047711')!;
    expect(resubmitted.submissions.map((s) => s.status)).toEqual(['REJECTED', 'CANCELLED']);
    expect(resubmitted.requests).toEqual([]);
  });

  it('counts the people and items an approved request was filed for', () => {
    const approved = groups.find((g) => g.inc === 'INC0047950')!;
    expect(approved.people).toBe(3);
    expect(approved.items).toBe(2);
    expect(approved.submissions[0].decidedBy).toBe('Carmen Ruiz');
  });

  it("ignores other people's approvals, other approvals, and requests without an INC", () => {
    const foreign = { ...DEMO_APPROVALS[0], requester: { identityID: 'someone-else' } };
    const unrelated = { ...DEMO_APPROVALS[0], name: [{ value: 'Quarterly review' }] };
    expect(groupByInc(DEMO_CONFIG, DEMO_ME.id, [foreign, unrelated], [])).toEqual([]);
    expect(isBulkApproval(unrelated)).toBe(false);
    expect(groups.some((g) => g.requests.some((r) => r.item === 'Zoom - Licensed User'))).toBe(false);
  });

  it('a request with requests but no approval still shows, as Requested', () => {
    const only = groupByInc(DEMO_CONFIG, DEMO_ME.id, [], DEMO_REQUESTS);
    expect(only.map((g) => [g.inc, g.status])).toEqual([['INC0048120', 'REQUESTED'], ['INC0047950', 'REQUESTED']]);
  });

  it('reads the execution ID from the approval and labels statuses', () => {
    expect(executionIdOf(DEMO_APPROVALS[0])).toBe('e0000000-0000-4000-8000-000000000001');
    expect(statusLabel('REJECTED')).toEqual({ label: 'Denied', severity: 'danger' });
  });

  it('groups the parts of one INC and counts how many are approved', () => {
    const split = groups.find((g) => g.inc === 'INC0048120')!;
    expect(split.latest.map((s) => [s.part, s.parts, s.status])).toEqual([
      [1, 3, 'APPROVED'], [2, 3, 'APPROVED'], [3, 3, 'PENDING'],
    ]);
    expect(split.approvedParts).toBe(2);
    expect(split.status).toBe('PENDING');
    expect(split.people).toBe(500);                          // parts 1 and 2 were requested
    const single = groups.find((g) => g.inc === 'INC0047950')!;
    expect(single.latest).toHaveLength(1);
    expect(single.latest[0].part).toBeNull();
  });

  it('shows when temporary access ends', () => {
    const split = groups.find((g) => g.inc === 'INC0048120')!;
    expect(split.requests[0].removeDate).toMatch(/^2026-11-06T/);
    expect(groups.find((g) => g.inc === 'INC0047950')!.requests[0].removeDate).toBeNull();
  });

  it("doesn't mix a re-submitted INC's parts with its earlier attempt", () => {
    const sub = (part: number | null, parts: number | null, status: string, created: string): Submission => ({
      approvalId: `${part}-${created}`, executionId: null, status, approver: 'A', decidedBy: null, created, completed: null,
      description: '', part, parts,
    });
    const newestFirst = [
      sub(2, 2, 'PENDING', '2026-10-08T10:00:02Z'), sub(1, 2, 'APPROVED', '2026-10-08T10:00:01Z'),
      sub(3, 3, 'REJECTED', '2026-10-01T10:00:03Z'), sub(2, 3, 'REJECTED', '2026-10-01T10:00:02Z'),
    ];
    expect(latestSubmission(newestFirst).map((s) => s.approvalId)).toEqual([
      '1-2026-10-08T10:00:01Z', '2-2026-10-08T10:00:02Z',
    ]);
    expect(latestSubmission([sub(null, null, 'APPROVED', 'x'), sub(1, 2, 'REJECTED', 'w')])).toHaveLength(1);
    expect(combinedStatus(['APPROVED', 'APPROVED'])).toBe('APPROVED');
    expect(combinedStatus(['APPROVED', 'PENDING'])).toBe('PENDING');
    expect(combinedStatus(['APPROVED', 'REJECTED'])).toBe('MIXED');
    expect(statusLabel('MIXED').label).toBe('Partly approved');
  });
});
