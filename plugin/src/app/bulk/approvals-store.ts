import { computed, inject, Injectable, OnDestroy, signal } from '@angular/core';
import { SailpointPluginService } from '@core';

import {
  classify, deciderOf, groupApprovals, groupMatches, OTHER_KEY, selectionCounts, type ApprovalGroup, type ApprovalRow,
  type Outcome,
} from './approvals';
import { BulkApiService, pool, type DecideAction, type DecideResult, type GenericApproval } from './bulk-api.service';
import { BulkConfigService } from './bulk-config.service';
import { describeError } from './errors';

/** One approval in a run of Approve all / Deny all. */
export interface RunItem {
  id: string;
  person: string;
  item: string;
  outcome: Outcome;
  /** Why it failed, or who else decided it. */
  message: string;
}

export interface ApprovalRun {
  action: DecideAction;
  /** The INC the run belongs to (a run never spans INCs). */
  inc: string;
  comment: string;
  /** In the order sent. */
  items: RunItem[];
  /** Calls finished so far (sent or failed), out of items.length. */
  done: number;
  phase: 'sending' | 'confirming' | 'done';
  /** The bulk endpoint was tried first (ORG_ADMIN). */
  bulk: boolean;
}

export type OutcomeCounts = Record<Outcome, number>;

export function outcomeCounts(items: Pick<RunItem, 'outcome'>[]): OutcomeCounts {
  const counts: OutcomeCounts = { queued: 0, sending: 0, checking: 0, confirmed: 0, elsewhere: 0, pending: 0, failed: 0 };
  for (const i of items) counts[i.outcome]++;
  return counts;
}

/** Re-read the decided approvals after these waits (ms), then call what's still PENDING "still pending". */
export const CONFIRM_SCHEDULE_MS = [1000, 2000, 4000, 8000];

/** The signed-in user's pending access-request approvals, grouped by INC, and deciding a group at once. */
@Injectable({ providedIn: 'root' })
export class ApprovalsStore implements OnDestroy {
  private readonly api = inject(BulkApiService);
  private readonly plugin = inject(SailpointPluginService);
  private readonly bulkConfig = inject(BulkConfigService);

  readonly approvals = signal<GenericApproval[] | null>(null);
  /** The list hit approvals.maxRows: there are more than shown. */
  readonly truncated = signal(false);
  readonly loading = signal(false);
  readonly error = signal('');
  readonly loadedAt = signal<Date | null>(null);
  readonly filter = signal('');
  /** The group open in the drill-down (one at a time, so an action never spans INCs). */
  readonly openKey = signal<string | null>(null);
  /** Approval IDs left out of Approve all / Deny all in the open group. */
  readonly excluded = signal<ReadonlySet<string>>(new Set());
  readonly comment = signal('');
  readonly run = signal<ApprovalRun | null>(null);

  readonly me = computed(() => this.plugin.user()?.id ?? '');
  readonly isAdmin = computed(() => this.plugin.user()?.capabilities?.isOrgAdmin ?? false);
  readonly settings = computed(() => this.bulkConfig.config().approvals);
  /** "always", or "auto" for an ORG_ADMIN: the bulk endpoint refuses everyone else (verified live). */
  readonly useBulk = computed(() => {
    const mode = this.settings().useBulkEndpoint;
    return mode === 'always' || (mode === 'auto' && this.isAdmin());
  });

  readonly groups = computed<ApprovalGroup[]>(() =>
    groupApprovals(this.bulkConfig.config(), this.approvals() ?? [], this.settings().showOther));
  readonly shown = computed(() => this.groups().filter((g) => groupMatches(g, this.filter())));
  readonly openGroup = computed(() => this.groups().find((g) => g.key === this.openKey()) ?? null);
  /** Rows of the open group that Approve all / Deny all would decide. */
  readonly included = computed<ApprovalRow[]>(() => {
    const excluded = this.excluded();
    return (this.openGroup()?.rows ?? []).filter((r) => !excluded.has(r.id));
  });
  readonly includedCounts = computed(() => selectionCounts(this.included()));
  readonly running = computed(() => {
    const phase = this.run()?.phase;
    return phase === 'sending' || phase === 'confirming';
  });
  readonly counts = computed(() => outcomeCounts(this.run()?.items ?? []));
  /** Approvals of the last run that can be sent again (failed, or still pending). */
  readonly retryable = computed(() => (this.run()?.items ?? []).filter((i) => i.outcome === 'failed' || i.outcome === 'pending'));

