/**
 * ?demo=<scenario>: run the page standalone (no ISC, no tenant) with fixture data.
 * Used for the README screenshots and for UI work. A stub replaces
 * SailpointPluginService and answers the same API paths from fixtures.ts.
 *
 * Scenarios: new (empty), people (600 chosen: no limit), items, details, inc-error (a mistyped INC),
 * temporary (details with temporary access), review (one item has no approval: the warning), submitted (filed
 * directly, the default), parts-review and parts-submitted (600 people through the Launcher: 3 runs of 250,
 * temporary), launcher-denied (a user without the Launcher Access profile submits), history,
 * approvals (a non-admin item approver: 300 + 40 approvals from two bulk requests, and 3 others),
 * approvals-partial (the same, with throttling, failures and colleagues deciding first).
 */
import { computed, inject, Injectable, provideAppInitializer, signal, type EnvironmentProviders, type Provider } from '@angular/core';
import type { PluginContext } from '@sailpoint/ui-plugin-sdk';
import { SailpointPluginService } from '@core';

import { ApprovalsStore } from '../bulk/approvals-store';
import type { AccessRequestStatus, GenericApproval, LauncherBlock } from '../bulk/bulk-api.service';
import { BulkConfigService } from '../bulk/bulk-config.service';
import { NavService } from '../bulk/nav';
import { RequestStore } from '../bulk/request-store';
import {
  crowdPeople, DEMO_CATALOG, DEMO_COLLEAGUE, DEMO_CONFIG, DEMO_CROWD, DEMO_HELD, DEMO_IDENTITIES, DEMO_LAUNCHER_ACCESS,
  DEMO_ME, DEMO_PENDING, DEMO_PENDING_INCS, DEMO_REQUESTS, demoItem, demoItemApproval, demoPerson, demoRequest,
} from './fixtures';
import type { DemoScenario } from './scenario';


/**
 * Trouble the demo's approvals API makes, by an approval's position in DEMO_PENDING (1-based):
 * every `throttle`-th answers its first call with 429, every `flaky`-th with 503 (both pass on retry),
 * every `fail`-th always refuses (400), every `elsewhere`-th is decided by a colleague first, and
 * every `stuck`-th accepts the call but stays PENDING.
 */
export interface DemoApprovalFaults {
  throttle: number;
  flaky: number;
  fail: number;
  elsewhere: number;
  stuck: number;
}
export const PARTIAL_FAULTS: DemoApprovalFaults = { throttle: 25, flaky: 70, fail: 60, elsewhere: 45, stuck: 97 };

const apiError = (status: number, text: string) =>
  Object.assign(new Error(text), { status, statusText: text, body: { messages: [{ text }] } });

const delay = <T>(value: T, ms = 120) => new Promise<T>((resolve) => setTimeout(() => resolve(structuredClone(value)), ms));

function filterValues(path: string): string[] {
  const filters = new URLSearchParams(path.split('?')[1] ?? '').get('filters') ?? '';
  return [...filters.matchAll(/"((?:[^"\\]|\\.)*)"/g)].map((m) => m[1].toLowerCase());
}

/** Stands in for SailpointPluginService: same surface, fixture answers. */
@Injectable()
export class DemoPluginService {
  private readonly _context = signal<PluginContext | null>({
    tenant: { id: 'demo', scriptName: 'demo', org: 'demo-tenant', name: 'Demo', pod: 'demo', region: 'demo',
      products: [], apiUrl: { idn: 'https://demo.api.example.com' } },
    user: { id: DEMO_ME.id, displayName: DEMO_ME.name, email: DEMO_ME.email, capabilities: {
      isOrgAdmin: true, isHelpdesk: false, isDashboard: false, isCertAdmin: false, isReportAdmin: false,
      isSourceAdmin: false, isSourceSubadmin: false, isRoleAdmin: false, isRoleSubadmin: false,
      isCloudGovAdmin: false, isCloudGovUser: false, isSaasManagementAdmin: false, isSaasManagementReader: false } },
    page: { route: 'https://demo.example.com/ui/plugin/demo', subPath: '' },
    slot: {},
    pluginConfiguration: { pluginId: 'demo' },
  });
  readonly context = this._context.asReadonly();
  readonly status = signal<'ready'>('ready').asReadonly();
  readonly user = computed(() => this._context()?.user ?? null);
  readonly apiReady = computed(() => true);

