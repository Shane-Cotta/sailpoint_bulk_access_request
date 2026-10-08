import { inject, Injectable } from '@angular/core';
import { SailpointPluginService } from '@core';

import type { RuntimeConfig } from './runtime-config';
import { APPROVAL_NAME_PREFIX, catalogOptions, type AccessItem, type CatalogOption } from './rules';

/**
 * Every SailPoint API call the page makes, as the signed-in user with the
 * plugin's scoped token (SailpointPluginService.get/post). In ?demo=1 mode the
 * plugin service is swapped for a stub that answers from fixtures.
 */

/** A person as the page shows them: an identity, found by search or through one of their accounts. */
export interface Person {
  id: string;
  name: string;
  email?: string | null;
  /** Department, or the source we found them through. */
  detail?: string | null;
}

export interface Resolution {
  resolved: Person[];
  unresolved: string[];
  ambiguous: { token: string; matches: Person[] }[];
}

/**
 * What POST /v3/workflows/{id}/test receives as `input`: the plugin workflow's trigger
 * contract (CONTRACTS §3). One run per part; every field is always present.
 */
export interface BulkInput {
  /** 1..partSize identity IDs (at most 250, SailPoint's loop limit). */
  people: string[];
  items: AccessItem[];
  approverId: string;
  requesterId: string;
  inc: string;
  justification: string;
  /** 1-based part number, the number of parts, and partLabel(part, parts) ("" when parts is 1). */
  part: number;
  parts: number;
  partLabel: string;
  /** Manage Access v2 removeDuration: "" = permanent, e.g. "30d" or "720h". */
  removeDuration: string;
  /** "Permanent", "Temporary: 30 days" or "Temporary: until 2026-11-07". */
  accessLabel: string;
}

/** Progress of a pasted-list lookup: tokens looked up so far, out of all of them. */
export type ResolveProgress = (done: number, total: number) => void;

export interface Execution {
  id: string;
  status: 'Running' | 'Completed' | 'Failed' | 'Canceled' | string;
  startTime?: string;
  closeTime?: string;
}

/** The fields of a /v2025/generic-approvals row the page reads. */
type Who = { name?: string };
/**
 * The approver the requester chose. While pending that is `assignedTo`. If the task was
 * later reassigned (an admin acting on the approver's behalf shows up as a manual
 * reassignment), the original choice is the first `reassignedFrom`; otherwise `approvers`.
 */
export function assignedApproverNames(a: {
  assignedTo?: Who[] | null;
  approvers?: Who[];
  reassignmentHistory?: { reassignedFrom?: Who }[] | null;
}): string[] {
  const original = a.reassignmentHistory?.[0]?.reassignedFrom;
  const people = a.assignedTo?.length ? a.assignedTo : original ? [original] : a.approvers ?? [];
  return people.map((x) => x.name).filter((n): n is string => !!n);
}

export interface GenericApproval {
  id: string;
  name?: { value: string; locale?: string }[];
  description?: { value: string }[];
  status: string;
  createdDate?: string;
  completedDate?: string | null;
  requester?: { identityID?: string; name?: string };
  /** Who the approval was assigned to (the chosen approver). */
  assignedTo?: { identityID?: string; name?: string }[] | null;
  /** Present when the task was reassigned (e.g. an admin decided on the approver's behalf). */
  reassignmentHistory?: { reassignedFrom?: { name?: string } }[] | null;
  /** Who acted on it. An admin deciding on someone's behalf shows up here, not the assignee. */
  approvers?: { identityID?: string; name?: string }[];
  approvedBy?: { name?: string }[] | null;
  rejectedBy?: { name?: string }[] | null;
  referenceData?: { id: string; type: string }[];
  comments?: { comment?: string; author?: { name?: string } }[];
}

/** The fields of a /v3/access-request-status row the page reads. */
export interface AccessRequestStatus {
  accessRequestId?: string;
  id: string;
  name: string;
  type: string;
  state: string;
  created?: string;
  modified?: string;
  requestedFor?: { id: string; name?: string };
  requester?: { id: string; name?: string };
  requesterComment?: { comment?: string } | null;
  /** When temporary access is removed (null or missing = permanent). */
  removeDate?: string | null;
}

/** Item access a chosen person already holds or has pending (from requestable-objects?identity-id=). */
export interface ExistingAccess {
  personId: string;
  itemId: string;
  status: 'ASSIGNED' | 'PENDING';
}

