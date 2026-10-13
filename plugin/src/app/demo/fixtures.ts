/**
 * Made-up data for ?demo=… mode and for unit tests. Nothing here comes from a
 * real tenant: names, IDs and INC numbers are fictitious.
 */
import type { AccessRequestStatus, GenericApproval } from '../bulk/bulk-api.service';
import { NO_APPROVAL_LABEL } from '../bulk/rules';
import { parseRuntimeConfig, type RuntimeConfig } from '../bulk/runtime-config';

export const DEMO_ME = { id: 'd0000000000000000000000000000001', name: 'Jordan Lee', email: 'jordan.lee@example.edu' };

/** The Launcher Access profile: holding it is what lets someone submit (the launcher-denied scenario lacks it). */
export const DEMO_LAUNCHER_ACCESS = 'c0000000000000000000000000000100';

export const DEMO_CONFIG: RuntimeConfig = parseRuntimeConfig({
  prefix: 'ACME',
  mode: 'live',
  // The default: file the access requests directly as the signed-in user (the parts-* scenarios use the Launcher).
  submit: 'direct',
  launcherId: 'demo-launcher',
  launcherAccessId: DEMO_LAUNCHER_ACCESS,
  launcherAccessName: 'ACME Bulk Access Request - Launcher Access',
  incPattern: '^INC\\d{7}$',
  incMessage: 'Enter a ServiceNow incident number: INC followed by 7 digits, e.g. INC0012345.',
  incExample: 'INC0012345',
  peopleMax: null,
  partSize: 250,
  itemsMax: 25,
  catalogTypes: ['ACCESS_PROFILE', 'ROLE', 'ENTITLEMENT'],
  nameStartsWith: null,
  launcherName: 'ACME Bulk Access Request',
  temporary: { enabled: true, allow: ['duration', 'endDate'], units: ['HOURS', 'DAYS', 'WEEKS', 'MONTHS'], maxDays: null },
  // The other keys take the Python defaults (concurrency 4, useBulkEndpoint auto, maxRows 5000, deny needs a comment).
  approvals: { enabled: true, showOther: true },
  // Written by the installer: items with no approval of their own (see DEMO_CATALOG).
  noApproval: ['c0000000000000000000000000000001'],
});

const people: [string, string, string][] = [
  ['Alan Bradley', 'alan.bradley', 'Radiology'],
  ['Andrei Popescu', 'andrei.popescu', 'Radiology'],
  ['Aisha Bello', 'aisha.bello', 'Clinical Informatics'],
  ['Amelia Thornton', 'amelia.thornton', 'Radiology'],
  ['Anouk De Vries', 'anouk.devries', 'Pediatrics'],
  ['Beatriz Santos', 'beatriz.santos', 'Radiology'],
  ['Brenda Cooper', 'brenda.cooper', 'Nursing'],
  ['Bruno Marchetti', 'bruno.marchetti', 'Radiology'],
  ['Carmen Ruiz', 'carmen.ruiz', 'Oncology'],
  ['Claire Beaumont', 'claire.beaumont', 'Radiology'],
  ['Diego Alvarez', 'diego.alvarez', 'Emergency Medicine'],
  ['Andrea Kim', 'andrea.kim', 'Radiology'],
  ['Andrea Kim', 'andrea.kim2', 'Pharmacy'],
];

/** Search documents (POST /search/v1, identities). */
export const DEMO_IDENTITIES = [
  { id: DEMO_ME.id, name: 'jordan.lee', displayName: DEMO_ME.name, email: DEMO_ME.email, attributes: { department: 'IT Identity Services' } },
  ...people.map(([name, user, dept], i) => ({
    id: `d${String(i + 10).padStart(31, '0')}`,
    name: user,
    displayName: name,
    email: `${user}@example.edu`,
    attributes: { department: dept },
  })),
];

const FIRST = ['Ada', 'Ben', 'Chloe', 'Dev', 'Elena', 'Farah', 'Gabe', 'Hana', 'Ivan', 'Jade', 'Kofi', 'Lena', 'Mateo',
  'Nia', 'Omar', 'Priya', 'Quinn', 'Rosa', 'Sam', 'Tara', 'Umar', 'Vera', 'Wes', 'Xin', 'Yara', 'Zane', 'Iris', 'Leo', 'Maya', 'Noah'];
