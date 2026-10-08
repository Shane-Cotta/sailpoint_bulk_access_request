/**
 * The Approvals tab, pure part: the signed-in user's pending access-request approvals,
 * grouped by the bulk request (INC) that created them.
 *
 * Each person × item a bulk request files gets its own approval when the item has an
 * approval scheme (owner, manager, …). Their comment is the bulk item comment
 * (CONTRACTS §4), which parseBulkComment() reads back. SailPoint can't search comment
 * text, so the grouping happens here.
 */
import type { DecideAction, DecideResult, GenericApproval } from './bulk-api.service';
import type { RuntimeConfig } from './runtime-config';
import { parseBulkComment, type BulkComment } from './rules';

/** The group key for approvals that didn't come from a bulk request. */
export const OTHER_KEY = '';

export interface ApprovalRow {
  id: string;
  person: string;
  personId: string | null;
  item: string;
  /** ACCESS_PROFILE, ROLE, ENTITLEMENT (requestedTarget.targetType). */
  itemType: string;
  /** GRANT_ACCESS or REVOKE_ACCESS. */
  requestType: string;
  /** Who filed the access request (for bulk requests: the workflow owner). */
  requester: string;
  created: string;
  due: string | null;
  /** When temporary access would be removed (null = permanent). */
  removalDate: string | null;
  /** Approval schemes from approvalConfig.serialChain[].identityType, e.g. ACCESS_PROFILE_OWNER. */
  schemes: string[];
  /** The bulk item comment, read back; null for anything else. */
  bulk: BulkComment | null;
}

export interface ApprovalGroup {
  /** The INC, or OTHER_KEY. */
  key: string;
  inc: string | null;
  /** Oldest first. */
  rows: ApprovalRow[];
  approvals: number;
  people: number;
  items: number;
  itemNames: string[];
  /** From the bulk comment: who asked, who approved the bulk request, the access label (joined if several). */
  requester: string;
  bulkApprover: string;
  accessLabel: string;
  justification: string;
  schemes: string[];
  oldest: string;
  earliestDue: string | null;
}

const uniq = (values: (string | null | undefined)[]) => [...new Set(values.filter((v): v is string => !!v))];

/** The bulk comment among an approval's comments (the first one that parses), or null. */
export function bulkCommentOf(cfg: Pick<RuntimeConfig, 'incPattern'>, a: GenericApproval): BulkComment | null {
  for (const c of a.comments ?? []) {
    const parsed = parseBulkComment(cfg, c.comment);
    if (parsed) return parsed;
  }
  return null;
}

export function toApprovalRow(cfg: Pick<RuntimeConfig, 'incPattern'>, a: GenericApproval): ApprovalRow {
  const target = a.requestedTarget ?? {};
  return {
    id: a.id,
    person: a.requestee?.name || a.requestee?.identityID || '?',
    personId: a.requestee?.identityID ?? null,
    item: target.name || a.name?.[0]?.value || '?',
    itemType: target.targetType ?? '',
    requestType: target.requestType ?? 'GRANT_ACCESS',
    requester: a.requester?.name ?? '',
    created: a.createdDate ?? '',
    due: a.dueDate ?? null,
    removalDate: target.removalDate ?? null,
    schemes: uniq((a.approvalConfig?.serialChain ?? []).map((s) => s.identityType)),
    bulk: bulkCommentOf(cfg, a),
  };
}

/** Earliest of some ISO dates, or null. */
function earliest(values: (string | null)[]): string | null {
  return uniq(values).sort()[0] ?? null;
}

function summarize(key: string, rows: ApprovalRow[]): ApprovalGroup {
  rows.sort((x, y) => x.created.localeCompare(y.created) || x.person.localeCompare(y.person) || x.item.localeCompare(y.item));
  const bulk = rows.map((r) => r.bulk).filter((b): b is BulkComment => !!b);
  const itemNames = uniq(rows.map((r) => r.item)).sort((a, b) => a.localeCompare(b));
  return {
    key,
    inc: key === OTHER_KEY ? null : key,
    rows,
    approvals: rows.length,
    people: new Set(rows.map((r) => r.personId ?? r.person)).size,
    items: itemNames.length,
    itemNames,
    requester: uniq(bulk.map((b) => b.requester)).join(', '),
    bulkApprover: uniq(bulk.map((b) => b.approver)).join(', '),
    accessLabel: uniq(bulk.map((b) => b.accessLabel)).join(', '),
    justification: uniq(bulk.map((b) => b.justification)).join(' / '),
    schemes: uniq(rows.flatMap((r) => r.schemes)),
    oldest: earliest(rows.map((r) => r.created)) ?? '',
    earliestDue: earliest(rows.map((r) => r.due)),
  };
}