  /** Access requests the demo user filed (directly, or through a Launcher run), newest first. */
  private filed: AccessRequestStatus[] = [];
  private runs = 0;
  /** Item approvals waiting for the demo user (the Approvals tab), decided in place. */
  private pending: GenericApproval[] = structuredClone(DEMO_PENDING);
  private position = new Map(DEMO_PENDING.map((a, i) => [a.id, i + 1]));
  /** IDs whose one-off 429/503 has been served. */
  private hiccups = new Set<string>();
  faults: DemoApprovalFaults | null = null;
  /** Launcher mode: each launch's interactive process (run k), the form it assigns, and the workflow's answer. */
  private processes = new Map<string, { k: number; polls: number; answer?: { title: string; message: string } }>();
  private forms = new Map<string, { k: number; state: string; formData?: Record<string, unknown> }>();
  /** false: play a user without the Launcher Access profile (the page and the Launcher turn them away). */
  launcherAccess = true;

  whenReady(): Promise<PluginContext> {
    return Promise.resolve(this._context()!);
  }

  /** Play a user without ORG_ADMIN (an ordinary item approver). */
  setOrgAdmin(isOrgAdmin: boolean): void {
    this._context.update((c) => c && { ...c, user: { ...c.user, capabilities: { ...c.user.capabilities, isOrgAdmin } } });
  }

  /** The list leaves out who decided; the single GET has it. */
  private static listRow({ approvedBy: _b, rejectedBy: _r, ...row }: GenericApproval): GenericApproval {
    return row;
  }

  /** Approve or reject one pending approval as the demo user, or fail the way `faults` says. */
  private decide(id: string, action: 'approve' | 'reject', comment?: string): Promise<GenericApproval> {
    const row = this.pending.find((a) => a.id === id);
    if (!row) return Promise.reject(apiError(404, 'Approval not found.'));
    const n = this.position.get(id) ?? 0;
    const every = (k: number | undefined) => !!k && n % k === 0;
    const f = this.faults;
    if (f && every(f.throttle) && !this.hiccups.has(`429:${id}`)) {
      this.hiccups.add(`429:${id}`);
      return Promise.reject(apiError(429, 'Too Many Requests'));
    }
    if (f && every(f.flaky) && !this.hiccups.has(`503:${id}`)) {
      this.hiccups.add(`503:${id}`);
      return Promise.reject(apiError(503, 'Service Unavailable'));
    }
    if (f && every(f.fail)) return Promise.reject(apiError(400, 'This approval can\'t be decided right now: its access item is being updated.'));
    if (f && every(f.elsewhere) && row.status === 'PENDING') {
      Object.assign(row, { status: 'APPROVED', approvedBy: [{ identityID: DEMO_COLLEAGUE.id, name: DEMO_COLLEAGUE.name }] });
    }
    if (row.status !== 'PENDING') return Promise.reject(apiError(400, 'The approval is not pending.'));
    if (f && every(f.stuck)) return delay(row, 80);
    const me = { identityID: DEMO_ME.id, name: DEMO_ME.name };
    Object.assign(row, action === 'approve'
      ? { status: 'APPROVED', approvedBy: [me] }
      : { status: 'REJECTED', rejectedBy: [me] });
    if (comment) row.comments = [...(row.comments ?? []), { comment, author: { name: DEMO_ME.name } }];
    return delay(row, 80);
  }

