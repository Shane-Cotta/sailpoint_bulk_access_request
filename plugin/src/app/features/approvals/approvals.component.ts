import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { SailpointPluginService } from '@core';
import { ButtonModule } from 'primeng/button';
import { DialogModule } from 'primeng/dialog';
import { InputTextModule } from 'primeng/inputtext';
import { MessageModule } from 'primeng/message';
import { ProgressBarModule } from 'primeng/progressbar';
import { SkeletonModule } from 'primeng/skeleton';
import { TableModule } from 'primeng/table';
import { TagModule } from 'primeng/tag';
import { TextareaModule } from 'primeng/textarea';

import { OTHER_KEY, schemeLabel, type ApprovalGroup, type ApprovalRow } from '../../bulk/approvals';
import { ApprovalsStore, type RunItem } from '../../bulk/approvals-store';
import type { DecideAction } from '../../bulk/bulk-api.service';
import { TYPE_LABELS } from '../../bulk/rules';
import type { ItemType } from '../../bulk/runtime-config';

/** Problem rows listed under the results before "Show all". */
const ISSUES_SHOWN = 10;

/**
 * The Approvals tab: the signed-in user's pending access-request approvals, grouped by the
 * bulk request (INC) that created them, with Approve all / Deny all per INC.
 */
@Component({
  selector: 'app-approvals',
  imports: [
    ButtonModule, DialogModule, FormsModule, InputTextModule, MessageModule, ProgressBarModule, SkeletonModule, TableModule,
    TagModule, TextareaModule,
  ],
  templateUrl: './approvals.component.html',
  styleUrl: './approvals.component.scss',
})
export class ApprovalsComponent {
  protected readonly store = inject(ApprovalsStore);
  private readonly plugin = inject(SailpointPluginService);

  /** The action waiting for confirmation in the dialog. */
  protected readonly confirming = signal<DecideAction | null>(null);
  protected readonly showAllIssues = signal(false);
  protected readonly OTHER = OTHER_KEY;
  protected readonly schemeLabel = schemeLabel;

  protected readonly totals = computed(() => {
    const groups = this.store.groups().filter((g) => g.key !== OTHER_KEY);
    return { requests: groups.length, approvals: groups.reduce((n, g) => n + g.approvals, 0) };
  });
  protected readonly percent = computed(() => {
    const run = this.store.run();
    return run && run.items.length ? Math.round((100 * run.done) / run.items.length) : 0;
  });
  /** Approvals of the run that need a look: failed, still pending, decided by someone else. */
  protected readonly issues = computed<RunItem[]>(() => (this.store.run()?.items ?? [])
    .filter((i) => i.outcome === 'failed' || i.outcome === 'pending' || i.outcome === 'elsewhere'));
  protected readonly shownIssues = computed(() => (this.showAllIssues() ? this.issues() : this.issues().slice(0, ISSUES_SHOWN)));
  protected readonly excludedCount = computed(() => (this.store.openGroup()?.approvals ?? 0) - this.store.included().length);

  constructor() {
    let started = false;
    effect(() => {
      if (this.plugin.apiReady() && this.plugin.user() && !started) {
        started = true;
        if (this.store.approvals() === null) void this.store.load();
      }
    });
  }

  protected verb(action: DecideAction): string {
    return action === 'approve' ? 'Approve' : 'Deny';
  }

  protected ask(action: DecideAction): void {
    if (!this.store.problems(action).length) this.confirming.set(action);
  }

  protected confirm(): void {
    const action = this.confirming();
    this.confirming.set(null);
    this.showAllIssues.set(false);
    if (action) void this.store.decide(action);
  }

  protected onSelection(rows: ApprovalRow[] | null): void {
    this.store.setIncluded((rows ?? []).map((r) => r.id));
  }

  protected isIncluded(row: ApprovalRow): boolean {
    return !this.store.excluded().has(row.id);
  }

  protected groupTitle(g: ApprovalGroup): string {
    return g.key === OTHER_KEY ? 'Other approvals' : g.key;
  }

  protected typeLabel(type: string): string {
    return TYPE_LABELS[type as ItemType] ?? type ?? '';
  }

  protected outcomeLabel(i: RunItem): string {
    return { failed: 'Failed', pending: 'Still pending', elsewhere: 'Decided by someone else' }[i.outcome as string] ?? i.outcome;
  }

  protected plural(n: number, one: string, many = `${one}s`): string {
    return `${n.toLocaleString()} ${n === 1 ? one : many}`;
  }

  protected date(iso: string | null | undefined): string {
    if (!iso) return '–';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
  }

  protected day(iso: string | null | undefined): string {
    if (!iso) return '–';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { dateStyle: 'medium' });
  }
}