const IDENTITY_ID = /^[0-9a-f]{32}$/i;
const LUCENE_SPECIAL = /[+\-&|!(){}[\]^"~*?:\\/]/g;

export function escapeQuery(term: string): string {
  return term.replace(LUCENE_SPECIAL, (c) => `\\${c}`);
}

function quoted(value: string): string {
  return `"${value.replace(/["\\]/g, (c) => `\\${c}`)}"`;
}

/**
 * Batch sizes for resolving a pasted list. Verified with read-only GETs on a demo tenant:
 * /v2025/identities takes `id in (…)` (50 IDs ≈ 2 KB of URL) but only `eq` for alias and
 * email, so words go as an `alias eq … or email eq …` chain; URLs above ~8 KB get HTTP 414.
 * The list API is search-backed and misses identities not indexed yet, so leftovers are
 * looked up through their accounts, then (IDs only) one by one.
 */
export const RESOLVE_BATCH = 50;
const FILTER_BUDGET = 4000;   // characters of filter per call, well under the URL limit
const RESOLVE_CONCURRENCY = 4;

function chunk<T>(list: T[], size: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < list.length; i += size) out.push(list.slice(i, i + size));
  return out;
}

/**
 * Cut `list` into batches of at most `size`, also keeping each batch's filter text
 * (terms joined by `sep`) under `budget` characters, so long emails can't overflow the URL.
 */
export function batches<T>(list: T[], term: (x: T) => string, sep: string,
                           budget = FILTER_BUDGET, size = RESOLVE_BATCH): T[][] {
  const out: T[][] = [];
  let current: T[] = [];
  let length = 0;
  for (const x of list) {
    const cost = term(x).length + sep.length;
    if (current.length && (current.length >= size || length + cost > budget)) {
      out.push(current);
      current = [];
      length = 0;
    }
    current.push(x);
    length += cost;
  }
  if (current.length) out.push(current);
  return out;
}

/** Run `jobs` with at most `concurrency` at a time. */
export async function pool(jobs: (() => Promise<unknown>)[], concurrency: number): Promise<void> {
  let next = 0;
  const worker = async () => {
    for (let i = next++; i < jobs.length; i = next++) await jobs[i]();
  };
  await Promise.all(Array.from({ length: Math.min(concurrency, jobs.length) }, worker));
}

/** Retry a call SailPoint throttled (HTTP 429), with a short back-off. */
export async function retry<T>(call: () => Promise<T>, attempts = 3, waitMs = 1500): Promise<T> {
  for (let i = 1; ; i++) {
    try {
      return await call();
    } catch (err) {
      if ((err as { status?: number })?.status !== 429 || i >= attempts) throw err;
      await new Promise((resolve) => setTimeout(resolve, waitMs * i));
    }
  }
}

/** Split a pasted list (newlines, commas, semicolons, tabs) into unique tokens. */
export function splitPasted(text: string): string[] {
  return [...new Set(text.split(/[\n,;\t]+/).map((t) => t.trim()).filter(Boolean))];
}

type Row = Record<string, unknown>;

function personFromSearch(doc: Row): Person {
  const attrs = (doc['attributes'] ?? {}) as Row;
  return {
    id: String(doc['id']),
    name: String(doc['displayName'] || doc['name'] || doc['id']),
    email: (doc['email'] as string) || null,
    detail: (attrs['department'] as string) || null,
  };
}

function personFromIdentity(row: Row): Person {
  const attrs = (row['attributes'] ?? {}) as Row;
  return {
    id: String(row['id']),
    name: String(attrs['displayName'] || row['name'] || row['id']),
    email: (row['emailAddress'] as string) || (attrs['email'] as string) || null,
    detail: (attrs['department'] as string) || null,
  };
}

function personFromAccount(acct: Row): Person | null {
  const identityId = acct['identityId'] as string | undefined;
  if (!identityId) return null;
  const identity = (acct['identity'] ?? {}) as Row;
  const attrs = (acct['attributes'] ?? {}) as Row;
  return {
    id: identityId,
    name: String(identity['name'] || attrs['displayName'] || acct['name']),
    email: (attrs['email'] as string) || null,
    detail: (attrs['department'] as string) || (acct['sourceName'] as string) || null,
  };
}

/** Keys a pasted token may equal, lower-cased. */
function identityKeys(row: Row): string[] {
  return [row['id'], row['name'], row['alias'], row['emailAddress']].filter(Boolean).map((v) => String(v).toLowerCase());
}

function accountKeys(acct: Row): string[] {
  const attrs = (acct['attributes'] ?? {}) as Row;
  return [acct['name'], acct['nativeIdentity'], attrs['userName'], attrs['email'], attrs['uid']]
    .filter(Boolean)
    .map((v) => String(v).toLowerCase());
}

