import type { EnvironmentProviders, Provider } from '@angular/core';
import { SailpointPluginService } from '@core';

import { BulkConfigService } from '../bulk/bulk-config.service';
import { DemoConfigService, DemoPluginService } from '../demo/demo';

/**
 * Test wiring: the demo stub (fixture answers, no App Shell handshake) in place
 * of SailpointPluginService, and the demo runtime config. Spy on
 * `TestBed.inject(SailpointPluginService).get/post` to check the exact calls.
 */
export function providePluginTesting(): (Provider | EnvironmentProviders)[] {
  return [
    { provide: SailpointPluginService, useClass: DemoPluginService },
    { provide: BulkConfigService, useClass: DemoConfigService },
  ];
}

/** A stub whose API answers come from `routes` (first matching prefix wins). */
export function routedPlugin(routes: Record<string, unknown>, user = { id: 'me', displayName: 'Me', isOrgAdmin: true }) {
  const answer = (path: string) => {
    const key = Object.keys(routes).find((k) => path.startsWith(k));
    if (!key) return Promise.reject(new Error(`no route for ${path}`));
    const value = routes[key];
    return Promise.resolve(typeof value === 'function' ? (value as (p: string) => unknown)(path) : value);
  };
  return {
    user: () => ({ id: user.id, displayName: user.displayName, capabilities: { isOrgAdmin: user.isOrgAdmin } }),
    apiReady: () => true,
    get: vi.fn((path: string) => answer(path)),
    post: vi.fn((path: string, _body: unknown) => answer(path)),
  };
}