  get<T>(path: string): Promise<T> {
    const [route] = path.split('?');
    const params = new URLSearchParams(path.split('?')[1] ?? '');
    // Verified live: identities, accounts and someone else's access requests are refused to non-admins.
    const admin = !!this.user()?.capabilities.isOrgAdmin;
    if (!admin && (route.startsWith('/identities/v1') || route === '/accounts/v1')) return Promise.reject(apiError(403, 'Forbidden'));
    if (!admin && route === '/access-request-status/v1' && params.get('requested-for')
        && params.get('requested-for') !== DEMO_ME.id) {
      return Promise.reject(apiError(400, '"request-by/requested-for" must be the current user.'));
    }
    if (route === '/requestable-objects/v1') {
      // Like the real API: access profiles and roles only, never entitlements.
      const types = params.getAll('types');
      const rows = DEMO_CATALOG.filter((c) => c.row['type'] !== 'ENTITLEMENT' && (!types.length || types.includes(String(c.row['type']))));
      // With identity-id (always sent: a non-admin gets 403 without it), every row carries that person's status;
      // `filters=id in (…)` narrows it to the chosen items (the "already has it" check).
      const who = params.get('identity-id');
      if (!who && !this.user()?.capabilities.isOrgAdmin) return Promise.reject(apiError(403, 'Forbidden'));
      if (who === DEMO_ME.id && filterValues(path).includes(DEMO_LAUNCHER_ACCESS)) {
        // The submit gate: does the user hold the Launcher Access profile?
        return delay([{ id: DEMO_LAUNCHER_ACCESS, type: 'ACCESS_PROFILE', name: DEMO_CONFIG.launcherAccessName,
          requestStatus: this.launcherAccess ? 'ASSIGNED' : 'AVAILABLE' }] as T);
      }
      const held = (who && DEMO_HELD[who]) || {};
      const wanted = (params.get('filters') ?? '').startsWith('id in') ? new Set(filterValues(path)) : null;
      return delay(rows.filter((c) => !wanted || wanted.has(String(c.row['id']).toLowerCase()))
        .map((c) => ({ ...c.row, requestStatus: held[String(c.row['id'])] ?? 'AVAILABLE' })) as T, who && wanted ? 400 : 120);
    }
    if (route === '/public-identities/v1') {
      // Open to every user. `sw` matches the start of a name, username or email (any word); otherwise exact values.
      const values = filterValues(path);
      const prefix = (params.get('filters') ?? '').includes(' sw ');
      const keys = (d: (typeof DEMO_IDENTITIES)[number]) => [d.id, d.name, d.email, d.displayName].map((v) => v.toLowerCase());
      const hit = (d: (typeof DEMO_IDENTITIES)[number]) => (prefix
        ? keys(d).some((k) => k.split(/[\s.@]/).some((w) => values.some((v) => w.startsWith(v))) || values.some((v) => k.startsWith(v)))
        : keys(d).some((k) => values.includes(k)));
      return delay([...DEMO_IDENTITIES, ...DEMO_CROWD].filter(hit).slice(0, Number(params.get('limit') ?? 250))
        .map((d) => ({ id: d.id, name: d.displayName, alias: d.name, email: d.email, status: 'active', identityState: 'ACTIVE',
          attributes: [{ key: 'department', name: 'Department', value: d.attributes.department }] })) as T);
    }
    if (route === '/entitlements/v1') {
      // Entitlement rows carry their source and no `type`.
      return delay(DEMO_CATALOG.filter((c) => c.row['type'] === 'ENTITLEMENT')
        .map(({ row: { type: _t, requestStatus: _s, ...row }, source }) => ({ ...row, requestable: true, source: { name: source } })) as T);
    }
    if (route.startsWith('/identities/v1/')) {
      const id = route.split('/').pop();
      const doc = [...DEMO_IDENTITIES, ...DEMO_CROWD].find((d) => d.id === id);
      return doc ? delay({ id: doc.id, name: doc.name, alias: doc.name, emailAddress: doc.email,
        attributes: { displayName: doc.displayName, department: doc.attributes.department } } as T)
        : Promise.reject(Object.assign(new Error('Not found'), { status: 404 }));
    }
    if (route === '/identities/v1') {
      const wanted = new Set(filterValues(path));
      return delay([...DEMO_IDENTITIES, ...DEMO_CROWD]
        .filter((d) => wanted.has(d.id) || wanted.has(d.name.toLowerCase()) || wanted.has(d.email.toLowerCase()))
        .map((d) => ({ id: d.id, name: d.name, alias: d.name, emailAddress: d.email,
          attributes: { displayName: d.displayName, department: d.attributes.department } })) as T);
    }
    if (route === '/accounts/v1') return delay([] as T);
    const blocks = /^\/beta\/interactive-processes\/([^/]+)\/blocks$/.exec(route);
    if (blocks) {
      // Like the real Launcher: the form shows up a moment after the launch.
      const process = this.processes.get(decodeURIComponent(blocks[1]));
      if (!process) return Promise.reject(apiError(404, 'Not found'));
      const now = new Date().toISOString();
      const items: LauncherBlock[] = process.polls++ < 1 ? [] : [{ id: `b-${process.k}`, type: 'FORM', created: now,
        config: { formInstanceId: `f-${process.k}` }, data: { title: DEMO_CONFIG.launcherName } }];
      // Once the workflow has taken the form, its INFO message ("Requested: …").
      if (process.answer) items.push({ id: `m-${process.k}`, type: 'MESSAGE', created: now,
        config: { category: 'INFO' }, data: process.answer });
      return delay({ items } as T);
    }
    if (route.startsWith('/form-instances/v1/')) {
      const id = decodeURIComponent(route.split('/').pop()!);
      const form = this.forms.get(id);
      if (!form) return Promise.reject(apiError(404, 'Not found'));
      const errors = form.state === 'IN_PROGRESS'
        ? [{ key: 'inc', messages: [{ text: DEMO_CONFIG.incMessage }] }] : [];
      return delay({ id, state: form.state, formData: form.formData ?? null, formErrors: errors } as T);
    }
    if (route === '/generic-approvals/v1' && params.get('mine') === 'true') {
      // The Approvals tab: the user's pending item approvals, oldest first, a page at a time.
      const offset = Number(params.get('offset') ?? 0);
      const page = this.pending.filter((a) => a.status === 'PENDING')
        .sort((a, b) => (a.createdDate ?? '').localeCompare(b.createdDate ?? ''))
        .slice(offset, offset + Number(params.get('limit') ?? 250));
      return delay(page.map(DemoPluginService.listRow) as T, 250);
    }
    if (route === '/generic-approvals/v1' && (params.get('filters') ?? '').startsWith('approvalId in')) {
      const wanted = new Set(filterValues(path));
      return delay(this.pending.filter((a) => wanted.has(a.id)).map(DemoPluginService.listRow) as T);
    }
    if (route.startsWith('/generic-approvals/v1/')) {
      const hit = this.pending.find((a) => a.id === route.split('/').pop());
      return hit ? delay(hit as T) : Promise.reject(Object.assign(new Error('Not found'), { status: 404 }));
    }
    if (route === '/access-request-status/v1' && params.get('requested-for')) {
      // Open entitlement requests of one person (the existing-access check).
      const held = DEMO_HELD[params.get('requested-for')!] ?? {};
      return delay(DEMO_CATALOG.filter((c) => c.row['type'] === 'ENTITLEMENT' && held[String(c.row['id'])] === 'PENDING')
        .map((c) => ({ id: c.row['id'], name: c.row['name'], type: 'ENTITLEMENT', state: 'EXECUTING', requestType: 'GRANT_ACCESS' })) as T, 300);
    }
    if (route === '/access-request-status/v1') {
      // requested-by=<me>: what the user filed, newest first (a Launcher run's requests are filed by its owner).
      const by = params.get('requested-by');
      const offset = Number(params.get('offset') ?? 0);
      return delay([...this.filed, ...DEMO_REQUESTS].filter((r) => !by || r.requester?.id === by)
        .slice(offset, offset + Number(params.get('limit') ?? 250)) as T);
    }
    return Promise.reject(new Error(`demo: no fixture for GET ${path}`));
  }

