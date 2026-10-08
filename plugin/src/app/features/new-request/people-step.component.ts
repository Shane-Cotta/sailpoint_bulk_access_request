import { Component, computed, inject, OnDestroy, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ButtonModule } from 'primeng/button';
import { InputTextModule } from 'primeng/inputtext';
import { MessageModule } from 'primeng/message';
import { ProgressBarModule } from 'primeng/progressbar';
import { TextareaModule } from 'primeng/textarea';

import { BulkApiService, splitPasted, type Person } from '../../bulk/bulk-api.service';
import { BulkConfigService } from '../../bulk/bulk-config.service';
import { describeError } from '../../bulk/errors';
import { RequestStore } from '../../bulk/request-store';

const PAGE_SIZE = 24;
/** Show at most this many not-found entries or ambiguous pickers at once. */
const SHOW_MAX = 10;

/** Step 1: who needs the access. Type-ahead search, or paste a list of IDs, usernames or emails. */
@Component({
  selector: 'app-people-step',
  imports: [ButtonModule, FormsModule, InputTextModule, MessageModule, ProgressBarModule, TextareaModule],
  templateUrl: './people-step.component.html',
  styleUrl: './steps.scss',
})
export class PeopleStepComponent implements OnDestroy {
  protected readonly store = inject(RequestStore);
  protected readonly cfg = inject(BulkConfigService).config;
  private readonly api = inject(BulkApiService);

  protected readonly searching = signal(false);
  protected readonly resolving = signal(false);
  /** Pasted-list lookup progress: entries looked up so far, of how many. */
  protected readonly progress = signal<{ done: number; total: number } | null>(null);
  /** How many of the last pasted list were added (fewer than found when the people limit cut it off). */
  protected readonly pasteAdded = signal(0);
  protected readonly error = signal('');

  /** Chosen people are shown as a filtered, paged list (never 1,000 chips). */
  protected readonly pageSize = PAGE_SIZE;
  protected readonly shownLimit = SHOW_MAX;
  protected readonly chosenMatches = computed(() => {
    const q = this.store.chosenFilter().trim().toLowerCase();
    const all = this.store.people();
    return q ? all.filter((p) => `${p.name} ${p.email ?? ''} ${p.detail ?? ''}`.toLowerCase().includes(q)) : all;
  });
  protected readonly pageCount = computed(() => Math.max(1, Math.ceil(this.chosenMatches().length / PAGE_SIZE)));
  protected readonly page = computed(() => Math.min(this.store.chosenPage(), this.pageCount() - 1));
  protected readonly chosenPage = computed(() => this.chosenMatches().slice(this.page() * PAGE_SIZE, (this.page() + 1) * PAGE_SIZE));
  protected readonly pageEnd = computed(() => Math.min((this.page() + 1) * PAGE_SIZE, this.chosenMatches().length));
  protected readonly partsCount = computed(() => this.store.parts().length);
  protected readonly percent = computed(() => {
    const p = this.progress();
    return p && p.total ? Math.round((100 * p.done) / p.total) : 0;
  });
  private debounce: ReturnType<typeof setTimeout> | null = null;
  private searchSeq = 0;

  protected onQuery(value: string): void {
    this.store.peopleQuery.set(value);
    if (this.debounce) clearTimeout(this.debounce);
    this.debounce = setTimeout(() => void this.search(value), 300);
  }

  private async search(term: string): Promise<void> {
    const seq = ++this.searchSeq;
    if (term.trim().length < 2) {
      this.store.peopleResults.set([]);
      return;
    }
    this.searching.set(true);
    this.error.set('');
    try {
      const found = await this.api.searchPeople(term);
      if (seq === this.searchSeq) this.store.peopleResults.set(found);
    } catch (err) {
      if (seq === this.searchSeq) this.error.set(describeError(err));
    } finally {
      if (seq === this.searchSeq) this.searching.set(false);
    }
  }

  protected isChosen(p: Person): boolean {
    return this.store.people().some((x) => x.id === p.id);
  }

  protected get full(): boolean {
    const max = this.cfg().peopleMax;
    return max !== null && this.store.people().length >= max;
  }

  protected onChosenFilter(value: string): void {
    this.store.chosenFilter.set(value);
    this.store.chosenPage.set(0);
  }

  protected goPage(delta: number): void {
    this.store.chosenPage.set(Math.max(0, Math.min(this.pageCount() - 1, this.page() + delta)));
  }

  protected first<T>(list: T[], n = SHOW_MAX): T[] {
    return list.slice(0, n);
  }

  protected add(p: Person): void {
    this.store.addPeople([p]);
  }

  protected async resolvePaste(): Promise<void> {
    const tokens = splitPasted(this.store.pasteText());
    if (!tokens.length) return;
    this.resolving.set(true);
    this.error.set('');
    this.progress.set({ done: 0, total: tokens.length });
    try {
      const result = await this.api.resolvePeople(tokens, (done, total) => this.progress.set({ done, total }));
      this.pasteAdded.set(this.store.addPeople(result.resolved));
      this.store.resolution.set(result);
      // Leave only what still needs attention in the box.
      this.store.pasteText.set([...result.unresolved, ...result.ambiguous.map((a) => a.token)].join('\n'));
    } catch (err) {
      this.error.set(describeError(err));
    } finally {
      this.resolving.set(false);
      this.progress.set(null);
    }
  }

  /** Pick one of several identities a pasted token matched. */
  protected pickAmbiguous(token: string, p: Person): void {
    this.store.addPeople([p]);
    this.store.resolution.update((r) => r && { ...r, ambiguous: r.ambiguous.filter((a) => a.token !== token) });
    this.store.pasteText.update((text) => splitPasted(text).filter((t) => t !== token).join('\n'));
  }

  ngOnDestroy(): void {
    if (this.debounce) clearTimeout(this.debounce);
  }
}
