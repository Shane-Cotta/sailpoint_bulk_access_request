import { Injectable, signal } from '@angular/core';

import { DEFAULT_CONFIG, loadRuntimeConfig, type RuntimeConfig } from './runtime-config';

/** Holds the runtime config (public/bulk-access.config.json), loaded once by the app initializer. */
@Injectable({ providedIn: 'root' })
export class BulkConfigService {
  readonly config = signal<RuntimeConfig>(DEFAULT_CONFIG);
  /** Set when the file is missing or invalid; the page shows it and blocks submitting. */
  readonly error = signal('');

  async load(fetchFn: typeof fetch = fetch.bind(globalThis)): Promise<void> {
    try {
      this.config.set(await loadRuntimeConfig(fetchFn));
    } catch (err) {
      this.error.set(err instanceof Error ? err.message : String(err));
    }
  }
}
