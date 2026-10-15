import { computed, inject, Injectable, OnDestroy, signal } from '@angular/core';
import { SailpointPluginService } from '@core';

import {
  BulkApiService, launcherInfoMessage, launcherStopMessage, type ExistingAccess, type LauncherFormData, type Person,
  type Resolution,
} from './bulk-api.service';
import { BulkConfigService } from './bulk-config.service';
import { describeError, describeSubmitError, isLauncherAccessDenied, msgNeedsSubmitAccess } from './errors';
import {
  accessLabel, bulkComment, JUSTIFICATION_MAX, launcherFormAccess, partLabel, PERMANENT, removeDate, removeDuration,
  splitIntoParts, temporaryModes, validateAccess, validateRequest, type AccessChoice, type CatalogOption,
} from './rules';
import { ACCESS_REQUEST_PEOPLE_MAX, MSG_LAUNCHER_ID, type DurationUnit, type SubmitMode } from './runtime-config';

/**
 * One part's state. A part is one direct access request (up to 10 people) or one Launcher run (up to `partSize`).
 * There is no approval of the bulk request itself: once a part is requested, each item is approved the way it's
 * set up in SailPoint.
 */
export type PartState =
  | 'queued'      // not sent yet
  | 'sending'
  | 'requested'   // filed (direct), or the Launcher's workflow accepted it (launcher); dry run: checked
  | 'failed'      // couldn't be sent; can be retried
  | 'stopped';    // the Launcher's workflow refused it (its message says why)

/** The whole submission, summed up over its parts. */
export type SubmissionState = 'sending' | 'requested' | 'partial' | 'error';

export interface PartStatus {
  part: number;
  parts: number;
  /** partLabel(part, parts): "" or " (2/3)" (Launcher runs show it next to the INC). */
  label: string;
  /** Identity IDs in this part. */
  people: string[];
  state: PartState;
  /** Direct: access requests filed, and person/item pairs SailPoint skipped as already pending. */
  created: number;
  existing: number;
  /** Launcher mode: the interactive process the launch started (its blocks carry the form and the workflow's answer). */
  processId: string | null;
  /** Launcher mode: its form was submitted; the workflow's answer is awaited. */
  submitted: boolean;
  message: string;
  /** Nothing more to follow for this part. */
  done: boolean;
}

export interface SubmissionStatus {
  state: SubmissionState;
  submit: SubmitMode;
  dryRun: boolean;
  inc: string;
  accessLabel: string;
  message: string;
  startedAt: number;
  parts: PartStatus[];
}

/** Launcher mode: how often, and for how long, the Launcher's answer is looked for. */
const POLL_SCHEDULE_MS = [2000, 2000, 3000, 3000, 5000];
const POLL_FOR_MS = 2 * 60 * 1000;
/** Direct requests in flight at once. */
const DIRECT_CONCURRENCY = 3;
/** Above this many people the review step skips the "already has it" check (one call per person). */
export const EXISTING_CHECK_MAX = 100;

/** Overall state of a submission from its parts' states. */
export function overallState(parts: Pick<PartStatus, 'state'>[]): SubmissionState {
  const states = parts.map((p) => p.state);
  if (states.some((s) => s === 'queued' || s === 'sending')) return 'sending';
  if (states.every((s) => s === 'requested')) return 'requested';
  if (states.some((s) => s === 'requested')) return 'partial';
  return 'error';
}

