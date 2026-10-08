/**
 * ?demo=<scenario>: run the page standalone (no ISC, no tenant) with fixture data.
 * Used for the README screenshots and for UI work. A stub replaces
 * SailpointPluginService and answers the same API paths from fixtures.ts.
 *
 * Scenarios: new (empty), people (600 chosen: no limit, 3 parts), items, approver,
 * approver-error, temporary (approver step with temporary access), review,
 * parts-review (600 people, 3 parts, temporary), submitted, parts-submitted, history,
 * approvals (a non-admin item approver: 300 + 40 approvals from two bulk requests, and 3 others),
 * approvals-partial (the same, with throttling, failures and colleagues deciding first).
 */
import { computed, inject, Injectable, provideAppInitializer, signal, type EnvironmentProviders, type Provider } from '@angular/core';
import type { PluginContext } from '@sailpoint/ui-plugin-sdk';
import { SailpointPluginService } from '@core';

import { ApprovalsStore } from '../bulk/approvals-store';
import type { GenericApproval } from '../bulk/bulk-api.service';
import { BulkConfigService } from '../bulk/bulk-config.service';
import { NavService } from '../bulk/nav';
import { RequestStore } from '../bulk/request-store';
import {
  crowdPeople, DEMO_APPROVALS, DEMO_CATALOG, DEMO_COLLEAGUE, DEMO_CONFIG, DEMO_CROWD, DEMO_HELD, DEMO_IDENTITIES, DEMO_ME,
  DEMO_PENDING, DEMO_PENDING_INCS, DEMO_REQUESTS, demoExecutionId, demoItem, demoNewApproval, demoPerson,
} from './fixtures';

export const DEMO_SCENARIOS = [
  'new', 'people', 'items', 'approver', 'approver-error', 'temporary', 'review', 'parts-review', 'submitted',
  'parts-submitted', 'history', 'approvals', 'approvals-partial',
] as const;

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
export type DemoScenario = (typeof DEMO_SCENARIOS)[number];

