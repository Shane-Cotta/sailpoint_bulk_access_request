import { Component, computed, inject, OnDestroy, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ButtonModule } from 'primeng/button';
import { InputTextModule } from 'primeng/inputtext';
import { MessageModule } from 'primeng/message';
import { SelectModule } from 'primeng/select';
import { SelectButtonModule } from 'primeng/selectbutton';
import { TextareaModule } from 'primeng/textarea';

import { BulkApiService, type Person } from '../../bulk/bulk-api.service';
import { BulkConfigService } from '../../bulk/bulk-config.service';
import { describeError } from '../../bulk/errors';
import { RequestStore } from '../../bulk/request-store';
import { endDateHours, incIsValid, localDate, removeDuration, unitWord, type AccessChoice } from '../../bulk/rules';
import type { DurationUnit } from '../../bulk/runtime-config';

const MODE_LABELS: Record<AccessChoice['mode'], string> = {
  permanent: 'Permanent', duration: 'For a duration', endDate: 'Until a date',
};

/** Step 3: one approver for the whole request, the ServiceNow INC number, why, and for how long. */
@Component({
  selector: 'app-approver-step',
  imports: [ButtonModule, FormsModule, InputTextModule, MessageModule, SelectButtonModule, SelectModule, TextareaModule],
  templateUrl: './approver-step.component.html',
  styleUrl: './steps.scss',
})
export class ApproverStepComponent implements OnDestroy {
  protected readonly store = inject(RequestStore);
  protected readonly cfg = inject(BulkConfigService).config;
  private readonly api = inject(BulkApiService);

  protected readonly searching = signal(false);
  protected readonly error = signal('');
  private debounce: ReturnType<typeof setTimeout> | null = null;
  private seq = 0;

  /** Live INC check: shown as soon as something is typed. */
  protected readonly incError = computed(() => {
    const value = this.store.inc();
    return value.trim() && !incIsValid(this.cfg(), value) ? this.cfg().incMessage : '';
  });
  protected readonly incOk = computed(() => incIsValid(this.cfg(), this.store.inc()));
  protected readonly isSelf = computed(() => !!this.store.approver() && this.store.approver()!.id === this.store.requesterId());
  protected readonly isOnRequest = computed(() => {
    const a = this.store.approver();
    return !!a && this.store.people().some((p) => p.id === a.id);
  });

  // ── How long the access lasts ──
  protected readonly modeOptions = computed(() => ['permanent', ...this.store.temporaryModes()]
    .map((value) => ({ value, label: MODE_LABELS[value as AccessChoice['mode']] })));
  protected readonly unitOptions = computed(() => this.cfg().temporary.units
    .map((value) => ({ value, label: unitWord(value, this.store.durationN() ?? 2) })));
  /** The date picker starts tomorrow (the end date must be after today). */
  protected readonly minDate = computed(() => {
    const d = new Date();
    d.setDate(d.getDate() + 1);
    return localDate(d);
  });
  protected readonly maxDate = computed(() => {
    const max = this.cfg().temporary.maxDays;
    if (max === null) return null;
    const d = new Date();
    d.setDate(d.getDate() + max - 1);   // the whole last day must fit within maxDays
    return localDate(d);
  });
  /** "Removed automatically around Thu, 7 Nov 2026, 14:00": a preview for the requester. */
  protected readonly removalPreview = computed(() => {
    const choice = this.store.accessChoice();
    if (choice.mode === 'permanent' || this.store.accessProblems().length) return '';
    const now = new Date();
    if (choice.mode === 'endDate') {
      // Manage Access counts a duration from when access is granted, so the page sends hours.
      const hours = endDateHours(choice.date, now);
      return `Sent as ${hours} hours: SailPoint removes the access ${hours} hours after it is granted, `
        + 'which is the end of that day if it is approved now. A later approval moves the end later.';
    }
    const when = addDuration(now, removeDuration(choice, now)).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
    return `SailPoint removes the access ${choice.n} ${unitWord(choice.unit!, choice.n!)} after it is granted `
      + `(around ${when} if it is approved now).`;
  });

  protected setMode(value: AccessChoice['mode'] | null): void {
    if (value) this.store.accessMode.set(value);
  }

  protected setDuration(value: string | number | null): void {
    const n = value === null || value === '' ? null : Number(value);
    this.store.durationN.set(n === null || Number.isNaN(n) ? null : n);
  }

  protected setUnit(value: DurationUnit | null): void {
    this.store.durationUnit.set(value);
  }

  protected onQuery(value: string): void {
    this.store.approverQuery.set(value);
    if (this.debounce) clearTimeout(this.debounce);
    this.debounce = setTimeout(() => void this.search(value), 300);
  }

  private async search(term: string): Promise<void> {
    const seq = ++this.seq;
    if (term.trim().length < 2) {
      this.store.approverResults.set([]);
      return;
    }
    this.searching.set(true);
    this.error.set('');
    try {
      const found = await this.api.searchPeople(term, 10);
      if (seq === this.seq) this.store.approverResults.set(found);
    } catch (err) {
      if (seq === this.seq) this.error.set(describeError(err));
    } finally {
      if (seq === this.seq) this.searching.set(false);
    }
  }

  protected isMe(p: Person): boolean {
    return p.id === this.store.requesterId();
  }

  /** People on the request can't approve it (they'd be approving their own access). */
  protected onRequest(p: Person): boolean {
    return this.store.people().some((x) => x.id === p.id);
  }

  protected choose(p: Person): void {
    this.store.approver.set(p);
    this.store.approverResults.set([]);
    this.store.approverQuery.set('');
  }

  ngOnDestroy(): void {
    if (this.debounce) clearTimeout(this.debounce);
  }
}

/** now + a removeDuration string ("2h", "30d", "1w", "3M"): only for the preview. */
function addDuration(now: Date, duration: string): Date {
  const m = /^(\d+)([hdwM])$/.exec(duration);
  const d = new Date(now);
  if (!m) return d;
  const n = Number(m[1]);
  if (m[2] === 'h') d.setHours(d.getHours() + n);
  else if (m[2] === 'd') d.setDate(d.getDate() + n);
  else if (m[2] === 'w') d.setDate(d.getDate() + 7 * n);
  else d.setMonth(d.getMonth() + n);
  return d;
}
