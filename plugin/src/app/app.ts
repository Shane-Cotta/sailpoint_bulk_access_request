import { Component, computed, inject } from '@angular/core';
import { SailpointPluginService } from '@core';
import { MessageModule } from 'primeng/message';
import { TabsModule } from 'primeng/tabs';
import { TagModule } from 'primeng/tag';

import { BulkConfigService } from './bulk/bulk-config.service';
import { NavService, TAB_IDS, type TabId } from './bulk/nav';
import { ApprovalsComponent } from './features/approvals/approvals.component';
import { MyRequestsComponent } from './features/my-requests/my-requests.component';
import { NewRequestComponent } from './features/new-request/new-request.component';

@Component({
  selector: 'app-root',
  imports: [ApprovalsComponent, MessageModule, MyRequestsComponent, NewRequestComponent, TabsModule, TagModule],
  templateUrl: './app.html',
  styleUrl: './app.scss',
})
export class App {
  protected readonly plugin = inject(SailpointPluginService);
  private readonly bulkConfig = inject(BulkConfigService);
  private readonly nav = inject(NavService);
  protected readonly cfg = this.bulkConfig.config;
  protected readonly configError = this.bulkConfig.error;

  protected readonly tab = this.nav.tab;
  protected readonly title = computed(() => `${this.cfg().prefix} Bulk Access Request`.trim());
  protected readonly approvalsOn = computed(() => this.cfg().approvals.enabled);

  protected onTab(value: string | number | undefined): void {
    const tab = TAB_IDS.includes(value as TabId) ? (value as TabId) : 'new';
    this.nav.chosen = true;
    this.tab.set(tab === 'approvals' && !this.approvalsOn() ? 'new' : tab);
  }
}
