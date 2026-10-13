import { Component, computed, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { InputTextModule } from 'primeng/inputtext';
import { MessageModule } from 'primeng/message';
import { SelectModule } from 'primeng/select';
import { SelectButtonModule } from 'primeng/selectbutton';
import { TextareaModule } from 'primeng/textarea';

import { BulkConfigService } from '../../bulk/bulk-config.service';
import { RequestStore } from '../../bulk/request-store';
import { endDateHours, incIsValid, localDate, removeDate, unitWord, type AccessChoice } from '../../bulk/rules';
import type { DurationUnit } from '../../bulk/runtime-config';

const MODE_LABELS: Record<AccessChoice['mode'], string> = {
  permanent: 'Permanent', duration: 'For a duration', endDate: 'Until a date',
};

const when = (iso: string) => new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });

/** Step 3: the ServiceNow INC number, why, and for how long. Each item is then approved as it's set up in SailPoint. */
@Component({
  selector: 'app-details-step',
  imports: [FormsModule, InputTextModule, MessageModule, SelectButtonModule, SelectModule, TextareaModule],
  templateUrl: './details-step.component.html',
  styleUrl: './steps.scss',
})
export class DetailsStepComponent {
  protected readonly store = inject(RequestStore);
  protected readonly cfg = inject(BulkConfigService).config;

  /** Live INC check: shown as soon as something is typed. */
  protected readonly incError = computed(() => {
    const value = this.store.inc();
    return value.trim() && !incIsValid(this.cfg(), value) ? this.cfg().incMessage : '';
  });
  protected readonly incOk = computed(() => incIsValid(this.cfg(), this.store.inc()));

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
  /**
   * When SailPoint removes the access. A direct request carries a fixed removal date, counted from now; through the
   * Launcher the duration counts from when the access is granted (an end date goes as hours).
   */
  protected readonly removalPreview = computed(() => {
    const choice = this.store.accessChoice();
    if (choice.mode === 'permanent' || this.store.accessProblems().length) return '';
    const now = new Date();
    if (this.cfg().submit === 'direct') {
      return choice.mode === 'endDate'
        ? `SailPoint removes the access at the end of ${choice.date}, however long the approvals take.`
        : `SailPoint removes the access on ${when(removeDate(choice, now)!)}: ${choice.n} `
          + `${unitWord(choice.unit!, choice.n!)} from now, however long the approvals take.`;
    }
    if (choice.mode === 'endDate') {
      const hours = endDateHours(choice.date, now);
      return `Sent as ${hours} hours: SailPoint removes the access ${hours} hours after it is granted, `
        + 'which is the end of that day if it is granted now. A later approval moves the end later.';
    }
    return `SailPoint removes the access ${choice.n} ${unitWord(choice.unit!, choice.n!)} after it is granted `
      + `(around ${when(removeDate(choice, now)!)} if it is granted now).`;
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
}
