import { DEMO_CONFIG, DEMO_ME, DEMO_REQUESTS, demoPerson, demoRequest } from '../demo/fixtures';
import { groupByInc, groupStatus, requestLabel, requestStatus, statusLabel } from './my-requests';

describe('My bulk requests grouping', () => {
  const groups = groupByInc(DEMO_CONFIG, DEMO_ME.id, DEMO_REQUESTS);

  it('groups the access requests by INC, newest first', () => {
    expect(groups.map((g) => [g.inc, g.status])).toEqual([
      ['INC0048120', 'WAITING'],
      ['INC0047950', 'WAITING'],
      ['INC0047711', 'NOT_GRANTED'],
    ]);
  });

  it('counts people, items and requests per status', () => {
    const big = groups.find((g) => g.inc === 'INC0048120')!;
    expect([big.people, big.items, big.waiting, big.done, big.notGranted]).toEqual([500, 1, 10, 490, 0]);
    const small = groups.find((g) => g.inc === 'INC0047950')!;
    expect([small.people, small.items, small.waiting, small.done]).toEqual([3, 2, 1, 5]);
    expect(small.lastActivity).toBe('2026-10-06T10:03:00Z');
  });

  it('reads the access label and justification from the bulk comment', () => {
    const big = groups.find((g) => g.inc === 'INC0048120')!;
    expect(big.accessLabel).toBe('Temporary: 30 days');
    expect(big.justification).toBe('Remote reading during the PACS cutover');
  });

  it('says who a waiting request waits for, sorted by person then item', () => {
    const small = groups.find((g) => g.inc === 'INC0047950')!;
    expect(small.requests.map((r) => [r.person, r.item])).toEqual([
      ['Amelia Thornton', 'PACS Radiologist Workstation'], ['Amelia Thornton', 'Radiology Department Staff'],
      ['Bruno Marchetti', 'PACS Radiologist Workstation'], ['Bruno Marchetti', 'Radiology Department Staff'],
      ['Claire Beaumont', 'PACS Radiologist Workstation'], ['Claire Beaumont', 'Radiology Department Staff'],
    ]);
    const waiting = small.requests.find((r) => r.status === 'WAITING')!;
    expect(waiting.waitingFor).toEqual(['Aisha Bello']);
    expect(requestLabel(waiting)).toEqual({ label: 'Waiting for Aisha Bello', severity: 'warn' });
  });

  it('shows when temporary access ends', () => {
    expect(groups.find((g) => g.inc === 'INC0048120')!.requests[0].removeDate).toMatch(/^2026-11-06T/);
    expect(groups.find((g) => g.inc === 'INC0047950')!.requests[0].removeDate).toBeNull();
  });

  it("ignores other people's requests and requests without an INC", () => {
    const foreign = { ...DEMO_REQUESTS[0], requester: { id: 'someone-else' } };
    expect(groupByInc(DEMO_CONFIG, DEMO_ME.id, [foreign])).toEqual([]);
    expect(groups.some((g) => g.requests.some((r) => r.item === 'Zoom - Licensed User'))).toBe(false);
  });

  it('still groups requests filed while bulk requests had an approver (the old comment)', () => {
    const old = demoRequest(demoPerson('Alan Bradley'), 'VPN - Remote Access', 'REQUEST_COMPLETED', '2026-09-01T10:00:00Z',
      'INC0040000 | Bulk access request by Jordan Lee | Approved by Aisha Bello | Permanent | Old request');
    expect(groupByInc(DEMO_CONFIG, DEMO_ME.id, [old]).map((g) => [g.inc, g.status, g.justification]))
      .toEqual([['INC0040000', 'DONE', 'Old request']]);
  });

  it('turns SailPoint states into plain statuses', () => {
    expect(requestStatus({ state: 'REQUEST_COMPLETED' })).toBe('DONE');
    expect(requestStatus({ state: 'REJECTED' })).toBe('NOT_GRANTED');
    expect(requestStatus({ state: 'CANCELLED' })).toBe('NOT_GRANTED');
    expect(requestStatus({ state: 'EXECUTING', approvalDetails: [{ status: 'PENDING' }] })).toBe('WAITING');
    expect(requestStatus({ state: 'EXECUTING', approvalDetails: [{ status: 'APPROVED' }] })).toBe('IN_PROGRESS');
    expect(requestStatus({ state: 'EXECUTING', approvalDetails: null })).toBe('IN_PROGRESS');
    expect(groupStatus([])).toBe('IN_PROGRESS');
    expect(groupStatus(['DONE', 'DONE'])).toBe('DONE');
    expect(groupStatus(['DONE', 'WAITING'])).toBe('WAITING');
    expect(groupStatus(['DONE', 'NOT_GRANTED'])).toBe('PARTLY_DONE');
    expect(groupStatus(['DONE', 'IN_PROGRESS'])).toBe('IN_PROGRESS');
    expect(statusLabel('PARTLY_DONE')).toEqual({ label: 'Partly granted', severity: 'warn' });
    expect(requestLabel({ status: 'NOT_GRANTED', state: 'REJECTED', waitingFor: [] }).label).toBe('Rejected');
    expect(requestLabel({ status: 'IN_PROGRESS', state: 'EXECUTING', waitingFor: [] }).label).toBe('Provisioning');
  });
});
