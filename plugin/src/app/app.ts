import { Component, computed, inject } from '@angular/core';
import { SailpointPluginService } from '@core';
import { MessageModule } from 'primeng/message';
import { TabsModule } from 'primeng/tabs';
import { TagModule } from 'primeng/tag';

import { BulkConfigService } from './bulk/bulk-config.service';
import { NavService } from './bulk/nav';
import { MyRequestsComponent } from './features/my-requests/my-requests.component';
import { NewRequestComponent } from './features/new-request/new-request.component';

@Component({
  selector: 'app-root',
  imports: [MessageModule, MyRequestsComponent, NewRequestComponent, TabsModule, TagModule],
  templateUrl: './app.html',
  styleUrl: './app.scss',
})
export class App {
  protected readonly plugin = inject(SailpointPluginService);
  private readonly bulkConfig = inject(BulkConfigService);
  protected readonly cfg = this.bulkConfig.config;
  protected readonly configError = this.bulkConfig.error;

  protected readonly tab = inject(NavService).tab;
  protected readonly title = computed(() => `${this.cfg().prefix} Bulk Access Request`.trim());
  protected readonly isAdmin = computed(() => this.plugin.user()?.capabilities?.isOrgAdmin ?? false);

  protected onTab(value: string | number | undefined): void {
    this.tab.set(value === 'mine' ? 'mine' : 'new');
  }
}
