import { Injectable, signal } from '@angular/core';

export type TabId = 'new' | 'mine' | 'approvals';

export const TAB_IDS: readonly TabId[] = ['new', 'mine', 'approvals'];

/** Which tab is showing (a service, so steps and demo scenarios can switch it). */
@Injectable({ providedIn: 'root' })
export class NavService {
  readonly tab = signal<TabId>('new');
  /** Set once a tab was chosen on purpose (a click or a demo scenario), so the default doesn't override it. */
  chosen = false;
}