  post<T>(path: string, data: unknown): Promise<T> {
    const body = data as Record<string, unknown>;
    if (path.startsWith('/search/v1') && !this.user()?.capabilities.isOrgAdmin) return Promise.reject(apiError(403, 'Forbidden'));
    if (path.startsWith('/search/v1')) {
      const indices = (body['indices'] as string[]) ?? [];
      const q = String((body['query'] as { query: string }).query);
      if (indices.includes('identities') && q.startsWith('id:(')) {
        // Held entitlements of the chosen people (the existing-access check).
        return delay(Object.entries(DEMO_HELD).filter(([id]) => q.includes(id)).map(([id, held]) => ({
          id,
          access: DEMO_CATALOG.filter((c) => c.row['type'] === 'ENTITLEMENT' && held[String(c.row['id'])] === 'ASSIGNED')
            .map((c) => ({ id: c.row['id'], type: 'ENTITLEMENT' })),
        })) as T, 300);
      }
      if (indices.includes('identities')) {
        const term = q.replace(/\\/g, '').replace(/\*$/, '').toLowerCase();
        const exact = [...q.matchAll(/"([^"]+)"/g)].map((m) => m[1].toLowerCase());
        return delay(DEMO_IDENTITIES.filter((d) => exact.length
          ? exact.includes(d.name.toLowerCase()) || exact.includes(d.email.toLowerCase())
          : [d.displayName, d.name, d.email].some((v) => v.toLowerCase().split(/[\s.@]/).some((w) => w.startsWith(term))
            || v.toLowerCase().startsWith(term))) as T);
      }
      return delay(DEMO_CATALOG.filter((c) => c.source).map((c) => ({ id: c.row['id'], source: { name: c.source } })) as T);
    }
    const single = /^\/generic-approvals\/v1\/([^/]+)\/(approve|reject)$/.exec(path);
    if (single) return this.decide(decodeURIComponent(single[1]), single[2] as 'approve' | 'reject', body?.['comment'] as string) as Promise<T>;
    const bulk = /^\/generic-approvals\/v1\/bulk-(approve|reject)$/.exec(path);
    if (bulk) {
      // Verified live: 403 for anyone without ORG_ADMIN, even on their own approvals; 202 {} otherwise.
      if (!this.user()?.capabilities.isOrgAdmin) return Promise.reject(apiError(403, 'Forbidden'));
      for (const id of (body['approvalIds'] as string[]) ?? []) {
        void this.decide(id, bulk[1] as 'approve' | 'reject', body['comment'] as string).catch(() => undefined);
      }
      return delay({} as T, 300);
    }
    if (/^\/launchers\/v1\/[^/]+\/launch$/.test(path)) {
      // A user without the Launcher Access profile can't start it.
      if (!this.launcherAccess) return Promise.reject(apiError(403, 'Forbidden'));
      const k = this.runs++;
      const processId = `01DEMOPROCESS${String(k).padStart(13, '0')}`;
      this.processes.set(processId, { k, polls: 0 });
      this.forms.set(`f-${k}`, { k, state: 'ASSIGNED' });
      return delay({ interactiveProcessId: processId } as T, 400);
    }
    if (path === '/access-requests/v1') return this.requestAccess(body) as Promise<T>;
    return Promise.reject(new Error(`demo: no fixture for POST ${path}`));
  }

