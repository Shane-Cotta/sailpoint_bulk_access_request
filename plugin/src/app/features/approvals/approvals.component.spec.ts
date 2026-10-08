import { TestBed } from '@angular/core/testing';

import { ApprovalsStore, type ApprovalRun, type RunItem } from '../../bulk/approvals-store';
import { BulkConfigService } from '../../bulk/bulk-config.service';
import type { Outcome } from '../../bulk/approvals';
import { providePluginTesting } from '../../testing/plugin.testing';
import { ApprovalsComponent } from './approvals.component';

describe('ApprovalsComponent run panel', () => {
  async function render(run: Partial<ApprovalRun> & { outcomes: Outcome[] }) {
    TestBed.configureTestingModule({ imports: [ApprovalsComponent], providers: providePluginTesting() });
    await TestBed.inject(BulkConfigService).load();
    const store = TestBed.inject(ApprovalsStore);
    store.approvals.set([]);
    const items: RunItem[] = run.outcomes.map((outcome, i) => ({ id: `a${i}`, person: `P${i}`, item: 'VPN', outcome, message: '' }));
    store.run.set({ action: 'approve', inc: 'INC0048502', comment: '', bulk: false, done: 0, phase: 'done', items, ...run });
    const fixture = TestBed.createComponent(ApprovalsComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    const run$ = (fixture.nativeElement as HTMLElement).querySelector('section.run') as HTMLElement;
    return { title: run$.querySelector('.run__title')?.textContent?.replace(/\s+/g, ' ').trim(), text: run$.textContent ?? '' };
  }

  it('while sending, counts what was sent instead of "0 approved by you"', async () => {
    const { text } = await render({ phase: 'sending', done: 2, outcomes: ['checking', 'checking', 'sending', 'sending'] });
    expect(text).toContain('2 of 4 sent');
    expect(text).toContain('2 sent, not yet confirmed');
    expect(text).not.toContain('approved by you');
  });

  it('heads a fully confirmed run "Approved"', async () => {
    const { title, text } = await render({ done: 2, outcomes: ['confirmed', 'confirmed'] });
    expect(title).toBe('Approved: INC0048502');
    expect(text).toContain('2 approved by you');
    expect(text).not.toContain('not yet confirmed');
  });

  it('heads a run with failures or pending ones "Partly done"', async () => {
    const { title, text } = await render({ done: 3, outcomes: ['confirmed', 'failed', 'pending'] });
    expect(title).toBe('Partly done: INC0048502');
    expect(text).toContain('1 approved by you');
    expect(text).toContain('Retry 2');
  });
});
