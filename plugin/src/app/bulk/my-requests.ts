/**
 * "My bulk requests": the access requests the signed-in user filed, grouped by INC.
 *
 * SailPoint has no single "bulk request" object. Every access request of a bulk request carries the bulk comment
 * ("<INC> | Bulk access request by …"), and the page lists the user's own requests (access-request-status
 * `requested-by=<me>`, verified live for a non-admin) and groups them by that INC. Each request is approved the way
 * its item is set up in SailPoint, so the status is per request: waiting for its approver, in progress, done or not
 * granted. Requests sent through the Launcher are filed in its workflow owner's name, so they don't show here.
 */
import type { AccessRequestStatus } from './bulk-api.service';
import type { RuntimeConfig } from './runtime-config';
import { extractInc, parseBulkComment } from './rules';

/** A group's status, from its requests' (requestStatus). */
export type GroupStatus = 'WAITING' | 'IN_PROGRESS' | 'DONE' | 'PARTLY_DONE' | 'NOT_GRANTED';

/** One access request's status in plain terms. */
export type RequestStatus = 'WAITING' | 'IN_PROGRESS' | 'DONE' | 'NOT_GRANTED';

export interface RequestRow {
  person: string;
  item: string;
  state: string;
  status: RequestStatus;
  /** Who the request waits for (its pending approvals' current owners), when WAITING. */
  waitingFor: string[];
  /** The phase it's in while IN_PROGRESS (e.g. PROVISIONING_PHASE), or null. */
  phase: string | null;
  /** Why SailPoint cancelled it, when it says (e.g. "Already has a pending request for this item"). */
  reason: string | null;
  created: string;
  /** When temporary access is removed (null = permanent). */
  removeDate: string | null;
}

export interface BulkGroup {
  inc: string;
  status: GroupStatus;
  /** The access label and justification from the bulk comment (joined if several submissions differ). */
  accessLabel: string;
  justification: string;
  requests: RequestRow[];
  people: number;
  items: number;
  waiting: number;
  done: number;
  lastActivity: string;
}

const NOT_GRANTED = ['REJECTED', 'CANCELLED', 'TERMINATED', 'ERROR', 'PROVISIONING_FAILED'];

/** One access request's status (access-request-status `state`, its current phase and its approvals). */
export function requestStatus(r: Pick<AccessRequestStatus, 'state' | 'approvalDetails' | 'accessRequestPhases'>): RequestStatus {
  if (r.state === 'REQUEST_COMPLETED') return 'DONE';
  if (NOT_GRANTED.includes(r.state)) return 'NOT_GRANTED';
  const phase = currentPhase(r);
  if (phase === 'APPROVAL_PHASE' || (!phase && (r.approvalDetails ?? []).some((a) => a.status === 'PENDING'))) return 'WAITING';
  return 'IN_PROGRESS';
}

/** The phase a request is in (the first one not finished), or null. */
export function currentPhase(r: Pick<AccessRequestStatus, 'accessRequestPhases'>): string | null {
  return (r.accessRequestPhases ?? []).find((p) => !p.finished && p.state !== 'NOT_EXECUTED')?.name ?? null;
}

/** One status for a group: all the same → that; any waiting → WAITING; all finished → PARTLY_DONE; else IN_PROGRESS. */
export function groupStatus(statuses: RequestStatus[]): GroupStatus {
  if (!statuses.length) return 'IN_PROGRESS';
  if (statuses.every((s) => s === statuses[0])) return statuses[0];
  if (statuses.includes('WAITING')) return 'WAITING';
  if (statuses.every((s) => s === 'DONE' || s === 'NOT_GRANTED')) return 'PARTLY_DONE';
  return 'IN_PROGRESS';
}

const uniq = (values: (string | null | undefined)[]) => [...new Set(values.filter((v): v is string => !!v))];

export function groupByInc(cfg: Pick<RuntimeConfig, 'incPattern'>, requesterId: string,
                           requests: AccessRequestStatus[]): BulkGroup[] {
  const groups = new Map<string, { rows: RequestRow[]; labels: string[]; why: string[] }>();
  for (const r of requests) {
    if (r.requester && r.requester.id !== requesterId) continue;
    const comment = r.requesterComment?.comment;
    const bulk = parseBulkComment(cfg, comment);
    const inc = bulk?.inc ?? extractInc(cfg, comment);
    if (!inc) continue;
    if (!groups.has(inc)) groups.set(inc, { rows: [], labels: [], why: [] });
    const g = groups.get(inc)!;
    g.labels.push(bulk?.accessLabel ?? '');
    g.why.push(bulk?.justification ?? '');
    const status = requestStatus(r);
    g.rows.push({
      person: r.requestedFor?.name ?? r.requestedFor?.id ?? '?',
      item: r.name,
      state: r.state,
      status,
      waitingFor: status === 'WAITING' ? uniq((r.approvalDetails ?? []).filter((a) => a.status === 'PENDING')
        .map((a) => a.currentOwner?.name ?? a.originalOwner?.name)) : [],
      phase: status === 'IN_PROGRESS' ? currentPhase(r) : null,
      reason: r.cancelledRequestDetails?.comment || null,
      created: r.created ?? '',
      removeDate: r.removeDate ?? null,
    });
  }
  const out: BulkGroup[] = [];
  for (const [inc, g] of groups) {
    const rows = g.rows.sort((x, y) => x.person.localeCompare(y.person) || x.item.localeCompare(y.item));
    const count = (s: RequestStatus) => rows.filter((r) => r.status === s).length;
    out.push({
      inc,
      status: groupStatus(rows.map((r) => r.status)),
      accessLabel: uniq(g.labels).join(', '),
      justification: uniq(g.why).join(' / '),
      requests: rows,
      people: new Set(rows.map((r) => r.person)).size,
      items: new Set(rows.map((r) => r.item)).size,
      waiting: count('WAITING'),
      done: count('DONE'),
      lastActivity: rows.map((r) => r.created).filter(Boolean).sort().at(-1) ?? '',
    });
  }
  return out.sort((a, b) => b.lastActivity.localeCompare(a.lastActivity));
}

type Severity = 'success' | 'warn' | 'danger' | 'info' | 'secondary';

/** Plain-English label and p-tag severity for a group's status. */
export function statusLabel(status: GroupStatus): { label: string; severity: Severity } {
  switch (status) {
    case 'WAITING': return { label: 'Waiting for approval', severity: 'warn' };
    case 'IN_PROGRESS': return { label: 'In progress', severity: 'info' };
    case 'DONE': return { label: 'Done', severity: 'success' };
    case 'PARTLY_DONE': return { label: 'Partly granted', severity: 'warn' };
    case 'NOT_GRANTED': return { label: 'Not granted', severity: 'danger' };
  }
}

/** One access request's status in plain English: who it waits for, or its state. */
export function requestLabel(r: Pick<RequestRow, 'status' | 'state' | 'waitingFor' | 'phase'>): { label: string; severity: Severity } {
  switch (r.status) {
    case 'WAITING':
      return { label: r.waitingFor.length ? `Waiting for ${r.waitingFor.join(', ')}` : 'Waiting for approval', severity: 'warn' };
    case 'DONE': return { label: 'Done', severity: 'success' };
    case 'NOT_GRANTED': return { label: { REJECTED: 'Rejected', CANCELLED: 'Cancelled' }[r.state] ?? 'Not granted', severity: 'danger' };
    case 'IN_PROGRESS': return { label: r.phase === 'PROVISIONING_PHASE' ? 'Provisioning' : r.phase ? 'Checking' : 'In progress',
      severity: 'info' };
  }
}
