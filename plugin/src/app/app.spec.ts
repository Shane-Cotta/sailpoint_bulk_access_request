import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { SailpointPluginService } from '@core';

import { App } from './app';
import { BulkConfigService } from './bulk/bulk-config.service';
import { NavService } from './bulk/nav';
import { demoScenario } from './demo/scenario';
import { providePluginTesting } from './testing/plugin.testing';

describe('App', () => {
  async function render(isOrgAdmin = true, approvalsEnabled = true) {
    TestBed.configureTestingModule({ imports: [App], providers: providePluginTesting() });
    await TestBed.compileComponents();   // the tabs are @defer blocks (lazy chunks)
    if (!isOrgAdmin) {
      const plugin = TestBed.inject(SailpointPluginService) as unknown as { user: () => unknown };
      const user = plugin.user() as { capabilities: Record<string, boolean> };
      plugin.user = signal({ ...user, capabilities: { ...user.capabilities, isOrgAdmin: false } });
    }
    const config = TestBed.inject(BulkConfigService);
    await config.load();
    if (!approvalsEnabled) config.config.update((c) => ({ ...c, approvals: { ...c.approvals, enabled: false } }));
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    await fixture.whenStable();
    return { el: fixture.nativeElement as HTMLElement, fixture };
  }

  const tabs = (el: HTMLElement) => [...el.querySelectorAll('p-tab')].map((t) => t.textContent?.trim());

  it('shows the title from the runtime config and all three tabs', async () => {
    const { el } = await render();
    expect(el.querySelector('.page__title')?.textContent).toContain('ACME Bulk Access Request');
    expect(tabs(el)).toEqual(['New request', 'My bulk requests', 'Approvals']);
    expect(TestBed.inject(NavService).tab()).toBe('new');               // admins start on New request
  });

  it('hides the Approvals tab when the config turns it off (or the runtime config predates it)', async () => {
    const { el } = await render(false, false);
    expect(tabs(el)).toEqual(['New request', 'My bulk requests']);
    expect(TestBed.inject(NavService).tab()).toBe('new');
  });

  it('explains the ORG_ADMIN requirement on the New request tab and points to the Launcher', async () => {
    const { el } = await render();
    const panel = el.querySelector('p-tabpanel[value="new"]') ?? el;
    expect(panel.textContent).toContain('ORG_ADMIN');
    expect(panel.textContent).toContain('ACME Bulk Access Request Launcher');
  });

  it('opens the Approvals tab first for people who are not ORG_ADMIN, without the submit banner', async () => {
    const { el, fixture } = await render(false);
    const nav = TestBed.inject(NavService);
    expect(nav.tab()).toBe('approvals');
    expect(el.querySelector('app-approvals')).not.toBeNull();
    await vi.waitFor(() => {
      fixture.detectChanges();
      expect(el.querySelectorAll('app-approvals .group').length).toBe(3);   // two INCs and Other
    }, { timeout: 3000 });
    expect(el.querySelector('app-approvals')?.textContent).toContain('INC0048502');
    expect(el.querySelector('app-approvals')?.textContent).not.toContain('You need ORG_ADMIN to submit');

    nav.tab.set('new');
    fixture.detectChanges();
    await fixture.whenStable();
    expect(el.textContent).toContain('You need ORG_ADMIN to submit from this page.');
  });

  it('only turns on demo mode at the top level with ?demo=', () => {
    expect(demoScenario({ search: '?demo=review', hash: '' }, true)).toBe('review');
    expect(demoScenario({ search: '?demo=approvals-partial', hash: '' }, true)).toBe('approvals-partial');
    expect(demoScenario({ search: '?demo=1', hash: '' }, true)).toBe('new');
    expect(demoScenario({ search: '?demo=review', hash: '' }, false)).toBeNull();   // inside ISC's iframe
    expect(demoScenario({ search: '', hash: '' }, true)).toBeNull();
  });
});
