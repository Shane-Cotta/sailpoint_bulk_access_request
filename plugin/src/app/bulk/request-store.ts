import { computed, inject, Injectable, OnDestroy, signal } from '@angular/core';
import { SailpointPluginService } from '@core';

import {
  assignedApproverNames, BulkApiService, launcherStopMessage, type BulkInput, type ExistingAccess, type GenericApproval,
  type LauncherFormData, type Person, type Resolution,
} from './bulk-api.service';
import { BulkConfigService } from './bulk-config.service';
import { approvalName, executionIdOf } from './my-requests';
import { describeError, describeLauncherError, isLauncherAccessDenied } from './errors';
import {
  accessLabel, APPROVAL_NAME_PREFIX, clip, justificationMax, launcherFormAccess, partLabel, PERMANENT, removeDuration,
  splitIntoParts, temporaryModes, validateAccess, validateRequest, type AccessChoice, type CatalogOption,
} from './rules';
import type { DurationUnit } from './runtime-config';

/** One part's state: one workflow run and its approval. */
export type PartState =
  | 'queued'          // not started yet (parts start one after another)
  | 'starting'
  | 'start-failed'    // the test endpoint refused it; can be retried
  | 'waiting'         // started; approval being created or waiting for the approver
  | 'approved'
  | 'denied'          // rejected, expired or cancelled
  | 'failed'          // the workflow stopped before a decision
  | 'still-waiting';  // we stopped polling

/** The whole submission, summed up over its parts. */
export type SubmissionState =
  | 'starting' | 'waiting' | 'approved' | 'denied' | 'mixed' | 'failed' | 'still-waiting' | 'error';

export interface PartStatus {
  part: number;
  parts: number;
  /** partLabel(part, parts): "" or " (2/3)". */
  label: string;
  /** Identity IDs in this part. */
  people: string[];
  state: PartState;
  executionId: string | null;
  /** Launcher mode: the interactive process the launch started (its blocks carry the form and any stop message). */
  processId: string | null;
  approvalId: string | null;
  approver: string;
  decidedBy: string | null;
  message: string;
  /** Polling for this part is over (decided and the workflow finished, or gave up). */
  done: boolean;
}

export interface SubmissionStatus {
  state: SubmissionState;
  inc: string;
  approver: string;
  accessLabel: string;
  message: string;
  startedAt: number;
  parts: PartStatus[];
}

/** Poll quickly while the approval is being created, then back off. */
const POLL_SCHEDULE_MS = [2000, 2000, 3000, 3000, 5000, 5000, 10000];
const POLL_FOR_MS = 10 * 60 * 1000;
/** Above this many people the review step skips the "already has it" check (one call per person). */
export const EXISTING_CHECK_MAX = 100;

const DECIDED: Partial<Record<string, PartState>> = {
  APPROVED: 'approved', REJECTED: 'denied', EXPIRED: 'denied', CANCELLED: 'denied',
};

/** Overall state of a submission from its parts' states. */
export function overallState(parts: Pick<PartStatus, 'state'>[]): SubmissionState {
  const states = parts.map((p) => p.state);
  if (states.some((s) => s === 'queued' || s === 'starting')) return 'starting';
  const started = states.filter((s) => s !== 'start-failed');
  if (!started.length) return 'error';
  if (started.some((s) => s === 'waiting')) return 'waiting';
  if (started.some((s) => s === 'still-waiting')) return 'still-waiting';
  if (started.every((s) => s === 'approved')) return 'approved';
  if (started.every((s) => s === 'denied')) return 'denied';
  if (started.every((s) => s === 'failed')) return 'failed';
  return 'mixed';
}