  private loadGeneration = 0;
  /** Bumped by every run, dismiss and destroy, so a confirmation round in flight stops. */
  private generation = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private wake: (() => void) | null = null;
  /** What each decision call returned, for the current run. */
  private sent = new Map<string, DecideResult>();

  /** Load (or reload) the pending approvals, up to approvals.maxRows. */
  async load(): Promise<void> {
    const generation = ++this.loadGeneration;
    this.loading.set(true);
    this.error.set('');
    try {
      const { approvals, truncated } = await this.api.pendingAccessApprovals(this.settings().maxRows);
      if (generation !== this.loadGeneration) return;
      this.approvals.set(approvals);
      this.truncated.set(truncated);
      this.loadedAt.set(new Date());
      // Drop exclusions of rows that are gone; close a group that no longer exists.
      const ids = new Set(approvals.map((a) => a.id));
      this.excluded.update((s) => new Set([...s].filter((id) => ids.has(id))));
      if (this.openKey() !== null && !this.openGroup()) this.openKey.set(null);
    } catch (err) {
      if (generation !== this.loadGeneration) return;
      this.error.set(describeError(err, 'approvals'));
      if (this.approvals() === null) this.approvals.set([]);
    } finally {
      if (generation === this.loadGeneration) this.loading.set(false);
    }
  }

  /** Open a group's drill-down (or close it), starting with every row included. */
  toggleGroup(key: string): void {
    if (this.running()) return;
    this.openKey.set(this.openKey() === key ? null : key);
    this.excluded.set(new Set());
    this.comment.set('');
  }

  /** Leave one approval out of (or put it back into) the open group's action. */
  /** Include exactly these rows of the open group (the table's selection). */
  setIncluded(ids: Iterable<string>): void {
    const keep = new Set(ids);
    this.excluded.set(new Set((this.openGroup()?.rows ?? []).filter((r) => !keep.has(r.id)).map((r) => r.id)));
  }

  /** Why Approve all / Deny all can't run now (empty = it can). */
  problems(action: DecideAction): string[] {
    const g = this.openGroup();
    const problems: string[] = [];
    if (this.running()) problems.push('Wait for the current run to finish.');
    if (!g) problems.push('Open a bulk request first.');
    else if (g.key === OTHER_KEY) problems.push('Approvals that are not from a bulk request are decided one by one in SailPoint.');
    else if (!this.included().length) problems.push('Choose at least one approval.');
    if (action === 'reject' && this.settings().denyCommentRequired && !this.comment().trim()) {
      problems.push('Enter a comment to deny.');
    }
    return problems;
  }

  /** Approve or deny every included approval of the open group, then confirm each one. */
  async decide(action: DecideAction): Promise<void> {
    const g = this.openGroup();
    if (!g || this.problems(action).length) return;
    const rows = this.included();
    await this.start(action, g.key, rows.map((r) => ({ id: r.id, person: r.person, item: r.item })), this.comment().trim());
  }

  /** Send the failed and still-pending approvals of the last run again (same action and comment). */
  async retry(): Promise<void> {
    const run = this.run();
    if (!run || this.running()) return;
    const again = this.retryable();
    if (!again.length) return;
    await this.start(run.action, run.inc, again.map(({ id, person, item }) => ({ id, person, item })), run.comment);
  }

  /** Close the results panel. */
  dismiss(): void {
    if (this.running()) return;
    this.stop();
    this.generation++;
    this.run.set(null);
  }

  ngOnDestroy(): void {
    this.stop();
    this.generation++;
  }

  private async start(action: DecideAction, inc: string, rows: Pick<RunItem, 'id' | 'person' | 'item'>[],
                      comment: string): Promise<void> {
    this.stop();
    const generation = ++this.generation;
    this.sent = new Map();
    const bulk = this.useBulk();
    this.run.set({
      action, inc, comment, bulk, done: 0, phase: 'sending',
      items: rows.map((r) => ({ ...r, outcome: 'sending', message: '' })),
    });
    const results = await this.api.decideApprovals(action, rows.map((r) => r.id), comment, {
      concurrency: this.settings().concurrency,
      useBulk: bulk,
      onProgress: (done) => generation === this.generation && this.patch({ done }),
      onResult: (id, result) => {
        if (generation !== this.generation) return;
        this.sent.set(id, result);
        this.patchItem(id, { outcome: 'checking', message: result.ok ? '' : result.message });
      },
    });
    if (generation !== this.generation) return;
    for (const [id, result] of results) this.sent.set(id, result);
    this.patch({ phase: 'confirming', done: rows.length });
    await this.confirm(generation, 0);
  }

