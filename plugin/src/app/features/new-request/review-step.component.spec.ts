import { TestBed } from '@angular/core/testing';

import { BulkConfigService } from '../../bulk/bulk-config.service';
import { RequestStore } from '../../bulk/request-store';
import { demoItem, demoPerson } from '../../demo/fixtures';
import { providePluginTesting } from '../../testing/plugin.testing';
import { ReviewStepComponent } from './review-step.component';

describe('ReviewStepComponent', () => {
  it('checks who already has the access each time the step is shown, not when the stepper creates it', async () => {
    TestBed.configureTestingModule({ imports: [ReviewStepComponent], providers: providePluginTesting() });
    await TestBed.inject(BulkConfigService).load();
    const store = TestBed.inject(RequestStore);
    const check = vi.spyOn(store, 'checkExisting');

    // The stepper creates every step when the page opens, with nothing chosen yet.
    const fixture = TestBed.createComponent(ReviewStepComponent);
    fixture.detectChanges();
    expect(check).not.toHaveBeenCalled();

    store.people.set([demoPerson('Alan Bradley')]);
    store.items.set([demoItem('ACME Bulk Test Access')]);
    store.step.set(4);
    fixture.detectChanges();
    await fixture.whenStable();
    expect(check).toHaveBeenCalledTimes(1);
    await vi.waitFor(() => expect(store.existing()).toEqual([
      { personId: demoPerson('Alan Bradley').id, itemId: demoItem('ACME Bulk Test Access').value.id, status: 'ASSIGNED' },
    ]), { timeout: 3000 });

    // Back to the details step and forward again: checked again (the people or items may have changed).
    store.step.set(3);
    fixture.detectChanges();
    store.step.set(4);
    fixture.detectChanges();
    await fixture.whenStable();
    expect(check).toHaveBeenCalledTimes(2);
    store.reset();
  });
});
