import { inject, Injectable, signal } from '@angular/core';

import { BulkApiService } from './bulk-api.service';
import { BulkConfigService } from './bulk-config.service';
import { describeError } from './errors';
import type { CatalogOption } from './rules';

/** The Request Center catalog, loaded once per page view. */
@Injectable({ providedIn: 'root' })
export class CatalogStore {
  private readonly api = inject(BulkApiService);
  private readonly cfg = inject(BulkConfigService).config;

  readonly options = signal<CatalogOption[] | null>(null);
  readonly loading = signal(false);
  readonly error = signal('');
  private pending: Promise<void> | null = null;

  ensureLoaded(): Promise<void> {
    this.pending ??= this.load();
    return this.pending;
  }

  async reload(): Promise<void> {
    this.pending = this.load();
    return this.pending;
  }

  private async load(): Promise<void> {
    this.loading.set(true);
    this.error.set('');
    try {
      this.options.set(await this.api.catalog(this.cfg()));
    } catch (err) {
      this.error.set(describeError(err));
      this.options.set([]);
    } finally {
      this.loading.set(false);
    }
  }
}