function searchKeys(doc: Row): string[] {
  return [doc['id'], doc['name'], doc['displayName'], doc['email']].filter(Boolean).map((v) => String(v).toLowerCase());
}

@Injectable({ providedIn: 'root' })
export class BulkApiService {
  private readonly plugin = inject(SailpointPluginService);

  // ── People ───────────────────────────────────────────────────────────────
  /**
   * Type-ahead people search. Identity search covers indexed identities; account
   * names cover identities that are not in the search index yet (for example
   * ones a new source just created).
   */
  async searchPeople(term: string, limit = 20): Promise<Person[]> {
    const t = term.trim();
    if (t.length < 2) return [];
    const [docs, accounts] = await Promise.allSettled([
      this.plugin.post<Row[]>(`/v3/search?limit=${limit}`, {
        indices: ['identities'],
        query: { query: `${escapeQuery(t)}*` },
        queryResultFilter: { includes: ['id', 'name', 'displayName', 'email', 'attributes.department'] },
        sort: ['name'],
      }),
      this.plugin.get<Row[]>(`/v3/accounts?limit=${limit}&filters=${encodeURIComponent(`name sw ${quoted(t)}`)}`),
    ]);
    const byId = new Map<string, Person>();
    for (const doc of docs.status === 'fulfilled' ? docs.value ?? [] : []) byId.set(String(doc['id']), personFromSearch(doc));
    for (const acct of accounts.status === 'fulfilled' ? accounts.value ?? [] : []) {
      const p = personFromAccount(acct);
      if (p && !byId.has(p.id)) byId.set(p.id, p);
    }
    if (docs.status === 'rejected' && accounts.status === 'rejected') throw docs.reason;
    return [...byId.values()].sort((a, b) => a.name.localeCompare(b.name));
  }

  /**
   * Resolve a pasted list of identity IDs, usernames or emails, in batches of about
   * RESOLVE_BATCH per call (so 1,000+ entries take a few dozen calls). A token resolves
   * when exactly one identity matches it; several matches are reported as ambiguous.
   *
   *  1. IDs: `/v2025/identities?filters=id in (…)`.
   *  2. Words: `/v2025/identities?filters=alias eq … or email eq …` (case-insensitive).
   *  3. Whatever is still unmatched: identity search (name, email) and accounts
   *     (name, nativeIdentity; identityId for IDs), which also find identities the
   *     identities list hasn't indexed yet.
   *  4. IDs still unmatched: GET /v2025/identities/{id}, one by one.
   */
  async resolvePeople(tokens: string[], onProgress?: ResolveProgress): Promise<Resolution> {
    const candidates = new Map<string, Map<string, Person>>(); // token(lower) -> identityId -> person
    const lower = new Map(tokens.map((t) => [t.toLowerCase(), t]));
    const add = (key: string, p: Person) => {
      if (!candidates.has(key)) candidates.set(key, new Map());
      candidates.get(key)!.set(p.id, p);
    };
    const match = (keys: string[], p: Person) => keys.forEach((k) => lower.has(k) && add(k, p));
    const open = (list: string[]) => list.filter((t) => !candidates.has(t.toLowerCase()));

    const ids = tokens.filter((t) => IDENTITY_ID.test(t));
    const words = tokens.filter((t) => !IDENTITY_ID.test(t));
    const total = tokens.length;
    let done = 0;
    const tick = (n: number) => {
      done = Math.min(total, done + n);
      onProgress?.(done, total);
    };
    onProgress?.(0, total);

    // 1 + 2: the identities list, the bulk of the work (progress counts these tokens).
    const idTerm = (id: string) => quoted(id);
    const wordTerm = (w: string) => `alias eq ${quoted(w)} or email eq ${quoted(w)}`;
    const jobs: (() => Promise<void>)[] = [
      ...batches(ids, idTerm, ',').map((part) => () => this.identities(`id in (${part.map(idTerm).join(',')})`)
        .then((rows) => rows.forEach((r) => match(identityKeys(r), personFromIdentity(r))))
        .finally(() => tick(part.length))),
      ...batches(words, wordTerm, ' or ').map((part) => () => this.identities(part.map(wordTerm).join(' or '))
        .then((rows) => rows.forEach((r) => match(identityKeys(r), personFromIdentity(r))))
        .finally(() => tick(part.length))),
    ];
    await pool(jobs, RESOLVE_CONCURRENCY);

    // 3: fallbacks for what the identities list didn't find.
    const leftWords = open(words);
    const leftIds = open(ids);
    const fallbacks: (() => Promise<void>)[] = [];
    for (const part of chunk(leftWords, RESOLVE_BATCH)) {
      const list = part.map(quoted).join(' OR ');
      fallbacks.push(() => this.plugin.post<Row[]>('/v3/search?limit=250', {
        indices: ['identities'],
        query: { query: `name:(${list}) OR email:(${list})` },
        queryResultFilter: { includes: ['id', 'name', 'displayName', 'email', 'attributes.department'] },
      }).then((docs) => (docs ?? []).forEach((d) => match(searchKeys(d), personFromSearch(d)))).catch(() => undefined));
    }
    for (const part of batches(leftWords, quoted, ',', FILTER_BUDGET / 2)) {
      const list = part.map(quoted).join(',');
      fallbacks.push(() => this.accounts(`name in (${list}) or nativeIdentity in (${list})`)
        .then((accts) => accts.forEach((a) => {
          const p = personFromAccount(a);
          if (p) match(accountKeys(a), p);
        })));
    }
    for (const part of batches(leftIds, quoted, ',')) {
      fallbacks.push(() => this.accounts(`identityId in (${part.map(quoted).join(',')})`)
        .then((accts) => accts.forEach((a) => {
          const p = personFromAccount(a);
          if (p) add(p.id.toLowerCase(), p);
        })));
    }
    await pool(fallbacks, RESOLVE_CONCURRENCY);

    // 4: an ID nobody has an account for yet.
    await pool(open(ids).map((id) => async () => {
      try {
        const row = await this.plugin.get<Row>(`/v2025/identities/${encodeURIComponent(id)}`);
        if (row?.['id']) add(id.toLowerCase(), personFromIdentity(row));
      } catch {
        /* unknown id: reported as unresolved */
      }
    }), RESOLVE_CONCURRENCY);
    onProgress?.(total, total);

    const out: Resolution = { resolved: [], unresolved: [], ambiguous: [] };
    const seen = new Set<string>();
    for (const token of tokens) {
      const found = [...(candidates.get(token.toLowerCase())?.values() ?? [])];
      if (!found.length) out.unresolved.push(token);
      else if (found.length > 1) out.ambiguous.push({ token, matches: found });
      else if (!seen.has(found[0].id)) {
        seen.add(found[0].id);
        out.resolved.push(found[0]);
      }
    }
    return out;
  }

