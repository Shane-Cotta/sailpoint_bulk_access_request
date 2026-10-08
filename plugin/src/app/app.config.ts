import {
  ApplicationConfig,
  inject,
  provideAppInitializer,
  provideBrowserGlobalErrorListeners,
  type EnvironmentProviders,
  type Provider,
} from '@angular/core';
import { provideSailPoint } from '@sailpoint/angular-sdk';
import { providePrimeNG } from 'primeng/config';

import { SailpointPluginService } from '@core';
// These will be imported from the SailPoint Design System package when available.
import spdsPrimePreset, { SPDS_DARK_MODE_SELECTOR, SPDS_THEME_PREFIX } from '@core/spds-prime-theme';
import { BulkConfigService } from './bulk/bulk-config.service';

/** `extra` lets main.ts swap in the demo stubs (?demo=…) without touching the real wiring. */
export function appConfig(extra: (Provider | EnvironmentProviders)[] = []): ApplicationConfig {
  return {
    providers: [
      provideBrowserGlobalErrorListeners(),
      // HttpClient + auth interceptor for @sailpoint/angular-sdk (kept from the starter).
      provideSailPoint(),
      providePrimeNG({
        theme: {
          preset: spdsPrimePreset,
          options: { darkModeSelector: SPDS_DARK_MODE_SELECTOR, prefix: SPDS_THEME_PREFIX },
        },
      }),
      ...extra,

      // Resolve the App Shell handshake once, before the app renders, so API calls
      // never race it (see SailpointPluginService).
      provideAppInitializer(async () => {
        try {
          await inject(SailpointPluginService).whenReady();
        } catch (err) {
          console.warn('[plugin] App Shell handshake did not complete during startup.', err);
        }
      }),

      // public/bulk-access.config.json: tenant-specific settings written by plugin/install.py.
      provideAppInitializer(() => inject(BulkConfigService).load()),
    ],
  };
}