  /** PATCH /form-instances/v1/{id}: the Launcher form, submitted (the INC must pass the form's REGEX). */
  patch<T>(path: string, ops: unknown): Promise<T> {
    const m = /^\/form-instances\/v1\/([^/]+)$/.exec(path);
    const form = m && this.forms.get(decodeURIComponent(m[1]));
    if (!form) return Promise.reject(new Error(`demo: no fixture for PATCH ${path}`));
    for (const op of (ops as { path: string; value: unknown }[]) ?? []) {
      if (op.path === '/formData') form.formData = op.value as Record<string, unknown>;
    }
    const data = form.formData ?? {};
    if (!new RegExp(DEMO_CONFIG.incPattern).test(String(data['inc'] ?? ''))) {
      form.state = 'IN_PROGRESS';
      return delay({} as T);
    }
    if (form.state !== 'COMPLETED') {
      form.state = 'COMPLETED';   // verified live: one PATCH can go ASSIGNED -> COMPLETED (the workflow took it)
      // The workflow files the requests in its owner's name, then posts its INFO message.
      const people = (data['people'] as string[] | undefined)?.length ?? 0;
      const items = (data['items'] as unknown[] | undefined)?.length ?? 0;
      const process = [...this.processes.values()].find((p) => p.k === form.k);
      setTimeout(() => process && (process.answer = {
        title: `Requested: ${data['inc']}${data['partLabel'] ?? ''}`,
        message: `${people * items} access requests filed. Each is approved the way it's set up in SailPoint.`,
      }), 1500);
    }
    return delay({} as T);
  }

  /**
   * POST /access-requests/v1: one request per person and item, as the demo user. An item a person already has
   * pending is skipped (existingRequests); one with no approval of its own is granted at once.
   */
  private requestAccess(body: Record<string, unknown>): Promise<unknown> {
    if (!this.launcherAccess) return Promise.reject(apiError(403, 'Forbidden'));
    const people = (body['requestedFor'] as string[]) ?? [];
    const items = (body['requestedItems'] as { id: string; comment?: string; removeDate?: string }[]) ?? [];
    const created = new Date().toISOString();
    const newRequests: unknown[] = [];
    const existingRequests: unknown[] = [];
    for (const id of people) {
      const doc = [...DEMO_IDENTITIES, ...DEMO_CROWD].find((d) => d.id === id);
      const person = { id, name: doc?.displayName ?? id };
      for (const item of items) {
        const ref = { requestedFor: id, requestedItem: item.id };
        if (DEMO_HELD[id]?.[item.id] === 'PENDING') {
          existingRequests.push(ref);
          continue;
        }
        newRequests.push(ref);
        const name = String(DEMO_CATALOG.find((c) => c.row['id'] === item.id)?.row['name'] ?? item.id);
        const free = !demoItemApproval(item.id);
        this.filed.unshift(demoRequest(person, name, free ? 'REQUEST_COMPLETED' : 'EXECUTING', created,
          item.comment ?? '', item.removeDate ?? null, free ? null : { status: 'PENDING' }));
      }
    }
    return delay({ newRequests, existingRequests }, 500);
  }
}

