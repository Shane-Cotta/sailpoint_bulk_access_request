import { Component, computed, effect, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { SailpointPluginService } from '@core';
import { ButtonModule } from 'primeng/button';
import { ChipModule } from 'primeng/chip';
import { InputTextModule } from 'primeng/inputtext';
import { MessageModule } from 'primeng/message';
import { SkeletonModule } from 'primeng/skeleton';
import { TableModule } from 'primeng/table';
import { TagModule } from 'primeng/tag';

import { BulkConfigService } from '../../bulk/bulk-config.service';
import { CatalogStore } from '../../bulk/catalog-store';
import { RequestStore } from '../../bulk/request-store';
import { TYPE_LABELS, type CatalogOption } from '../../bulk/rules';
import type { ItemType } from '../../bulk/runtime-config';

/** Step 2: what to request. The Request Center catalog, filtered by the config. */
@Component({
  selector: 'app-items-step',
  imports: [ButtonModule, ChipModule, FormsModule, InputTextModule, MessageModule, SkeletonModule, TableModule, TagModule],
  templateUrl: './items-step.component.html',
  styleUrl: './steps.scss',
})
export class ItemsStepComponent {
  protected readonly store = inject(RequestStore);
  protected readonly catalog = inject(CatalogStore);
  protected readonly cfg = inject(BulkConfigService).config;
  private readonly plugin = inject(SailpointPluginService);

  protected readonly overLimit = signal(false);
  protected readonly filtered = computed(() => {
    const q = this.store.itemFilter().trim().toLowerCase();
    const all = this.catalog.options() ?? [];
    return q ? all.filter((o) => `${o.label} ${o.subLabel} ${o.description}`.toLowerCase().includes(q)) : all;
  });

  constructor() {
    effect(() => {
      if (this.plugin.apiReady()) void this.catalog.ensureLoaded();
    });
  }

  protected onSelection(selection: CatalogOption[]): void {
    const max = this.cfg().itemsMax;
    this.overLimit.set(selection.length > max);
    this.store.items.set(selection.slice(0, max));
  }

  protected remove(o: CatalogOption): void {
    this.store.items.update((list) => list.filter((x) => x.value.id !== o.value.id));
  }

  protected isChosen(o: CatalogOption): boolean {
    return this.store.items().some((x) => x.value.id === o.value.id);
  }

  protected typeLabel(t: ItemType): string {
    return TYPE_LABELS[t] ?? t;
  }

  protected typeSeverity(t: ItemType): 'info' | 'success' | 'secondary' {
    return t === 'ROLE' ? 'success' : t === 'ACCESS_PROFILE' ? 'info' : 'secondary';
  }
}