/** "2 of 3 approved · 1 waiting": the parts per state, for the submitted view. */
export function partsSummary(parts: Pick<PartStatus, 'state'>[]): string {
  const n = parts.length;
  const count = (...states: PartState[]) => parts.filter((p) => states.includes(p.state)).length;
  const bits: [number, string][] = [
    [count('approved'), 'approved'],
    [count('waiting', 'still-waiting'), 'waiting'],
    [count('denied'), 'not approved'],
    [count('failed'), 'stopped'],
    [count('queued', 'starting'), 'starting'],
    [count('start-failed'), 'not started'],
  ];
  const shown = bits.filter(([k]) => k > 0);
  if (!shown.length) return '';
  const [[first, word], ...rest] = shown;
  return [`${first} of ${n} ${word}`, ...rest.map(([k, w]) => `${k} ${w}`)].join(' · ');
}

/** The request being built on the New request tab, and its submission. */
@Injectable({ providedIn: 'root' })
export class RequestStore implements OnDestroy {
  private readonly api = inject(BulkApiService);
  private readonly plugin = inject(SailpointPluginService);
  private readonly bulkConfig = inject(BulkConfigService);

  readonly step = signal(1);
  readonly people = signal<Person[]>([]);
  readonly items = signal<CatalogOption[]>([]);
  readonly approver = signal<Person | null>(null);
  readonly inc = signal('');
  readonly justification = signal('');
  readonly existing = signal<ExistingAccess[]>([]);
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
  readonly approverQuery = signal('');
  readonly approverResults = signal<Person[]>([]);
  readonly itemFilter = signal('');

  readonly justificationMax = computed(() => justificationMax(this.inc(), this.bulkConfig.config().incExample));
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

  /** The chosen people cut into parts of `partSize`, one approval each. */
  readonly parts = computed(() => splitIntoParts(this.people(), this.bulkConfig.config().partSize, (p) => p.id));

  readonly problems = computed(() => {
    const problems = validateRequest(this.bulkConfig.config(), {
      requesterId: this.requesterId(),
      approverId: this.approver()?.id,
      people: this.people().map((p) => p.id),
      items: this.items().map((o) => o.value),
      inc: this.inc(),
    });
    problems.push(...this.accessProblems());
    const max = this.justificationMax();
    if (!this.justification().trim()) problems.push('Enter a business justification.');
    else if (this.justification().trim().length > max) problems.push(`Keep the justification to ${max} characters.`);
    return problems;
  });