const LAST = ['Abara', 'Brandt', 'Castillo', 'Dubois', 'Eriksen', 'Fischer', 'Garcia', 'Haddad', 'Ito', 'Jensen', 'Kowalski',
  'Laine', 'Moreau', 'Nakamura', 'Okafor', 'Petrov', 'Quint', 'Rossi', 'Silva', 'Tanaka'];
const DEPTS = ['Nursing', 'Radiology', 'Pharmacy', 'Emergency Medicine', 'Oncology', 'Pediatrics'];

/**
 * A big made-up population for the "no people limit" scenarios: 600 people
 * (30 first names × 20 last names), so a request goes out as 3 parts of 250.
 * IDs are 32 hex characters like real identity IDs.
 */
export const DEMO_CROWD = LAST.flatMap((last, j) => FIRST.map((first, i) => {
  const n = j * FIRST.length + i;
  const user = `${first}.${last}`.toLowerCase();
  return {
    id: `e${String(n + 1).padStart(31, '0')}`,
    name: user,
    displayName: `${first} ${last}`,
    email: `${user}@example.edu`,
    attributes: { department: DEPTS[n % DEPTS.length] },
  };
}));

export function crowdPeople(n = DEMO_CROWD.length) {
  return DEMO_CROWD.slice(0, n).map((d) => ({ id: d.id, name: d.displayName, email: d.email, detail: d.attributes.department }));
}

export function demoPerson(displayName: string, nth = 0) {
  const doc = DEMO_IDENTITIES.filter((d) => d.displayName === displayName)[nth];
  if (!doc) throw new Error(`no demo person ${displayName}`);
  return { id: doc.id, name: doc.displayName, email: doc.email, detail: doc.attributes.department };
}

/**
 * Catalog rows (GET /requestable-objects/v1, or /entitlements/v1 for entitlements), plus each one's source.
 * The first one, ACME Bulk Test Access, has no approval of its own (DEMO_CONFIG.noApproval).
 */
export const DEMO_CATALOG: { row: Record<string, unknown>; source: string | null }[] = [
  ['ACME Bulk Test Access', 'ACCESS_PROFILE', 'ACME SaaS', 'Read-only group on the ACME SaaS demo source.'],
  ['PACS Radiologist Workstation', 'ACCESS_PROFILE', 'PACS', 'View and report on imaging studies.'],
  ['Epic - Clinician Read Only', 'ACCESS_PROFILE', 'Epic', 'Chart review without order entry.'],
  ['Radiology Department Staff', 'ROLE', null, 'Baseline access for everyone in Radiology.'],
  ['VPN - Remote Access', 'ACCESS_PROFILE', 'Active Directory', 'GlobalProtect VPN for off-site work.'],
  ['Box - Imaging Research Share', 'ENTITLEMENT', 'Box', 'Read/write on the imaging research folder.'],
  ['ServiceNow - ITIL User', 'ROLE', null, 'Work incidents and requests in ServiceNow.'],
  ['Zoom - Licensed User', 'ENTITLEMENT', 'Zoom', 'Meetings longer than 40 minutes.'],
  ['Teams - Radiology Channel', 'ENTITLEMENT', 'Entra ID', 'Membership of the Radiology Teams channel.'],
].map(([name, type, source, description], i) => ({
  row: { id: `c${String(i + 1).padStart(31, '0')}`, name, type, description, requestStatus: null },
  source,
}));

export function demoItem(name: string) {
  const hit = DEMO_CATALOG.find((c) => c.row['name'] === name)!;
  const type = hit.row['type'] as 'ACCESS_PROFILE' | 'ROLE' | 'ENTITLEMENT';
  const label = { ACCESS_PROFILE: 'Access profile', ROLE: 'Role', ENTITLEMENT: 'Entitlement' }[type];
  return {
    label: name,
    subLabel: label + (hit.source ? ` · ${hit.source}` : '')
      + (DEMO_CONFIG.noApproval.includes(String(hit.row['id'])) ? ` · ${NO_APPROVAL_LABEL}` : ''),
    description: String(hit.row['description']),
    source: hit.source,
    value: { id: String(hit.row['id']), type, name },
  };
}

