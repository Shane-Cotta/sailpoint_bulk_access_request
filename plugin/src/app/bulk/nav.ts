import { Injectable, signal } from '@angular/core';

export type TabId = 'new' | 'mine';

/** Which tab is showing (a service, so steps and demo scenarios can switch it). */
@Injectable({ providedIn: 'root' })
export class NavService {
  readonly tab = signal<TabId>('new');
}