/** Fixed demo config instead of public/bulk-access.config.json. */
@Injectable()
export class DemoConfigService extends BulkConfigService {
  override async load(): Promise<void> {
    this.config.set(DEMO_CONFIG);
  }
}

/**
 * Fill the store as a user would have by the time they reach the scenario's screen. The approvals
 * scenarios also need the demo plugin service (to play a non-admin) and the approvals store.
 */
export function applyScenario(scenario: DemoScenario, store: RequestStore, nav: NavService,
                              more: { plugin?: unknown; approvals?: ApprovalsStore; config?: BulkConfigService } = {}): void {
  const big = scenario === 'people' || scenario === 'parts-review' || scenario === 'parts-submitted';
  // 600 people through the Launcher: 3 runs of 250 (direct requests carry 10 people each).
  if (scenario === 'parts-review' || scenario === 'parts-submitted') {
    more.config?.config.update((c) => ({ ...c, submit: 'launcher' }));
  }
  if (scenario === 'launcher-denied' && more.plugin instanceof DemoPluginService) {
    more.plugin.setOrgAdmin(false);
    more.plugin.launcherAccess = false;
  }
  if (scenario === 'history') {
    nav.tab.set('mine');
    nav.chosen = true;
    return;
  }
  if (scenario === 'approvals' || scenario === 'approvals-partial') {
    // An ordinary item approver (not ORG_ADMIN): the Approvals tab opens first, on the big request.
    if (more.plugin instanceof DemoPluginService) {
      more.plugin.setOrgAdmin(false);
      more.plugin.faults = scenario === 'approvals-partial' ? PARTIAL_FAULTS : null;
    }
    nav.tab.set('approvals');
    nav.chosen = true;
    more.approvals?.openKey.set(DEMO_PENDING_INCS.big);
    return;
  }
  if (scenario === 'new') return;
  const chosen = ['Alan Bradley', 'Andrei Popescu', 'Amelia Thornton', 'Beatriz Santos', 'Bruno Marchetti'].map((n) => demoPerson(n));
  if (scenario === 'people') {
    // A pasted list of 600 people: no people limit.
    store.people.set([...chosen.slice(0, 4), ...crowdPeople(596)]);
    store.peopleQuery.set('br');
    store.peopleResults.set(['Alan Bradley', 'Brenda Cooper', 'Bruno Marchetti'].map((n) => demoPerson(n)));
    store.resolution.set({
      resolved: crowdPeople(596),
      unresolved: ['j.doe@example.edu'],
      ambiguous: [{ token: 'andrea.kim', matches: [demoPerson('Andrea Kim', 0), demoPerson('Andrea Kim', 1)] }],
    });
    store.pasteText.set('j.doe@example.edu\nandrea.kim');
    return;
  }
  store.people.set(big ? [...chosen, ...crowdPeople(595)] : chosen);
  store.items.set([demoItem('ACME Bulk Test Access'), demoItem('PACS Radiologist Workstation')]);
  if (scenario === 'items') {
    store.step.set(2);
    return;
  }
  // inc-error: a mistyped INC, refused as it's typed.
  store.inc.set(scenario === 'inc-error' ? 'INC12345' : 'INC0048391');
  store.justification.set('Radiology is moving to the new PACS on 14 Oct; these readers need workstation access before go-live.');
  if (big || scenario === 'temporary') {
    store.accessMode.set('duration');
    store.durationN.set(30);
    store.durationUnit.set('DAYS');
  }
  if (big) store.justification.set('Hospital-wide move to the new PACS on 14 Oct: all clinical readers need workstation access for the cutover.');
  if (scenario === 'details' || scenario === 'inc-error' || scenario === 'temporary') {
    store.step.set(3);
    return;
  }
  store.step.set(4);
  if (['submitted', 'parts-submitted', 'launcher-denied'].includes(scenario)) void store.submit();
}

export function demoProviders(scenario: DemoScenario): (Provider | EnvironmentProviders)[] {
  return [
    { provide: SailpointPluginService, useClass: DemoPluginService },
    { provide: BulkConfigService, useClass: DemoConfigService },
    provideAppInitializer(() => {
      // inject() only works before the first await.
      const [config, store, nav] = [inject(BulkConfigService), inject(RequestStore), inject(NavService)];
      const more = { plugin: inject(SailpointPluginService), approvals: inject(ApprovalsStore), config };
      return config.load().then(() => applyScenario(scenario, store, nav, more));
    }),
  ];
}