  private timer: ReturnType<typeof setTimeout> | null = null;
  private workflowId: string | null = null;
  private starting = false;
  /** Bumped by every new submission and reset, so a poll that was in flight stops. */
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
    this.existing.set(await this.api.existingAccess(this.people().map((p) => p.id), this.items().map((o) => o.value)));
  }

  /** Start one workflow run per part, one after another, then follow each part's approval. */
  async submit(): Promise<void> {
    if (this.problems().length || this.starting) return;
    const state = this.submission()?.state;
    if (state && state !== 'error') return;
    const approver = this.approver()!;
    const inc = this.inc().trim();
    const parts = this.parts();
    this.stop();
    this.generation++;
    this.submission.set({
      state: 'starting', inc, approver: approver.name, accessLabel: accessLabel(this.accessChoice()),
      message: parts.length > 1 ? `Starting ${parts.length} approvals…` : 'Starting the workflow…', startedAt: Date.now(),
      parts: parts.map((list, i) => ({
        part: i + 1, parts: parts.length, label: partLabel(i + 1, parts.length), people: list.map((p) => p.id),
        state: 'queued', executionId: null, processId: null, approvalId: null, approver: approver.name, decidedBy: null,
        message: 'Not started yet.', done: false,
      })),
    });
    await this.start(this.submission()!.parts.map((p) => p.part));
  }

  /** Start again just the parts that didn't start. */
  async retryFailedParts(): Promise<void> {
    const s = this.submission();
    if (!s || this.starting) return;
    const failed = s.parts.filter((p) => p.state === 'start-failed').map((p) => p.part);
    if (!failed.length) return;
    for (const n of failed) this.patchPart(n, { state: 'queued', message: 'Not started yet.', executionId: null, processId: null });
    this.patch({ startedAt: Date.now() });
    await this.start(failed);
  }

  /** The parts that failed to start (for the retry banner). */
  readonly startFailed = computed(() => (this.submission()?.parts ?? []).filter((p) => p.state === 'start-failed'));

  private async start(partNumbers: number[]): Promise<void> {
    const s = this.submission()!;
    this.starting = true;
    this.refresh();
    try {
      // The same access choice for every part; an end date is converted to hours now.
      const cfg = this.bulkConfig.config();
      const viaLauncher = cfg.submit === 'launcher';
      const choice = this.accessChoice();
      const problems = validateAccess(cfg, choice);
      let fatal: string | null = problems[0] ?? null;
      const duration = fatal ? '' : removeDuration(choice);
      const label = accessLabel(choice);
      if (!fatal && !viaLauncher) {
        try {
          this.workflowId ??= await this.api.workflowId(cfg);
        } catch (err) {
          fatal = describeError(err);
        }
      }
      for (const n of partNumbers) {
        const part = this.submission()!.parts[n - 1];
        if (fatal) {
          this.patchPart(n, { state: 'start-failed', message: fatal });
          continue;
        }
        if (viaLauncher) {
          fatal = await this.startViaLauncher(part, s.inc, duration);
          continue;
        }
        this.patchPart(n, { state: 'starting', message: 'Starting…' });
        const input: BulkInput = {
          people: part.people,
          items: this.items().map((o) => o.value),
          approverId: this.approver()!.id,
          requesterId: this.requesterId(),
          inc: s.inc,
          justification: clip(this.justification(), this.justificationMax()),
          part: part.part,
          parts: part.parts,
          partLabel: part.label,
          removeDuration: duration,
          accessLabel: label,
        };
        try {
          const executionId = await this.api.submit(this.workflowId!, input);
          this.patchPart(n, { state: 'waiting', executionId, message: 'Creating the approval…', done: false });
          if (!this.timer) this.schedule(0);
        } catch (err) {
          const status = (err as { status?: number })?.status;
          this.patchPart(n, { state: 'start-failed', message: describeError(err) });
          // A permission problem fails every part the same way: don't hammer the endpoint.
          if (status === 401 || status === 403) fatal = describeError(err);
        }
      }
    } finally {
      this.starting = false;
      this.refresh();
    }
  }

  /**
   * One part through the Launcher, as the signed-in user (CONTRACTS §9): launch it, wait for its form, then
   * submit the form with this part's people and the hidden part label. Returns a message when no other part
   * can start either (the user may not use the Launcher), else null.
   */
  private async startViaLauncher(part: PartStatus, inc: string, duration: string): Promise<string | null> {
    const cfg = this.bulkConfig.config();
    const n = part.part;
    const formData: LauncherFormData = {
      people: part.people,
      items: this.items().map((o) => o.value),
      approver: [this.approver()!.id],
      inc,
      justification: clip(this.justification(), this.justificationMax()),
      ...launcherFormAccess(duration),
      partLabel: part.label,
    };
    try {
      this.patchPart(n, { state: 'starting', message: 'Starting the Launcher…' });
      const processId = await this.api.launch(cfg.launcherId ?? '');
      this.patchPart(n, { processId, message: 'Opening its form…' });
      const formId = await this.api.launcherFormInstance(processId);
      this.patchPart(n, { message: 'Submitting the form…' });
      const result = await this.api.submitLauncherForm(formId, formData);
      if (result.state !== 'SUBMITTED' && result.state !== 'COMPLETED') {
        const why = result.errors.map((e) => `${e.key}: ${e.messages.join(' ')}`).join('; ') || `it stayed ${result.state}`;
        this.patchPart(n, { state: 'start-failed',
          message: `The Launcher form refused the request (${why}). Nothing was sent for approval.` });
        return null;
      }
      this.patchPart(n, { state: 'waiting', executionId: result.executionId, message: 'Creating the approval…', done: false });
      if (!this.timer) this.schedule(0);
      return null;
    } catch (err) {
      const message = describeLauncherError(err, cfg.launcherAccessName);
      this.patchPart(n, { state: 'start-failed', message });
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
    this.approver.set(null);
    this.inc.set('');
    this.justification.set('');
    this.existing.set([]);
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
    this.approverQuery.set('');
    this.approverResults.set([]);
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
    const one = s.parts.length === 1 ? s.parts[0] : null;
    const failed = s.parts.filter((p) => p.state === 'start-failed');
    let message: string;
    if (one) {
      message = one.message;
    } else if (state === 'starting') {
      const started = s.parts.filter((p) => !['queued', 'starting'].includes(p.state)).length;
      message = `Starting ${s.parts.length} approvals, one after another (${started} of ${s.parts.length} done)…`;
    } else if (state === 'error') {
      message = `None of the ${s.parts.length} approvals started. ${failed[0]?.message ?? ''}`.trim();
    } else {
      const mode = this.bulkConfig.config().mode;
      message = {
        waiting: `Sent as ${s.parts.length} approvals with the same INC. Waiting for ${s.approver} to decide each one.`,
        'still-waiting': `Still waiting for ${s.approver}. Follow it on the My bulk requests tab.`,
        approved: `${s.approver} approved all ${s.parts.length} approvals. ` + (mode === 'live'
          ? 'Access is being requested for every person; follow it on My bulk requests.'
          : 'Dry-run mode: nothing was requested.'),
        denied: `None of the ${s.parts.length} approvals was approved. Nothing was requested.`,
        failed: 'The workflow stopped before the approvals were decided. An administrator can see why in Admin > Workflows > executions.',
        mixed: 'Some approvals were decided differently; see each part below. Access is only requested for approved parts.',
        starting: '',
        error: '',
      }[state];
    }
    if (failed.length && state !== 'error' && state !== 'starting' && !one) {
      message += ` ${failed.length} of ${s.parts.length} didn't start.`;
    }
    this.patch({ state, message });
  }

  private schedule(attempt: number): void {
    this.stop();
    const delay = POLL_SCHEDULE_MS[Math.min(attempt, POLL_SCHEDULE_MS.length - 1)];
    const generation = this.generation;
    this.timer = setTimeout(() => void this.poll(attempt, generation), delay);
  }

  /** One round: each live part's execution status, plus the approval it created (found by execution ID). */
  private async poll(attempt: number, generation: number): Promise<void> {
    this.timer = null;
    const s = this.submission();
    if (!s || generation !== this.generation) return;
    const live = s.parts.filter(tracked);
    if (!live.length && !this.starting) return;
    if (live.length) {
      try {
        const approvals = await this.api.approvals(this.requesterId());
        if (generation !== this.generation) return;
        await Promise.all(live.map((p) => this.pollPart(p, approvals)));
      } catch (err) {
        // A failed poll is not fatal; try again on the next round.
        for (const p of live) this.patchPart(p.part, { message: `Still checking… (${describeError(err)})` });
      }
    }
    const now = this.submission();
    if (!now || generation !== this.generation) return;
    if (Date.now() - now.startedAt > POLL_FOR_MS) {
      for (const p of now.parts.filter(tracked)) {
        this.patchPart(p.part, {
          done: true,
          ...(p.state === 'waiting' ? { state: 'still-waiting' as const,
            message: `Still waiting for ${p.approver}. Follow it on the My bulk requests tab.` } : {}),
        });
      }
      return;
    }
    if (this.starting || now.parts.some(tracked)) this.schedule(attempt + 1);
  }

  private async pollPart(p: PartStatus, approvals: GenericApproval[]): Promise<void> {
    try {
      if (this.bulkConfig.config().submit === 'launcher') await this.pollLauncherPart(p, approvals);
      else await this.pollWorkflowPart(p, approvals);
    } catch (err) {
      this.patchPart(p.part, { message: `Still checking… (${describeError(err)})` });
    }
  }

  /** The approval is decided: say by whom (the list leaves that out; the detail call has it). */
  private async settle(p: PartStatus, approval: GenericApproval, approver: string, decided: PartState,
                       done: boolean): Promise<void> {
    Object.assign(approval, await this.api.approval(approval.id).catch(() => ({})));
    const by = decided === 'approved' ? approval.approvedBy?.[0]?.name : approval.rejectedBy?.[0]?.name;
    const mode = this.bulkConfig.config().mode;
    const message = decided === 'denied'
      ? `${approval.status === 'REJECTED' ? `Denied by ${by ?? approver}` : `Approval ${approval.status.toLowerCase()}`}. Nothing was requested.`
      : `Approved by ${by ?? approver}. ` + (mode === 'live'
        ? 'Access is being requested for every person; follow it on My bulk requests.'
        : 'Dry-run mode: nothing was requested.');
    this.patchPart(p.part, { state: decided, decidedBy: by ?? null, message, done });
  }

  /**
   * Launcher mode: only what the signed-in user may read. The approval (filed in their name) is found by the
   * run's ID or by its name; until it exists, the process's blocks tell whether the workflow stopped with a
   * message (an INC or approver it refused). Workflow executions are not read: they need admin rights.
   */
  private async pollLauncherPart(p: PartStatus, approvals: GenericApproval[]): Promise<void> {
    const s = this.submission()!;
    const name = `${APPROVAL_NAME_PREFIX}${s.inc}${p.label}`;
    const approval = (p.executionId ? approvals.find((a) => executionIdOf(a) === p.executionId) : undefined)
      ?? approvals.find((a) => approvalName(a) === name && Date.parse(a.createdDate ?? '') >= s.startedAt - MATCH_SLACK_MS);
    if (approval) {
      const approver = assignedApproverNames(approval)[0] || p.approver;
      this.patchPart(p.part, { approvalId: approval.id, approver });
      const decided = DECIDED[approval.status];
      if (decided) await this.settle(p, approval, approver, decided, true);
      else this.patchPart(p.part, { state: 'waiting', message: `Waiting for ${approver} to approve or deny.` });
      return;
    }
    const stopped = p.processId ? launcherStopMessage(await this.api.launcherBlocks(p.processId).catch(() => [])) : null;
    if (stopped) {
      this.patchPart(p.part, { state: 'failed', done: true,
        message: `The workflow stopped before the approval: ${stopped}` });
    } else {
      this.patchPart(p.part, { state: 'waiting', message: 'Creating the approval…' });
    }
  }

  /** Test-endpoint mode: the run's execution status, plus the approval it created (found by execution ID). */
  private async pollWorkflowPart(p: PartStatus, approvals: GenericApproval[]): Promise<void> {
    const execution = await this.api.execution(p.executionId!);
    const approval = approvals.find((a) => executionIdOf(a) === p.executionId);
    const approver = (approval && assignedApproverNames(approval)[0]) || p.approver;
    if (approval) this.patchPart(p.part, { approvalId: approval.id, approver });

    const decided = approval && DECIDED[approval.status];
    if (approval && decided) {
      // Keep polling until the workflow itself finishes (its emails go out last).
      await this.settle(p, approval, approver, decided, execution.status !== 'Running');
    } else if (execution.status === 'Failed' || execution.status === 'Canceled') {
      this.patchPart(p.part, { state: 'failed', done: true, message: `The workflow ${execution.status.toLowerCase()} before an approval `
        + 'was decided. An administrator can see why in Admin > Workflows > executions.' });
    } else if (execution.status === 'Completed' && !approval) {
      this.patchPart(p.part, { state: 'failed', done: true, message: 'The workflow finished without creating an approval '
        + '(the INC number or approver was rejected). Check your email for details.' });
    } else {
      this.patchPart(p.part, { state: 'waiting',
        message: approval ? `Waiting for ${approver} to approve or deny.` : 'Creating the approval…' });
    }
  }
}

/** A part being followed: started (a run or a Launcher process) and not finished. */
function tracked(p: PartStatus): boolean {
  return !p.done && !!(p.executionId || p.processId) && !['queued', 'starting', 'start-failed'].includes(p.state);
}

/** Launcher mode, approval found by name: how much earlier than the submission it may have been created (clock skew). */
const MATCH_SLACK_MS = 5 * 60 * 1000;