/** "2 of 3 sent · 1 not sent": the parts per state, for the submitted view. */
export function partsSummary(parts: Pick<PartStatus, 'state'>[]): string {
  const n = parts.length;
  const count = (...states: PartState[]) => parts.filter((p) => states.includes(p.state)).length;
  const bits: [number, string][] = [
    [count('requested'), 'sent'],
    [count('queued', 'sending'), 'sending'],
    [count('failed'), 'not sent'],
    [count('stopped'), 'stopped'],
  ];
  const shown = bits.filter(([k]) => k > 0);
  if (!shown.length) return '';
  const [[first, word], ...rest] = shown;
  return [`${first} of ${n} ${word}`, ...rest.map(([k, w]) => `${k} ${w}`)].join(' · ');
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;

/** The request being built on the New request tab, and its submission. */
@Injectable({ providedIn: 'root' })
export class RequestStore implements OnDestroy {
  private readonly api = inject(BulkApiService);
  private readonly plugin = inject(SailpointPluginService);
  private readonly bulkConfig = inject(BulkConfigService);

  readonly step = signal(1);
  readonly people = signal<Person[]>([]);
  readonly items = signal<CatalogOption[]>([]);
  readonly inc = signal('');
  readonly justification = signal('');
  readonly existing = signal<ExistingAccess[]>([]);
  /** Why the "already has it" check couldn't run (its first failure), or null. */
  readonly existingError = signal<string | null>(null);
  readonly submission = signal<SubmissionStatus | null>(null);

  // How long the access lasts (CONTRACTS §2). Only offered when the config allows it.
  readonly accessMode = signal<AccessChoice['mode']>('permanent');
  readonly durationN = signal<number | null>(30);
  readonly durationUnit = signal<DurationUnit | null>(null);
  readonly endDate = signal('');

  // Step UI state lives here too, so switching tabs keeps it (and demo scenarios can set it).
  readonly peopleQuery = signal('');
  readonly peopleResults = signal<Person[]>([]);
  readonly pasteText = signal('');
  readonly resolution = signal<Resolution | null>(null);
  readonly chosenFilter = signal('');
  readonly chosenPage = signal(0);
  readonly itemFilter = signal('');

  readonly justificationMax = JUSTIFICATION_MAX;
  readonly requesterId = computed(() => this.plugin.user()?.id ?? '');

  /** The unit shown in the picker: the chosen one, else DAYS if offered, else the first offered. */
  readonly unit = computed<DurationUnit | null>(() => {
    const units = this.bulkConfig.config().temporary.units;
    const chosen = this.durationUnit();
    if (chosen && units.includes(chosen)) return chosen;
    return units.includes('DAYS') ? 'DAYS' : units[0] ?? null;
  });
  readonly accessChoice = computed<AccessChoice>(() => {
    const mode = this.accessMode();
    if (mode === 'duration') return { mode, n: this.durationN(), unit: this.unit() };
    if (mode === 'endDate') return { mode, date: this.endDate() };
    return PERMANENT;
  });
  readonly accessProblems = computed(() => validateAccess(this.bulkConfig.config(), this.accessChoice()));
  readonly accessLabel = computed(() => (this.accessProblems().length ? '' : accessLabel(this.accessChoice())));

  /** How the chosen people are sent: direct requests of up to 10 people, or Launcher runs of up to `partSize`. */
  readonly partSize = computed(() => {
    const cfg = this.bulkConfig.config();
    return cfg.submit === 'launcher' ? cfg.partSize : ACCESS_REQUEST_PEOPLE_MAX;
  });
  readonly parts = computed(() => splitIntoParts(this.people(), this.partSize(), (p) => p.id));
  /** How many access requests SailPoint files: one per person and item. */
  readonly requestCount = computed(() => this.people().length * this.items().length);
  /** The chosen items SailPoint grants without any approval (the review step warns about them). */
  readonly grantedAtOnce = computed(() => {
    const ids = new Set(this.bulkConfig.config().noApproval);
    return this.items().filter((o) => ids.has(o.value.id));
  });

  readonly problems = computed(() => [
    ...validateRequest(this.bulkConfig.config(), {
      people: this.people().map((p) => p.id),
      items: this.items().map((o) => o.value),
      inc: this.inc(),
      justification: this.justification(),
    }),
    ...this.accessProblems(),
  ]);

  private timer: ReturnType<typeof setTimeout> | null = null;
  private sending = false;
  /** Bumped by every new submission and reset, so work that was in flight stops. */
  private generation = 0;

  /** Add people (skipping ones already chosen), up to `peopleMax` when the config sets one. */
  addPeople(found: Person[]): number {
    const max = this.bulkConfig.config().peopleMax;
    const current = this.people();
    const ids = new Set(current.map((p) => p.id));
    const fresh = found.filter((p) => !ids.has(p.id) && !!ids.add(p.id));
    const room = max === null ? fresh.length : Math.max(0, max - current.length);
    const added = fresh.slice(0, room);
    if (added.length) this.people.set([...current, ...added]);
    return added.length;
  }

  removePerson(id: string): void {
    this.people.update((list) => list.filter((p) => p.id !== id));
  }

  clearPeople(): void {
    this.people.set([]);
    this.chosenFilter.set('');
    this.chosenPage.set(0);
  }

  /**
   * Check which chosen people already have the chosen items (shown as warnings on the
   * review step). That is one call per person, so big lists skip it.
   */
  async checkExisting(): Promise<void> {
    this.existing.set([]);
    if (this.people().length > EXISTING_CHECK_MAX) return;
    this.existingError.set(null);
    const failed = (err: unknown) => {
      if (!this.existingError()) this.existingError.set(describeError(err, 'read', 'who already has this access'));
    };
    this.existing.set(await this.api.existingAccess(this.people().map((p) => p.id), this.items().map((o) => o.value),
      undefined, failed));
  }

  /** Send every part (direct requests a few at a time; Launcher runs one after another). */
  async submit(): Promise<void> {
    if (this.problems().length || this.sending) return;
    const state = this.submission()?.state;
    if (state && state !== 'error') return;
    const cfg = this.bulkConfig.config();
    const parts = this.parts();
    this.stop();
    this.generation++;
    this.submission.set({
      state: 'sending', submit: cfg.submit, dryRun: cfg.mode !== 'live', inc: this.inc().trim(),
      accessLabel: accessLabel(this.accessChoice()), message: 'Checking that you may submit…', startedAt: Date.now(),
      parts: parts.map((list, i) => ({
        part: i + 1, parts: parts.length, label: partLabel(i + 1, parts.length), people: list.map((p) => p.id),
        state: 'queued', created: 0, existing: 0, processId: null, submitted: false,
        message: 'Not sent yet.', done: false,
      })),
    });
    await this.send(this.submission()!.parts.map((p) => p.part));
  }

  /** Send again just the parts that couldn't be sent. */
  async retryFailedParts(): Promise<void> {
    const s = this.submission();
    if (!s || this.sending) return;
    const failed = s.parts.filter((p) => p.state === 'failed').map((p) => p.part);
    if (!failed.length) return;
    for (const n of failed) {
      this.patchPart(n, { state: 'queued', message: 'Not sent yet.', processId: null, submitted: false });
    }
    this.patch({ startedAt: Date.now() });
    await this.send(failed);
  }

  /** The parts that couldn't be sent (for the retry banner). */
  readonly sendFailed = computed(() => (this.submission()?.parts ?? []).filter((p) => p.state === 'failed'));

  private async send(partNumbers: number[]): Promise<void> {
    const cfg = this.bulkConfig.config();
    const generation = this.generation;
    this.sending = true;
    this.refresh();
    try {
      const choice = this.accessChoice();
      let fatal: string | null = validateAccess(cfg, choice)[0]
        ?? (cfg.submit === 'launcher' && !cfg.launcherId ? MSG_LAUNCHER_ID : null);
      if (!fatal) fatal = await this.mayNotSubmit();
      if (fatal) {
        for (const n of partNumbers) this.patchPart(n, { state: 'failed', message: fatal });
        return;
      }
      if (cfg.submit === 'launcher') {
        const duration = removeDuration(choice);
        for (const n of partNumbers) {
          if (generation !== this.generation) return;
          fatal = fatal ?? await this.sendViaLauncher(n, duration);
          if (fatal) this.patchPart(n, { state: 'failed', message: fatal });
        }
      } else {
        await this.sendDirect(partNumbers, choice, generation);
      }
    } finally {
      this.sending = false;
      this.refresh();
    }
  }

  /** Why the user may not submit at all (they don't hold the Submit Access role), or null. */
  private async mayNotSubmit(): Promise<string | null> {
    const cfg = this.bulkConfig.config();
    try {
      return (await this.api.hasSubmitAccess(cfg, this.requesterId())) === false ? msgNeedsSubmitAccess(cfg) : null;
    } catch (err) {
      return `Couldn't check that you may submit: ${describeError(err, 'read', 'your access')}`;
    }
  }

  /**
   * Direct: one access request per part (up to 10 people, every item), as the signed-in user, a few at a time.
   * In dry-run mode nothing is sent: the parts are only checked.
   */
  private async sendDirect(partNumbers: number[], choice: AccessChoice, generation: number): Promise<void> {
    const cfg = this.bulkConfig.config();
    const s = this.submission()!;
    const items = this.items().map((o) => o.value);
    const requester = this.plugin.user()?.displayName || this.plugin.user()?.id || '';
    const comment = bulkComment(s.inc, requester, accessLabel(choice), this.justification());
    const until = removeDate(choice);
    let fatal: string | null = null;
    const queue = [...partNumbers];
    const worker = async () => {
      for (let n = queue.shift(); n !== undefined; n = queue.shift()) {
        if (generation !== this.generation) return;
        const part = this.submission()!.parts[n - 1];
        if (fatal) {
          this.patchPart(n, { state: 'failed', message: fatal });
          continue;
        }
        if (cfg.mode !== 'live') {
          this.patchPart(n, { state: 'requested', done: true,
            message: 'Dry-run mode: everything checks out, but nothing was requested.' });
          continue;
        }
        this.patchPart(n, { state: 'sending', message: 'Requesting…' });
        try {
          const result = await this.api.requestAccess(part.people, items, comment, until);
          this.patchPart(n, { state: 'requested', done: true, created: result.created, existing: result.existing,
            message: requestedMessage(result.created, result.existing) });
        } catch (err) {
          const message = describeSubmitError(err, cfg);
          this.patchPart(n, { state: 'failed', message });
          const status = (err as { status?: number })?.status;
          // A permission problem fails every part the same way: don't send the rest.
          if (status === 401 || status === 403) fatal = message;
        }
      }
    };
    await Promise.all(Array.from({ length: Math.min(DIRECT_CONCURRENCY, partNumbers.length) }, worker));
  }

  /**
   * One part through the Launcher, as the signed-in user (CONTRACTS §9): launch it, wait for its form, then
   * submit the form with this part's people and its part label; then look for the workflow's answer.
   * Returns a message when no other part can be sent either (the user may not use the Launcher), else null.
   */
  private async sendViaLauncher(n: number, duration: string): Promise<string | null> {
    const cfg = this.bulkConfig.config();
    const part = this.submission()!.parts[n - 1];
    const formData: LauncherFormData = {
      people: part.people,
      items: this.items().map((o) => o.value),
      inc: this.submission()!.inc,
      justification: this.justification().trim(),
      ...launcherFormAccess(duration),
      partLabel: part.label,
    };
    try {
      this.patchPart(n, { state: 'sending', message: 'Starting the Launcher…' });
      const processId = await this.api.launch(cfg.launcherId ?? '');
      this.patchPart(n, { processId, message: 'Opening its form…' });
      const formId = await this.api.launcherFormInstance(processId);
      this.patchPart(n, { message: 'Submitting the form…' });
      const result = await this.api.submitLauncherForm(formId, formData);
      if (result.state !== 'SUBMITTED' && result.state !== 'COMPLETED') {
        const why = result.errors.map((e) => `${e.key}: ${e.messages.join(' ')}`).join('; ') || `it stayed ${result.state}`;
        this.patchPart(n, { state: 'failed', message: `The Launcher form refused the request (${why}). Nothing was requested.` });
        return null;
      }
      this.patchPart(n, { submitted: true,
        message: 'Submitted; waiting for the Launcher\'s answer…' });
      if (!this.timer) this.schedule(0);
      return null;
    } catch (err) {
      const message = describeSubmitError(err, cfg);
      this.patchPart(n, { state: 'failed', message });
      // Without access to the Launcher every part fails the same way: don't try the others.
      return isLauncherAccessDenied(err) ? message : null;
    }
  }

  /** Start over with an empty form (keeps nothing from the last request). */
  reset(): void {
    this.stop();
    this.generation++;
    this.step.set(1);
    this.people.set([]);
    this.items.set([]);
    this.inc.set('');
    this.justification.set('');
    this.existing.set([]);
    this.existingError.set(null);
    this.submission.set(null);
    this.accessMode.set('permanent');
    this.durationN.set(30);
    this.durationUnit.set(null);
    this.endDate.set('');
    this.peopleQuery.set('');
    this.peopleResults.set([]);
    this.pasteText.set('');
    this.resolution.set(null);
    this.chosenFilter.set('');
    this.chosenPage.set(0);
    this.itemFilter.set('');
  }

  /** Temporary access modes this installation offers (empty = the section is hidden). */
  readonly temporaryModes = computed(() => temporaryModes(this.bulkConfig.config()));

  ngOnDestroy(): void {
    this.stop();
  }

  private stop(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }

  private patch(change: Partial<SubmissionStatus>): void {
    this.submission.update((s) => (s ? { ...s, ...change } : s));
  }

  private patchPart(n: number, change: Partial<PartStatus>): void {
    this.submission.update((s) => s && { ...s, parts: s.parts.map((p) => (p.part === n ? { ...p, ...change } : p)) });
    this.refresh();
  }

  /** Recompute the overall state and message from the parts. */
  private refresh(): void {
    const s = this.submission();
    if (!s) return;
    const state = overallState(s.parts);
    const n = s.parts.length;
    const one = n === 1 ? s.parts[0] : null;
    const sent = s.parts.filter((p) => p.state === 'requested');
    const unsent = s.parts.filter((p) => p.state === 'failed' || p.state === 'stopped');
    const runs = s.submit === 'launcher' ? 'Launcher runs' : 'requests';
    let message: string;
    if (one) {
      message = one.message;
    } else if (state === 'sending') {
      const finished = s.parts.filter((p) => !['queued', 'sending'].includes(p.state)).length;
      message = `Sending ${n} ${runs} (${finished} of ${n} done)…`;
    } else if (state === 'error') {
      message = `Nothing was sent. ${unsent[0]?.message ?? ''}`.trim();
    } else if (s.dryRun) {
      message = 'Dry-run mode: everything checks out, but nothing was requested.';
    } else if (s.submit === 'launcher') {
      message = `Submitted as ${n} Launcher runs with the same INC. The Launcher files the requests, and each item is `
        + 'approved the way it\'s set up in SailPoint; you\'ll get an email for each run.';
    } else {
      message = requestedMessage(sent.reduce((k, p) => k + p.created, 0), sent.reduce((k, p) => k + p.existing, 0));
    }
    if (state === 'partial' && !one) {
      message += ` ${unsent.length} of ${n} ${runs} weren't sent; see below.`;
    }
    this.patch({ state, message });
  }

  private schedule(attempt: number): void {
    this.stop();
    const delay = POLL_SCHEDULE_MS[Math.min(attempt, POLL_SCHEDULE_MS.length - 1)];
    const generation = this.generation;
    this.timer = setTimeout(() => void this.poll(attempt, generation), delay);
  }

  /** Launcher mode: each submitted run's answer (its INFO or ERROR message), read from the process's blocks. */
  private async poll(attempt: number, generation: number): Promise<void> {
    this.timer = null;
    const s = this.submission();
    if (!s || generation !== this.generation) return;
    const waiting = s.parts.filter(awaitingLauncher);
    await Promise.all(waiting.map(async (p) => {
      const blocks = await this.api.launcherBlocks(p.processId!).catch(() => []);
      const stopped = launcherStopMessage(blocks);
      const accepted = launcherInfoMessage(blocks);
      if (stopped) this.patchPart(p.part, { state: 'stopped', done: true, message: `The Launcher stopped it: ${stopped}` });
      else if (accepted) this.patchPart(p.part, { state: 'requested', done: true, message: accepted });
    }));
    const now = this.submission();
    if (!now || generation !== this.generation) return;
    const still = now.parts.filter(awaitingLauncher);
    if (!still.length && !this.sending) return;
    if (Date.now() - now.startedAt > POLL_FOR_MS) {
      for (const p of still) {
        this.patchPart(p.part, { state: 'requested', done: true,
          message: 'Submitted to the Launcher, which hasn\'t answered yet. You\'ll get an email with the result.' });
      }
      return;
    }
    this.schedule(attempt + 1);
  }
}

/** "Filed 12 access requests. Each is approved …" (and how many SailPoint skipped as already pending). */
function requestedMessage(created: number, existing: number): string {
  return `Filed ${plural(created, 'access request')}`
    + (existing ? ` (${existing} already pending, so not requested again)` : '')
    + '. Each is approved the way it\'s set up in SailPoint; follow them on My bulk requests.';
}

/** A Launcher run that was submitted and hasn't answered yet. */
function awaitingLauncher(p: PartStatus): boolean {
  return !p.done && p.submitted && !!p.processId && p.state === 'sending';
}
