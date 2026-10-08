import { Component, computed, inject, output } from '@angular/core';
import { ButtonModule } from 'primeng/button';
import { StepperModule } from 'primeng/stepper';

import { BulkConfigService } from '../../bulk/bulk-config.service';
import { RequestStore } from '../../bulk/request-store';
import { incIsValid } from '../../bulk/rules';
import { ApproverStepComponent } from './approver-step.component';
import { ItemsStepComponent } from './items-step.component';
import { PeopleStepComponent } from './people-step.component';
import { ReviewStepComponent } from './review-step.component';

/** The New request tab: a four-step guided page (people, items, approver + INC, review). */
@Component({
  selector: 'app-new-request',
  imports: [ApproverStepComponent, ButtonModule, ItemsStepComponent, PeopleStepComponent, ReviewStepComponent, StepperModule],
  templateUrl: './new-request.component.html',
  styleUrl: './steps.scss',
})
export class NewRequestComponent {
  protected readonly store = inject(RequestStore);
  private readonly cfg = inject(BulkConfigService).config;
  readonly showHistory = output<void>();

  /** Can the user move past step n? */
  protected readonly ready = computed<Record<number, boolean>>(() => ({
    1: this.store.people().length > 0,
    2: this.store.items().length > 0,
    3: !!this.store.approver()
      && this.store.approver()!.id !== this.store.requesterId()
      && !this.store.people().some((p) => p.id === this.store.approver()!.id)
      && incIsValid(this.cfg(), this.store.inc())
      && !!this.store.justification().trim()
      && !this.store.accessProblems().length,
  }));

  /** Steps after an unfinished one are locked; once submitted, only the last step is shown. */
  protected locked(step: number): boolean {
    if (this.store.submission()) return step !== 4;
    for (let s = 1; s < step; s++) if (!this.ready()[s]) return true;
    return false;
  }

  protected go(step: number | undefined): void {
    if (step && !this.locked(step)) this.store.step.set(step);
  }
}