/** Who already holds what (requestable-objects?identity-id=…; entitlements: identity search and access-request-status). */
export const DEMO_HELD: Record<string, Record<string, 'ASSIGNED' | 'PENDING'>> = {
  [demoPerson('Alan Bradley').id]: {
    [demoItem('ACME Bulk Test Access').value.id]: 'ASSIGNED',
    [demoItem('Zoom - Licensed User').value.id]: 'ASSIGNED',
  },
  [demoPerson('Beatriz Santos').id]: {
    [demoItem('PACS Radiologist Workstation').value.id]: 'PENDING',
    [demoItem('Box - Imaging Research Share').value.id]: 'PENDING',
  },
};

/** Who approves an item, the way the demo tenant has it set up (none for DEMO_CONFIG.noApproval). */
export function demoItemApproval(itemId: string): { scheme: string; owner: string } | null {
  if (DEMO_CONFIG.noApproval.includes(itemId)) return null;
  const type = DEMO_CATALOG.find((c) => c.row['id'] === itemId)?.row['type'];
  if (type === 'ROLE') return { scheme: 'ROLE_OWNER', owner: 'Carmen Ruiz' };
  if (type === 'ENTITLEMENT') return { scheme: 'SOURCE_OWNER', owner: 'Diego Alvarez' };
  return { scheme: 'ACCESS_PROFILE_OWNER', owner: 'Aisha Bello' };
}

// ── The Approvals tab: item approvals waiting for the demo user ─────────────────
/** The Launcher workflow's owner: SailPoint files every request of a Launcher run in its name. */
export const DEMO_SERVICE = { id: 'd0000000000000000000000000000002', name: 'ACME Bulk Service' };
/** A colleague in the same governance group, who sometimes decides first (approvals-partial). */
export const DEMO_COLLEAGUE = { id: 'd0000000000000000000000000000003', name: 'Elliot Reid' };

/** Two bulk requests and a few ordinary approvals (the "Other" group). */
export const DEMO_PENDING_INCS = { big: 'INC0048502', small: 'INC0048466' } as const;

function pending(n: number, person: { id: string; name: string }, item: string, created: string, due: string,
                 scheme: string, comment: string | null, removalDate: string | null = null,
                 requester: { id: string; name: string } = DEMO_SERVICE): GenericApproval {
  const it = demoItem(item);
  return {
    id: `b0000000-0000-4000-8000-${String(n).padStart(12, '0')}`,
    type: 'ACCESS_REQUEST_APPROVAL',
    status: 'PENDING',
    name: [{ value: item, locale: 'en-US' }],
    createdDate: created,
    dueDate: due,
    requester: { identityID: requester.id, name: requester.name },
    requestee: { identityID: person.id, name: person.name },
    requestedTarget: { id: it.value.id, name: item, targetType: it.value.type, requestType: 'GRANT_ACCESS', removalDate },
    approvalConfig: { serialChain: [{ tier: 1, identityType: scheme }] },
    // The real list leaves assignedTo out when it includes comments.
    comments: comment ? [{ comment, author: { name: requester.name } }] : [],
  };
}

/** The bulk comment on every request of a bulk request (CONTRACTS §4). */
const bulkComment = (inc: string, requester: string, access: string, why: string) =>
  `${inc} | Bulk access request by ${requester} | ${access} | ${why}`;
/** The same, as the old Launcher (with its bulk approver) wrote it: still read, so older requests keep grouping. */
const oldBulkComment = (inc: string, requester: string, approver: string, access: string, why: string) =>
  `${inc} | Bulk access request by ${requester} | Approved by ${approver} | ${access} | ${why}`;

export const DEMO_PENDING: GenericApproval[] = [
  // INC0048502: 150 people × 2 access profiles the demo user owns = 300 approvals, filed directly by Carmen Ruiz.
  ...crowdPeople(150).flatMap((p, i) => ['PACS Radiologist Workstation', 'Epic - Clinician Read Only'].map((item, j) =>
    pending(1 + i * 2 + j, p, item, `2026-10-07T13:15:${String(i % 60).padStart(2, '0')}Z`, '2026-10-14T13:15:00Z',
      'ACCESS_PROFILE_OWNER', bulkComment(DEMO_PENDING_INCS.big, 'Carmen Ruiz', 'Permanent',
        'Hospital-wide move to the new PACS on 14 Oct | cutover weekend'), null, demoPerson('Carmen Ruiz')))),
  // INC0048466: 40 people the demo user manages, VPN for 30 days, from an older Launcher run (old comment form).
  ...crowdPeople(190).slice(150).map((p, i) => pending(301 + i, p, 'VPN - Remote Access', '2026-10-06T09:40:00Z',
    '2026-10-13T09:40:00Z', 'MANAGER', oldBulkComment(DEMO_PENDING_INCS.small, 'Diego Alvarez', 'Carmen Ruiz',
      'Temporary: 30 days', 'Remote reading during the ward refurbishment'), '2026-11-05T09:40:00Z')),
  // Ordinary requests, not from a bulk request: the "Other" group.
  pending(401, demoPerson('Brenda Cooper'), 'Zoom - Licensed User', '2026-10-05T08:00:00Z', '2026-10-12T08:00:00Z',
    'ENTITLEMENT_OWNER', 'Need longer meetings for the nursing huddle'),
  pending(402, demoPerson('Anouk De Vries'), 'Box - Imaging Research Share', '2026-10-05T10:30:00Z', '2026-10-12T10:30:00Z',
    'SOURCE_OWNER', null),
  pending(403, demoPerson('Diego Alvarez'), 'ServiceNow - ITIL User', '2026-10-04T16:00:00Z', '2026-10-11T16:00:00Z',
    'ROLE_OWNER', 'INC0047001 please'),
];

