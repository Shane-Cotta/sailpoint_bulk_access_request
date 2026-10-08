import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { SailpointPluginService } from '@core';
import { ButtonModule } from 'primeng/button';
import { InputTextModule } from 'primeng/inputtext';
import { MessageModule } from 'primeng/message';
import { SkeletonModule } from 'primeng/skeleton';
import { TagModule } from 'primeng/tag';

import { BulkApiService } from '../../bulk/bulk-api.service';
import { BulkConfigService } from '../../bulk/bulk-config.service';
import { describeError } from '../../bulk/errors';
import { groupByInc, requestStateLabel, statusLabel, type BulkGroup, type RequestRow } from '../../bulk/my-requests';

/** Access-request rows shown per group before "Show all" (a 600-person request has 600+). */
const ROWS_SHOWN = 10;

/** The My bulk requests tab: the signed-in user's bulk requests, grouped by INC. */
@Component({
  selector: 'app-my-requests',
  imports: [ButtonModule, FormsModule, InputTextModule, MessageModule, SkeletonModule, TagModule],
  templateUrl: './my-requests.component.html',
  styleUrl: './my-requests.component.scss',
})
export class MyRequestsComponent {
  private readonly api = inject(BulkApiService);
  private readonly plugin = inject(SailpointPluginService);
  private readonly cfg = inject(BulkConfigService).config;

  protected readonly groups = signal<BulkGroup[] | null>(null);
  protected readonly loading = signal(false);
  protected readonly errors = signal<string[]>([]);
  protected readonly filter = signal('');
  protected readonly open = signal<Set<string>>(new Set());
  protected readonly loadedAt = signal<Date | null>(null);
  private readonly expanded = signal<Set<string>>(new Set());

  protected readonly shown = computed(() => {
    const q = this.filter().trim().toUpperCase();
    return (this.groups() ?? []).filter((g) => !q || g.inc.toUpperCase().includes(q));
  });
  protected readonly counts = computed(() => {
    const all = this.groups() ?? [];
    return {
      total: all.length,
      pending: all.filter((g) => g.status === 'PENDING').length,
      approved: all.filter((g) => g.status === 'APPROVED' || g.status === 'REQUESTED' || g.status === 'MIXED').length,
      denied: all.filter((g) => g.status === 'REJECTED').length,
    };
  });

  protected readonly statusLabel = statusLabel;
  protected readonly requestStateLabel = requestStateLabel;

  constructor() {
    let started = false;
    effect(() => {
      if (this.plugin.apiReady() && this.plugin.user() && !started) {
        started = true;
        void this.load();
      }
    });
  }

  async load(): Promise<void> {
    const me = this.plugin.user()?.id;
    if (!me) return;
    this.loading.set(true);
    this.errors.set([]);
    const [approvals, requests] = await Promise.allSettled([this.api.myBulkApprovals(me), this.api.myAccessRequests(me)]);
    const errors: string[] = [];
    if (approvals.status === 'rejected') errors.push(`Approvals: ${describeError(approvals.reason)}`);
    if (requests.status === 'rejected') errors.push(`Access requests: ${describeError(requests.reason)}`);
    const groups = groupByInc(
      this.cfg(),
      me,
      approvals.status === 'fulfilled' ? approvals.value : [],
      requests.status === 'fulfilled' ? requests.value : [],
    );
    this.groups.set(groups);
    this.errors.set(errors);
    this.loadedAt.set(new Date());
    // Open the newest request so the page shows detail straight away.
    if (groups.length && !this.open().size) this.open.set(new Set([groups[0].inc]));
    this.loading.set(false);
  }

  protected hasParts(g: BulkGroup): boolean {
    return g.submissions.some((s) => s.part !== null);
  }

  /** The latest submission's parts in order (1/3, 2/3, 3/3), then earlier attempts, newest first. */
  protected approvalRows(g: BulkGroup): BulkGroup['submissions'] {
    return [...g.latest, ...g.submissions.filter((s) => !g.latest.includes(s))];
  }

  protected shownRequests(g: BulkGroup): RequestRow[] {
    return this.expanded().has(g.inc) ? g.requests : g.requests.slice(0, ROWS_SHOWN);
  }

  protected showAll(inc: string): void {
    this.expanded.update((s) => new Set(s).add(inc));
  }

  /** A removeDate as a day ("7 Nov 2026"). */
  protected day(iso: string): string {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { dateStyle: 'medium' });
  }

  protected toggle(inc: string): void {
    this.open.update((s) => {
      const next = new Set(s);
      if (next.has(inc)) next.delete(inc);
      else next.add(inc);
      return next;
    });
  }

  protected date(iso: string | null | undefined): string {
    if (!iso) return '–';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
  }
}
