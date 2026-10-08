import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { SailpointPluginService } from '@core';

import { App } from './app';
import { BulkConfigService } from './bulk/bulk-config.service';
import { demoScenario } from './demo/demo';
import { providePluginTesting } from './testing/plugin.testing';

describe('App', () => {
  async function render(isOrgAdmin = true) {
    TestBed.configureTestingModule({ imports: [App], providers: providePluginTesting() });
    if (!isOrgAdmin) {
      const plugin = TestBed.inject(SailpointPluginService) as unknown as { user: () => unknown };
      const user = plugin.user() as { capabilities: Record<string, boolean> };
      plugin.user = signal({ ...user, capabilities: { ...user.capabilities, isOrgAdmin: false } });
    }
    await TestBed.inject(BulkConfigService).load();
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  it('shows the title from the runtime config and both tabs', async () => {
    const el = await render();
    expect(el.querySelector('.page__title')?.textContent).toContain('ACME Bulk Access Request');
    const tabs = [...el.querySelectorAll('p-tab')].map((t) => t.textContent?.trim());
    expect(tabs).toEqual(['New request', 'My bulk requests']);
  });

  it('always explains the ORG_ADMIN requirement and points to the Launcher', async () => {
    const el = await render();
    expect(el.textContent).toContain('ORG_ADMIN');
    expect(el.textContent).toContain('ACME Bulk Access Request Launcher');
  });

  it('warns people who are not ORG_ADMIN', async () => {
    const el = await render(false);
    expect(el.textContent).toContain('You need ORG_ADMIN to submit from this page.');
  });

  it('only turns on demo mode at the top level with ?demo=', () => {
    expect(demoScenario({ search: '?demo=review', hash: '' }, true)).toBe('review');
    expect(demoScenario({ search: '?demo=1', hash: '' }, true)).toBe('new');
    expect(demoScenario({ search: '?demo=review', hash: '' }, false)).toBeNull();   // inside ISC's iframe
    expect(demoScenario({ search: '', hash: '' }, true)).toBeNull();
  });
});