  /** One /v2025/identities list call (empty on failure: the fallbacks still run). */
  private identities(filter: string): Promise<Row[]> {
    return retry(() => this.plugin.get<Row[]>(`/v2025/identities?limit=250&filters=${encodeURIComponent(filter)}`))
      .then((rows) => rows ?? [])
      .catch(() => []);
  }

  private accounts(filter: string): Promise<Row[]> {
    return retry(() => this.plugin.get<Row[]>(`/v3/accounts?limit=250&filters=${encodeURIComponent(filter)}`))
      .then((rows) => rows ?? [])
      .catch(() => []);
  }

  // ── Catalog ──────────────────────────────────────────────────────────────
  /** The Request Center catalog (types and name prefix from the config), with each item's source. */
  async catalog(cfg: RuntimeConfig, max = 1000): Promise<CatalogOption[]> {
    // Repeat `types`: a comma list that includes ENTITLEMENT is rejected with a 400.
    const types = cfg.catalogTypes.map((t) => `types=${t}`).join('&');
    const filter = cfg.nameStartsWith ? `&filters=${encodeURIComponent(`name sw ${quoted(cfg.nameStartsWith)}`)}` : '';
    const rows: Row[] = [];
    for (let offset = 0; offset < max; offset += 250) {
      const page = await this.plugin.get<Row[]>(`/v3/requestable-objects?${types}&limit=250&offset=${offset}${filter}`);
      rows.push(...(page ?? []));
      if (!page || page.length < 250) break;
    }
    return catalogOptions(cfg, rows, await this.sources(rows));
  }

  /** Source names for access profiles and entitlements (roles have none). Best effort. */
  private async sources(rows: Row[]): Promise<Record<string, string>> {
    const ids = rows.filter((r) => r['type'] !== 'ROLE').map((r) => String(r['id']));
    const out: Record<string, string> = {};
    await Promise.all(chunk(ids, 100).map(async (part) => {
      try {
        const docs = await this.plugin.post<Row[]>('/v3/search?limit=250', {
          indices: ['accessprofiles', 'entitlements'],
          query: { query: `id:(${part.join(' OR ')})` },
          queryResultFilter: { includes: ['id', 'source.name'] },
        });
        for (const d of docs ?? []) {
          const name = ((d['source'] ?? {}) as Row)['name'];
          if (name) out[String(d['id'])] = String(name);
        }
      } catch {
        /* sources are decoration; the catalog still works without them */
      }
    }));
    return out;
  }

