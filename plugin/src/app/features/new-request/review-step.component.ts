import { Component, computed, inject, OnInit, output, signal } from '@angular/core';
import { SailpointPluginService } from '@core';
import { ButtonModule } from 'primeng/button';
import { MessageModule } from 'primeng/message';
import { ProgressSpinnerModule } from 'primeng/progressspinner';
import { TagModule } from 'primeng/tag';

import { BulkConfigService } from '../../bulk/bulk-config.service';
import { EXISTING_CHECK_MAX, partsSummary, RequestStore, type PartState } from '../../bulk/request-store';
import { APPROVAL_NAME_PREFIX } from '../../bulk/rules';

/** Step 4: check everything, submit, then follow the approval. */
@Component({
  selector: 'app-review-step',
  imports: [ButtonModule, MessageModule, ProgressSpinnerModule, TagModule],
  templateUrl: './review-step.component.html',
  styleUrl: './steps.scss',
})
export class ReviewStepComponent implements OnInit {
  protected readonly store = inject(RequestStore);
  protected readonly cfg = inject(BulkConfigService).config;
  protected readonly configError = inject(BulkConfigService).error;
  private readonly plugin = inject(SailpointPluginService);

  /** Ask the host page to show the My bulk requests tab. */
  readonly showHistory = output<void>();

  protected readonly checking = signal(false);
  protected readonly isAdmin = computed(() => this.plugin.user()?.capabilities?.isOrgAdmin ?? false);
  /** Through the Launcher anyone may try (SailPoint checks the Launcher access); the test endpoint is ORG_ADMIN only. */
  protected readonly viaLauncher = computed(() => this.cfg().submit === 'launcher');
  protected readonly mayStart = computed(() => this.viaLauncher() || this.isAdmin());
  protected readonly canSubmit = computed(() => !this.store.problems().length && this.mayStart() && !this.configError()
    && (!this.store.submission() || this.store.submission()!.state === 'error'));
  protected readonly canRetry = computed(() => this.mayStart() && !this.configError() && this.store.startFailed().length > 0
    && !this.store.submission()?.parts.some((p) => p.state === 'queued' || p.state === 'starting'));

  /** People shown by name on the review card; the rest are counted. */
  protected readonly previewMax = 5;
  protected readonly existingCheckMax = EXISTING_CHECK_MAX;
  protected readonly approvalPrefix = APPROVAL_NAME_PREFIX;
  protected readonly summary = computed(() => partsSummary(this.store.submission()?.parts ?? []));
  /** "parts 2 and 3" / "part 2" for the retry banner. */
  protected readonly failedNames = computed(() => {
    const nums = this.store.startFailed().map((p) => p.part);
    if (nums.length === 1) return `part ${nums[0]}`;
    return `parts ${nums.slice(0, -1).join(', ')} and ${nums.at(-1)}`;
  });

  /** "Alan Bradley already has ACME Bulk Test Access", grouped per item. */
  protected readonly warnings = computed(() => {
    const names = new Map(this.store.people().map((p) => [p.id, p.name]));
    const byItem = new Map<string, { item: string; assigned: string[]; pending: string[] }>();
    for (const e of this.store.existing()) {
      const item = this.store.items().find((o) => o.value.id === e.itemId)?.label ?? e.itemId;
      const row = byItem.get(item) ?? { item, assigned: [], pending: [] };
      (e.status === 'ASSIGNED' ? row.assigned : row.pending).push(names.get(e.personId) ?? e.personId);
      byItem.set(item, row);
    }
    return [...byItem.values()];
  });

  ngOnInit(): void {
    if (!this.store.submission()) void this.check();
  }

  private async check(): Promise<void> {
    this.checking.set(true);
    try {
      await this.store.checkExisting();
    } finally {
      this.checking.set(false);
    }
  }

  protected requestCount(): number {
    return this.store.people().length;
  }

  protected partState(state: PartState): { label: string; severity: 'success' | 'warn' | 'danger' | 'info' | 'secondary' } {
    switch (state) {
      case 'queued': return { label: 'Queued', severity: 'secondary' };
      case 'starting': return { label: 'Starting', severity: 'info' };
      case 'start-failed': return { label: 'Not started', severity: 'danger' };
      case 'waiting': return { label: 'Waiting', severity: 'warn' };
      case 'still-waiting': return { label: 'Still waiting', severity: 'warn' };
      case 'approved': return { label: 'Approved', severity: 'success' };
      case 'denied': return { label: 'Not approved', severity: 'danger' };
      case 'failed': return { label: 'Stopped', severity: 'danger' };
    }
  }
}