/** The scenario named in the URL, or null. Never inside an iframe (that is ISC). */
export function demoScenario(loc: Pick<Location, 'search' | 'hash'>, top = window.top === window.self): DemoScenario | null {
  if (!top) return null;
  const params = new URLSearchParams(loc.search || loc.hash.split('?')[1] || '');
  const value = params.get('demo');
  if (value === null) return null;
  return (DEMO_SCENARIOS as readonly string[]).includes(value) ? (value as DemoScenario) : 'new';
}

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
  readonly tenant = computed(() => this._context()?.tenant ?? null);
  readonly user = computed(() => this._context()?.user ?? null);
  readonly apiReady = computed(() => true);

  /** Approvals the demo's workflow runs created, newest first. */
  private submitted: GenericApproval[] = [];
  private runs = 0;
  /** Item approvals waiting for the demo user (the Approvals tab), decided in place. */
  private pending: GenericApproval[] = structuredClone(DEMO_PENDING);
  private position = new Map(DEMO_PENDING.map((a, i) => [a.id, i + 1]));
  /** IDs whose one-off 429/503 has been served. */
  private hiccups = new Set<string>();
  faults: DemoApprovalFaults | null = null;

  whenReady(): Promise<PluginContext> {
    return Promise.resolve(this._context()!);
  }

  /** Play a user without ORG_ADMIN (an ordinary item approver). */
  setOrgAdmin(isOrgAdmin: boolean): void {
    this._context.update((c) => c && { ...c, user: { ...c.user, capabilities: { ...c.user.capabilities, isOrgAdmin } } });
  }

  /** The list leaves out who decided; the single GET has it. */
  private static listRow({ approvedBy: _b, rejectedBy: _r, assignedTo: _t, ...row }: GenericApproval): GenericApproval {
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
    if (route === '/v3/requestable-objects') {
      const who = params.get('identity-id');
      if (who) {
        const held = DEMO_HELD[who] ?? {};
        return delay(DEMO_CATALOG.filter((c) => held[String(c.row['id'])])
          .map((c) => ({ ...c.row, requestStatus: held[String(c.row['id'])] })) as T, 400);
      }
      return delay(DEMO_CATALOG.map((c) => c.row) as T);
    }
    if (route.startsWith('/v2025/identities/')) {
      const id = route.split('/').pop();
      const doc = [...DEMO_IDENTITIES, ...DEMO_CROWD].find((d) => d.id === id);
      return doc ? delay({ id: doc.id, name: doc.name, alias: doc.name, emailAddress: doc.email,
        attributes: { displayName: doc.displayName, department: doc.attributes.department } } as T)
        : Promise.reject(Object.assign(new Error('Not found'), { status: 404 }));
    }
    if (route === '/v2025/identities') {
      const wanted = new Set(filterValues(path));
      return delay([...DEMO_IDENTITIES, ...DEMO_CROWD]
        .filter((d) => wanted.has(d.id) || wanted.has(d.name.toLowerCase()) || wanted.has(d.email.toLowerCase()))
        .map((d) => ({ id: d.id, name: d.name, alias: d.name, emailAddress: d.email,
          attributes: { displayName: d.displayName, department: d.attributes.department } })) as T);
    }
    if (route === '/v3/accounts') return delay([] as T);
    if (route === '/v3/workflows') return delay([{ id: 'demo-workflow', name: DEMO_CONFIG.workflowName }] as T);
    if (route.startsWith('/v3/workflow-executions/')) return delay({ id: route.split('/').pop(), status: 'Running' } as T);
    const approvals = [...this.submitted, ...DEMO_APPROVALS];
    if (route === '/v2025/generic-approvals' && params.get('mine') === 'true') {
      // The Approvals tab: the user's pending item approvals, oldest first, a page at a time.
      const offset = Number(params.get('offset') ?? 0);
      const page = this.pending.filter((a) => a.status === 'PENDING')
        .sort((a, b) => (a.createdDate ?? '').localeCompare(b.createdDate ?? ''))
        .slice(offset, offset + Number(params.get('limit') ?? 250));
      return delay(page.map(DemoPluginService.listRow) as T, 250);
    }
    if (route === '/v2025/generic-approvals' && (params.get('filters') ?? '').startsWith('approvalId in')) {
      const wanted = new Set(filterValues(path));
      return delay([...this.pending, ...approvals].filter((a) => wanted.has(a.id)).map(DemoPluginService.listRow) as T);
    }
    // Like the real API, the list leaves out approvers and deciders; the detail call has them.
    if (route === '/v2025/generic-approvals') {
      return delay(approvals.map(({ approvers: _a, approvedBy: _b, rejectedBy: _r, ...row }) => row) as T);
    }
    if (route.startsWith('/v2025/generic-approvals/')) {
      const hit = [...this.pending, ...approvals].find((a) => a.id === route.split('/').pop());
      return hit ? delay(hit as T) : Promise.reject(Object.assign(new Error('Not found'), { status: 404 }));
    }
    if (route === '/v3/access-request-status') {
      const offset = Number(params.get('offset') ?? 0);
      return delay(DEMO_REQUESTS.slice(offset, offset + Number(params.get('limit') ?? 250)) as T);
    }
    return Promise.reject(new Error(`demo: no fixture for GET ${path}`));
  }

  post<T>(path: string, data: unknown): Promise<T> {
    const body = data as Record<string, unknown>;
    if (path.startsWith('/v3/search')) {
      const indices = (body['indices'] as string[]) ?? [];
      const q = String((body['query'] as { query: string }).query);
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
    const single = /^\/v2025\/generic-approvals\/([^/]+)\/(approve|reject)$/.exec(path);
    if (single) return this.decide(decodeURIComponent(single[1]), single[2] as 'approve' | 'reject', body?.['comment'] as string) as Promise<T>;
    const bulk = /^\/v2025\/generic-approvals\/bulk-(approve|reject)$/.exec(path);
    if (bulk) {
      // Verified live: 403 for anyone without ORG_ADMIN, even on their own approvals; 202 {} otherwise.
      if (!this.user()?.capabilities.isOrgAdmin) return Promise.reject(apiError(403, 'Forbidden'));
      for (const id of (body['approvalIds'] as string[]) ?? []) {
        void this.decide(id, bulk[1] as 'approve' | 'reject', body['comment'] as string).catch(() => undefined);
      }
      return delay({} as T, 300);
    }
    if (/^\/v3\/workflows\/[^/]+\/test$/.test(path)) {
      const input = body['input'] as { inc: string; approverId: string; partLabel?: string; accessLabel?: string };
      const approver = DEMO_IDENTITIES.find((d) => d.id === input.approverId)?.displayName ?? 'the approver';
      const k = this.runs++;
      setTimeout(() => this.submitted.unshift(demoNewApproval(input.inc, approver, k, input.partLabel, input.accessLabel)), 1500);
      return delay({ workflowExecutionId: demoExecutionId(k) } as T, 600);
    }
    return Promise.reject(new Error(`demo: no fixture for POST ${path}`));
  }

  setRoute(): Promise<void> {
    return Promise.resolve();
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
                              more: { plugin?: unknown; approvals?: ApprovalsStore } = {}): void {
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
  const big = scenario === 'people' || scenario === 'parts-review' || scenario === 'parts-submitted';
  if (scenario === 'people') {
    // A pasted list of 600 people: no people limit, sent as 3 approvals of 250.
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
  // approver-error: the user picked themselves and mistyped the INC; both are refused live.
  store.approver.set(demoPerson(scenario === 'approver-error' ? DEMO_ME.name : 'Aisha Bello'));
  store.inc.set(scenario === 'approver-error' ? 'INC12345' : 'INC0048391');
  store.justification.set('Radiology is moving to the new PACS on 14 Oct; these readers need workstation access before go-live.');
  if (big || scenario === 'temporary') {
    store.accessMode.set('duration');
    store.durationN.set(30);
    store.durationUnit.set('DAYS');
  }
  if (big) store.justification.set('Hospital-wide move to the new PACS on 14 Oct: all clinical readers need workstation access for the cutover.');
  if (scenario === 'approver' || scenario === 'approver-error' || scenario === 'temporary') {
    store.step.set(3);
    return;
  }
  store.step.set(4);
  if (scenario === 'submitted' || scenario === 'parts-submitted') void store.submit();
}

export function demoProviders(scenario: DemoScenario): (Provider | EnvironmentProviders)[] {
  return [
    { provide: SailpointPluginService, useClass: DemoPluginService },
    { provide: BulkConfigService, useClass: DemoConfigService },
    provideAppInitializer(() => {
      // inject() only works before the first await.
      const [config, store, nav] = [inject(BulkConfigService), inject(RequestStore), inject(NavService)];
      const more = { plugin: inject(SailpointPluginService), approvals: inject(ApprovalsStore) };
      return config.load().then(() => applyScenario(scenario, store, nav, more));
    }),
  ];
}