  /** Which chosen people already hold (or have pending) which chosen items. */
  async existingAccess(personIds: string[], items: AccessItem[], concurrency = 5): Promise<ExistingAccess[]> {
    if (!personIds.length || !items.length) return [];
    const types = [...new Set(items.map((i) => i.type))].map((t) => `types=${t}`).join('&');
    const filter = encodeURIComponent(`id in (${items.map((i) => quoted(i.id)).join(',')})`);
    const out: ExistingAccess[] = [];
    const queue = [...personIds];
    const worker = async () => {
      for (let id = queue.shift(); id; id = queue.shift()) {
        try {
          const rows = await this.plugin.get<Row[]>(
            `/v3/requestable-objects?identity-id=${encodeURIComponent(id)}&${types}&limit=250&filters=${filter}`,
          );
          for (const r of rows ?? []) {
            const status = r['requestStatus'];
            if (status === 'ASSIGNED' || status === 'PENDING') out.push({ personId: id, itemId: String(r['id']), status });
          }
        } catch {
          /* a warning we can't compute is not a blocker */
        }
      }
    };
    await Promise.all(Array.from({ length: Math.min(concurrency, queue.length) }, worker));
    return out;
  }

  // ── Workflow ─────────────────────────────────────────────────────────────
  async workflowId(cfg: RuntimeConfig): Promise<string> {
    if (cfg.workflowId) return cfg.workflowId;
    const flows = await this.plugin.get<Row[]>('/v3/workflows?limit=250');
    const hit = (flows ?? []).find((w) => w['name'] === cfg.workflowName);
    if (!hit) throw new Error(`The workflow "${cfg.workflowName}" is not installed in this tenant. Run plugin/install.py.`);
    return String(hit['id']);
  }

  /** Start the plugin workflow through the workflow test endpoint (needs the right to test workflows). */
  async submit(workflowId: string, input: BulkInput): Promise<string> {
    const started = await this.plugin.post<{ workflowExecutionId?: string }>(
      `/v3/workflows/${encodeURIComponent(workflowId)}/test`,
      { input },
    );
    if (!started?.workflowExecutionId) throw new Error('The workflow did not start (no execution ID returned).');
    return started.workflowExecutionId;
  }

  execution(id: string): Promise<Execution> {
    return this.plugin.get<Execution>(`/v3/workflow-executions/${encodeURIComponent(id)}`);
  }

  async approvals(): Promise<GenericApproval[]> {
    return (await this.plugin.get<GenericApproval[]>('/v2025/generic-approvals?limit=250')) ?? [];
  }

  /**
   * The user's own bulk approvals with their approvers and deciders. The list call
   * leaves `approvers`/`approvedBy`/`rejectedBy` out, so the newest `max` are
   * fetched one by one (the list row is kept if a detail call fails).
   */
  async myBulkApprovals(requesterId: string, max = 30, concurrency = 5): Promise<GenericApproval[]> {
    const mine = (await this.approvals())
      .filter((a) => a.requester?.identityID === requesterId && (a.name?.[0]?.value ?? '').startsWith(APPROVAL_NAME_PREFIX))
      .sort((a, b) => (b.createdDate ?? '').localeCompare(a.createdDate ?? ''))
      .slice(0, max);
    const out = [...mine];
    let next = 0;
    const worker = async () => {
      for (let i = next++; i < mine.length; i = next++) {
        try {
          out[i] = { ...mine[i], ...(await this.plugin.get<GenericApproval>(`/v2025/generic-approvals/${encodeURIComponent(mine[i].id)}`)) };
        } catch {
          /* keep the list row */
        }
      }
    };
    await Promise.all(Array.from({ length: Math.min(concurrency, mine.length) }, worker));
    return out;
  }

  approval(id: string): Promise<GenericApproval> {
    return this.plugin.get<GenericApproval>(`/v2025/generic-approvals/${encodeURIComponent(id)}`);
  }

  /** The user's access requests, newest first: up to `max` (pages of 250, so a 600-person request fits). */
  async myAccessRequests(requesterId: string, max = 1000): Promise<AccessRequestStatus[]> {
    const rows: AccessRequestStatus[] = [];
    for (let offset = 0; offset < max; offset += 250) {
      const page = await this.plugin.get<AccessRequestStatus[]>(
        `/v3/access-request-status?requested-by=${encodeURIComponent(requesterId)}&limit=250&offset=${offset}&sorters=-created`,
      );
      rows.push(...(page ?? []));
      if (!page || page.length < 250) break;
    }
    return rows;
  }
}