/** One access-request-status row the demo user filed (the My bulk requests tab). */
export function demoRequest(person: { id: string; name: string }, item: string, state: string, created: string,
                            comment: string, removeDate: string | null = null,
                            approval: { status: string; owner?: string } | null = null): AccessRequestStatus {
  const it = demoItem(item);
  const scheme = demoItemApproval(it.value.id);
  return {
    id: it.value.id, name: item, type: it.value.type, state, created,
    requestedFor: { id: person.id, name: person.name },
    requester: { id: DEMO_ME.id, name: DEMO_ME.name },
    requesterComment: { comment },
    removeDate,
    approvalDetails: scheme && approval
      ? [{ status: approval.status, scheme: scheme.scheme, currentOwner: { name: approval.owner ?? scheme.owner } }] : [],
  };
}

const PACS = 'New PACS rollout';
const approved = { status: 'APPROVED' };
const waiting = { status: 'PENDING' };

export const DEMO_REQUESTS: AccessRequestStatus[] = [
  // INC0048120: 500 people, VPN for 30 days, approved by each person's manager; a few still wait.
  ...crowdPeople(500).map((p, i) => demoRequest(p, 'VPN - Remote Access', i % 50 === 7 ? 'EXECUTING' : 'REQUEST_COMPLETED',
    `2026-10-07T13:${i < 250 ? '15' : '16'}:30Z`,
    bulkComment('INC0048120', DEMO_ME.name, 'Temporary: 30 days', 'Remote reading during the PACS cutover'),
    i < 250 ? '2026-11-06T13:15:30Z' : '2026-11-06T13:16:30Z',
    i % 50 === 7 ? { status: 'PENDING', owner: 'Carmen Ruiz' } : approved)),
  // INC0047950: an access profile (its owner approves) and a role, for 3 people.
  ...['Amelia Thornton', 'Bruno Marchetti', 'Claire Beaumont'].flatMap((name, i) => [
    demoRequest(demoPerson(name), 'PACS Radiologist Workstation', i === 2 ? 'EXECUTING' : 'REQUEST_COMPLETED',
      '2026-10-06T10:03:00Z', bulkComment('INC0047950', DEMO_ME.name, 'Permanent', PACS), null, i === 2 ? waiting : approved),
    demoRequest(demoPerson(name), 'Radiology Department Staff', 'REQUEST_COMPLETED', '2026-10-06T10:03:00Z',
      bulkComment('INC0047950', DEMO_ME.name, 'Permanent', PACS), null, approved),
  ]),
  // INC0047711: the entitlement's source owner said no.
  ...['Diego Alvarez', 'Brenda Cooper'].map((name) => demoRequest(demoPerson(name), 'Box - Imaging Research Share', 'REJECTED',
    '2026-10-03T16:45:00Z', bulkComment('INC0047711', DEMO_ME.name, 'Permanent', 'Research share for the imaging study'),
    null, { status: 'REJECTED' })),
  // An ordinary access request without an INC: the page ignores it.
  { id: 'x', name: 'Zoom - Licensed User', type: 'ENTITLEMENT', state: 'REQUEST_COMPLETED', created: '2026-10-01T10:00:00Z',
    requestedFor: { id: DEMO_ME.id, name: DEMO_ME.name }, requester: { id: DEMO_ME.id }, requesterComment: { comment: 'need it' } },
];