/**
 * Group pending approvals by INC, oldest group first. Approvals without a bulk comment go
 * in one "Other" group (key OTHER_KEY), last, and only when `showOther` is on.
 */
export function groupApprovals(cfg: Pick<RuntimeConfig, 'incPattern'>, approvals: GenericApproval[],
                               showOther: boolean): ApprovalGroup[] {
  const byKey = new Map<string, ApprovalRow[]>();
  const seen = new Set<string>();
  for (const a of approvals) {
    if (seen.has(a.id) || (a.status && a.status !== 'PENDING')) continue;
    seen.add(a.id);
    const row = toApprovalRow(cfg, a);
    const key = row.bulk?.inc ?? OTHER_KEY;
    if (key === OTHER_KEY && !showOther) continue;
    if (!byKey.has(key)) byKey.set(key, []);
    byKey.get(key)!.push(row);
  }
  const groups = [...byKey.entries()].map(([key, rows]) => summarize(key, rows));
  return groups.sort((a, b) => Number(a.key === OTHER_KEY) - Number(b.key === OTHER_KEY)
    || a.oldest.localeCompare(b.oldest) || a.key.localeCompare(b.key));
}

/** Does a group match the filter text (its INC, a person, an item, the requester or bulk approver)? */
export function groupMatches(g: ApprovalGroup, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return [g.key, g.requester, g.bulkApprover, ...g.itemNames].some((v) => v.toLowerCase().includes(q))
    || g.rows.some((r) => r.person.toLowerCase().includes(q));
}

/** "Access profile owner" from ACCESS_PROFILE_OWNER. */
export function schemeLabel(scheme: string): string {
  const words = scheme.toLowerCase().split('_').filter(Boolean).join(' ');
  return words ? words[0].toUpperCase() + words.slice(1) : scheme;
}

/** The counts the confirm dialog repeats: approvals, people and items in a set of rows. */
export function selectionCounts(rows: ApprovalRow[]): { approvals: number; people: number; items: number } {
  return {
    approvals: rows.length,
    people: new Set(rows.map((r) => r.personId ?? r.person)).size,
    items: new Set(rows.map((r) => r.item)).size,
  };
}

// ── Outcomes ──────────────────────────────────────────────────────────────────
/**
 * What became of one approval after Approve all / Deny all:
 *  - confirmed: re-read shows the decision we sent, and our call succeeded (or the detail names us);
 *  - elsewhere: decided by someone else (a governance-group colleague or an admin), or no longer visible.
 *    Not an error;
 *  - pending: our call went through but it still reads PENDING (retry);
 *  - failed: our call failed and it's still pending (retry, with the reason).
 */
export type Outcome = 'queued' | 'sending' | 'checking' | 'confirmed' | 'elsewhere' | 'pending' | 'failed';

/** The status a decision should leave behind. */
export function expectedStatus(action: DecideAction): 'APPROVED' | 'REJECTED' {
  return action === 'approve' ? 'APPROVED' : 'REJECTED';
}

/**
 * Classify one approval from what we sent and what the re-read says. `row` is the list row
 * (undefined if it's gone); `decider` is the identity ID from the detail call, when we made one.
 * Returns null when it's too early to say (sent, still PENDING: check again).
 */
export function classify(action: DecideAction, sent: DecideResult | undefined, row: GenericApproval | undefined,
                         me: string, decider?: string | null): Exclude<Outcome, 'queued' | 'sending' | 'checking'> | null {
  if (!row) return 'elsewhere';
  if (row.status === 'PENDING') return sent?.ok ? null : 'failed';
  if (decider) return decider === me && row.status === expectedStatus(action) ? 'confirmed' : 'elsewhere';
  return sent?.ok && row.status === expectedStatus(action) ? 'confirmed' : 'elsewhere';
}

/** The decider's identity ID from a detail row (approvedBy / rejectedBy), or null. */
export function deciderOf(a: GenericApproval | null | undefined): string | null {
  if (!a) return null;
  const by = a.status === 'APPROVED' ? a.approvedBy : a.status === 'REJECTED' ? a.rejectedBy : null;
  return by?.[0]?.identityID ?? null;
}