  /** One confirmation round: re-read what's still being checked and classify it. */
  private async confirm(generation: number, round: number): Promise<void> {
    this.timer = null;
    const run = this.run();
    if (!run || generation !== this.generation) return;
    const open = run.items.filter((i) => i.outcome === 'checking');
    const last = round >= CONFIRM_SCHEDULE_MS.length;
    if (open.length) {
      try {
        const rows = await this.api.approvalStatuses(open.map((i) => i.id));
        if (generation !== this.generation) return;
        // Decided, but our call didn't go through: ask the detail who decided (the list leaves it out).
        const ask = open.filter((i) => {
          const row = rows.get(i.id);
          return row && row.status !== 'PENDING' && !this.sent.get(i.id)?.ok;
        });
        const deciders = new Map<string, GenericApproval | null>();
        await pool(ask.map((i) => async () => {
          deciders.set(i.id, await this.api.approval(i.id).catch(() => null));
        }), 2);
        if (generation !== this.generation) return;
        for (const i of open) {
          const row = rows.get(i.id);
          const sent = this.sent.get(i.id);
          const outcome = classify(run.action, sent, row, this.me(), deciderOf(deciders.get(i.id)));
          if (outcome) this.patchItem(i.id, { outcome, message: this.explain(outcome, row, deciders.get(i.id), sent) });
          else if (last) this.patchItem(i.id, { outcome: 'pending', message: 'Sent, but SailPoint still shows it pending.' });
        }
      } catch (err) {
        if (generation !== this.generation) return;
        if (last) {
          for (const i of open) {
            const sent = this.sent.get(i.id);
            this.patchItem(i.id, sent?.ok
              ? { outcome: 'pending', message: `Sent, but it couldn't be checked: ${describeError(err, 'approvals')}` }
              : { outcome: 'failed', message: sent?.message ?? describeError(err, 'approvals') });
          }
        }
      }
    }
    const still = (this.run()?.items ?? []).some((i) => i.outcome === 'checking');
    if (still && !last) {
      // Wait, then check again; awaited, so decide() and retry() resolve only when the run is done.
      await new Promise<void>((resolve) => {
        this.wake = resolve;
        this.timer = setTimeout(resolve, CONFIRM_SCHEDULE_MS[round]);
      });
      this.wake = null;
      return this.confirm(generation, round + 1);
    }
    this.finish();
  }

  private explain(outcome: Outcome, row: GenericApproval | undefined, detail: GenericApproval | null | undefined,
                  sent: DecideResult | undefined): string {
    if (outcome === 'failed') return sent && !sent.ok ? sent.message : 'Not sent.';
    if (outcome !== 'elsewhere') return '';
    if (!row) return 'No longer in your approvals (decided or reassigned by someone else).';
    const by = row.status === 'APPROVED' ? detail?.approvedBy?.[0]?.name : detail?.rejectedBy?.[0]?.name;
    const what = { APPROVED: 'Approved', REJECTED: 'Denied', CANCELLED: 'Cancelled', EXPIRED: 'Expired' }[row.status]
      ?? row.status.toLowerCase();
    return by ? `${what} by ${by}.` : `${what} by someone else.`;
  }

  /** The run is over: drop the decided approvals from the list (a refresh brings any follow-up steps). */
  private finish(): void {
    const run = this.run();
    if (!run) return;
    this.patch({ phase: 'done' });
    const decided = new Set(run.items.filter((i) => i.outcome === 'confirmed' || i.outcome === 'elsewhere').map((i) => i.id));
    if (!decided.size) return;
    this.approvals.update((list) => list && list.filter((a) => !decided.has(a.id)));
    this.excluded.update((s) => new Set([...s].filter((id) => !decided.has(id))));
    if (this.openKey() !== null && !this.openGroup()) this.openKey.set(null);
  }

  private stop(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    // Release a confirmation round waiting on the timer; the generation check then ends it.
    this.wake?.();
    this.wake = null;
  }

  private patch(change: Partial<ApprovalRun>): void {
    this.run.update((r) => (r ? { ...r, ...change } : r));
  }

  private patchItem(id: string, change: Partial<RunItem>): void {
    this.run.update((r) => r && { ...r, items: r.items.map((i) => (i.id === id ? { ...i, ...change } : i)) });
  }
}
