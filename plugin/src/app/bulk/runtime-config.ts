/**
 * The plugin's runtime config: public/bulk-access.config.json.
 *
 * plugin/install.py writes it from the tenant config (config/<tenant>.json), so
 * the same bundle works in any tenant. Nothing tenant-specific is compiled in.
 * The committed copy holds neutral defaults.
 */
export type ItemType = 'ACCESS_PROFILE' | 'ROLE' | 'ENTITLEMENT';

export const ITEM_TYPES: readonly ItemType[] = ['ACCESS_PROFILE', 'ROLE', 'ENTITLEMENT'];

/** How a requester may make access temporary (config.TEMPORARY_MODES). */
export type TemporaryMode = 'duration' | 'endDate';
export const TEMPORARY_MODES: readonly TemporaryMode[] = ['duration', 'endDate'];

/** Units of a temporary-access duration (config.DURATION_UNITS). */
export type DurationUnit = 'HOURS' | 'DAYS' | 'WEEKS' | 'MONTHS';
export const DURATION_UNIT_NAMES: readonly DurationUnit[] = ['HOURS', 'DAYS', 'WEEKS', 'MONTHS'];

/**
 * SailPoint's workflow Loop rejects more than 250 iterations (config.LOOP_MAX, verified
 * live), so one workflow run, and so one approval, covers at most this many people.
 */
export const LOOP_MAX = 250;

export interface TemporaryConfig {
  enabled: boolean;
  allow: TemporaryMode[];
  units: DurationUnit[];
  /** null = no cap. */
  maxDays: number | null;
}

/**
 * How the page submits a request (config.PLUGIN_SUBMIT_MODES, `plugin.submit`):
 *  - launcher: it starts the Launcher deployment's Launcher and submits its form as the signed-in user, so anyone
 *    holding the Launcher Access profile can submit (CONTRACTS §9);
 *  - test-endpoint: it starts the disabled plugin workflow through the workflow test endpoint (ORG_ADMIN only).
 */
type SubmitMode = 'launcher' | 'test-endpoint';
const SUBMIT_MODES: readonly SubmitMode[] = ['launcher', 'test-endpoint'];
export const MSG_SUBMIT = 'submit must be "launcher" or "test-endpoint".';
export const MSG_LAUNCHER_ID = 'This page doesn\'t know which Launcher to use yet (launcherId is missing): run plugin/install.py, '
  + 'which writes it when submit is "launcher".';

/** How the Approvals tab sends decisions (config.BULK_ENDPOINT_MODES). */
export type BulkEndpointMode = 'auto' | 'always' | 'never';
export const BULK_ENDPOINT_MODES: readonly BulkEndpointMode[] = ['auto', 'always', 'never'];
export const APPROVALS_CONCURRENCY_MAX = 8;
export const APPROVALS_MAX_ROWS_RANGE = [250, 20000] as const;

/** The Approvals tab: item approvers decide one bulk request's approvals at once (config `approvals`). */
export interface ApprovalsConfig {
  /** config.plugin_approvals_enabled (false when the file comes from an older install). */
  enabled: boolean;
  /** Decisions sent at a time, 1..8. */
  concurrency: number;
  useBulkEndpoint: BulkEndpointMode;
  /** Most pending approvals loaded, 250..20000. */
  maxRows: number;
  /** Also list approvals that aren't from a bulk request. */
  showOther: boolean;
  denyCommentRequired: boolean;
}

export interface RuntimeConfig {
  /** Names every object install.py created, e.g. "ACME". */
  prefix: string;
  /** "dry-run" (approve, but request nothing) or "live". */
  mode: 'dry-run' | 'live';
  /** How the page submits (see SubmitMode). A file without it comes from an older install: test-endpoint. */
  submit: SubmitMode;
  /** The Launcher to start in launcher mode (a non-admin can't list launchers, so install.py writes its ID). */
  launcherId: string | null;
  /** The access profile that lets people use the Launcher: what a user without it is told to request. */
  launcherAccessName: string;
  /** The plugin workflow (test-endpoint mode), found by name when workflowId is empty. */
  workflowName: string;
  workflowId: string | null;
  incPattern: string;
  incMessage: string;
  incExample: string;
  /** null = no limit. */
  peopleMax: number | null;
  /** People per workflow run (one approval each), 1..LOOP_MAX. */
  partSize: number;
  itemsMax: number;
  catalogTypes: ItemType[];
  nameStartsWith: string | null;
  /** The Launcher's name: where people without ORG_ADMIN go instead in test-endpoint mode. */
  launcherName: string;
  temporary: TemporaryConfig;
  approvals: ApprovalsConfig;
}

export const DEFAULT_CONFIG: RuntimeConfig = {
  prefix: '',
  mode: 'dry-run',
  submit: 'test-endpoint',
  launcherId: null,
  launcherAccessName: 'Bulk Access Request - Launcher Access',
  workflowName: 'Bulk Access Request (Plugin)',
  workflowId: null,
  incPattern: '^INC\\d{7}$',
  incMessage: 'Enter a ServiceNow incident number: INC followed by 7 digits, e.g. INC0012345.',
  incExample: 'INC0012345',
  peopleMax: null,
  partSize: LOOP_MAX,
  itemsMax: 25,
  catalogTypes: [...ITEM_TYPES],
  nameStartsWith: null,
  launcherName: 'Bulk Access Request',
  // Off when the file has no `temporary` block: that file comes from an older install,
  // whose workflow would ignore the duration and grant the access permanently.
  temporary: { enabled: false, allow: [...TEMPORARY_MODES], units: [...DURATION_UNIT_NAMES], maxDays: null },
  // Off when the file has no `approvals` block: it comes from an install that predates the Approvals tab.
  approvals: { enabled: false, concurrency: 4, useBulkEndpoint: 'auto', maxRows: 5000, showOther: false, denyCommentRequired: true },
};

/** Problem messages for the `approvals` block: the same text as core/bulkaccess/config.py. */
export const MSG_APPROVALS_OBJECT = '`approvals` must be an object.';
export const MSG_APPROVALS_CONCURRENCY = `\`approvals.concurrency\` must be a whole number between 1 and ${APPROVALS_CONCURRENCY_MAX}.`;
export const MSG_APPROVALS_BULK_ENDPOINT = '`approvals.useBulkEndpoint` must be "auto", "always" or "never".';
export const MSG_APPROVALS_MAX_ROWS =
  `\`approvals.maxRows\` must be a whole number between ${APPROVALS_MAX_ROWS_RANGE[0]} and ${APPROVALS_MAX_ROWS_RANGE[1]}.`;
export function msgApprovalsFlag(key: string): string {
  return `\`approvals.${key}\` must be true or false.`;
}

export class RuntimeConfigError extends Error {}

/** Merge a parsed JSON object over the defaults, rejecting values that would break the page. */
export function parseRuntimeConfig(raw: unknown): RuntimeConfig {
  const data = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  const cfg: RuntimeConfig = { ...DEFAULT_CONFIG };
  const str = (key: keyof RuntimeConfig) => {
    const v = data[key];
    return typeof v === 'string' ? v : undefined;
  };
  const num = (key: keyof RuntimeConfig) => {
    const v = data[key];
    return typeof v === 'number' && Number.isFinite(v) ? v : undefined;
  };

  cfg.prefix = str('prefix') ?? cfg.prefix;
  cfg.mode = data['mode'] === 'live' ? 'live' : 'dry-run';
  if (data['submit'] !== undefined && data['submit'] !== null) {
    if (!SUBMIT_MODES.includes(data['submit'] as SubmitMode)) throw new RuntimeConfigError(MSG_SUBMIT);
    cfg.submit = data['submit'] as SubmitMode;
  }
  // Launcher mode without an ID (the committed neutral file) still loads, so the page can be browsed; submitting
  // then stops with MSG_LAUNCHER_ID. plugin/install.py always writes the ID in launcher mode.
  cfg.launcherId = str('launcherId') || null;
  cfg.launcherAccessName = str('launcherAccessName') || cfg.launcherAccessName;
  cfg.workflowName = str('workflowName') || cfg.workflowName;
  cfg.workflowId = str('workflowId') || null;
  cfg.incPattern = str('incPattern') || cfg.incPattern;
  cfg.incMessage = str('incMessage') || cfg.incMessage;
  cfg.incExample = str('incExample') || cfg.incExample;
  cfg.peopleMax = 'peopleMax' in data ? optionalWhole(data['peopleMax'], 'peopleMax') : cfg.peopleMax;
  if ('partSize' in data) {
    const size = data['partSize'];
    if (!isWhole(size) || size < 1 || size > LOOP_MAX) {
      throw new RuntimeConfigError(`partSize must be between 1 and ${LOOP_MAX} (SailPoint's workflow loop limit).`);
    }
    cfg.partSize = size;
  }
  cfg.itemsMax = num('itemsMax') ?? cfg.itemsMax;
  cfg.temporary = parseTemporary(data['temporary']);
  cfg.approvals = parseApprovals(data['approvals']);
  cfg.nameStartsWith = str('nameStartsWith') || null;
  cfg.launcherName = str('launcherName') || cfg.launcherName;
  const types = Array.isArray(data['catalogTypes']) ? (data['catalogTypes'] as unknown[]) : null;
  if (types) {
    const bad = types.filter((t) => !ITEM_TYPES.includes(t as ItemType));
    if (bad.length) {
      throw new RuntimeConfigError(`catalogTypes may only contain ${ITEM_TYPES.join(', ')} (got ${bad.join(', ')}).`);
    }
    cfg.catalogTypes = types as ItemType[];
  }

  try {
    new RegExp(cfg.incPattern);
  } catch {
    throw new RuntimeConfigError(`incPattern is not a valid regular expression: ${cfg.incPattern}`);
  }
  if (!new RegExp(cfg.incPattern).test(cfg.incExample)) {
    throw new RuntimeConfigError(`incExample (${cfg.incExample}) does not match incPattern.`);
  }
  if (cfg.itemsMax < 1 || cfg.itemsMax > 25) {
    throw new RuntimeConfigError('itemsMax must be between 1 and 25 (SailPoint\'s per-request limit).');
  }
  return cfg;
}

function isWhole(v: unknown): v is number {
  return typeof v === 'number' && Number.isInteger(v);
}

/** null (no limit) or a whole number of at least 1. */
function optionalWhole(v: unknown, name: string): number | null {
  if (v === null) return null;
  if (!isWhole(v) || v < 1) throw new RuntimeConfigError(`${name} must be null (no limit) or a whole number of at least 1.`);
  return v;
}

function parseTemporary(raw: unknown): TemporaryConfig {
  const fallback = DEFAULT_CONFIG.temporary;
  if (raw === undefined || raw === null) return { ...fallback, allow: [...fallback.allow], units: [...fallback.units] };
  if (typeof raw !== 'object' || Array.isArray(raw)) throw new RuntimeConfigError('temporary must be an object.');
  const t = raw as Record<string, unknown>;
  if (t['enabled'] !== undefined && typeof t['enabled'] !== 'boolean') {
    throw new RuntimeConfigError('temporary.enabled must be true or false.');
  }
  const list = <T extends string>(key: string, allowed: readonly T[]): T[] => {
    const v = t[key];
    if (v === undefined || v === null) return [...allowed];
    const bad = Array.isArray(v) ? v.filter((x) => !allowed.includes(x as T)) : [v];
    if (bad.length) {
      throw new RuntimeConfigError(`temporary.${key} may only contain ${allowed.join(', ')} (got ${bad.join(', ')}).`);
    }
    return [...new Set(v as T[])];
  };
  return {
    // A temporary block is only written by installs whose workflow honours it (Python's default is on).
    enabled: t['enabled'] === undefined ? true : (t['enabled'] as boolean),
    allow: list('allow', TEMPORARY_MODES),
    units: list('units', DURATION_UNIT_NAMES),
    maxDays: t['maxDays'] === undefined ? null : optionalWhole(t['maxDays'], 'temporary.maxDays'),
  };
}

/** The `approvals` block (config._approvals): missing → off; missing keys → the Python defaults. */
function parseApprovals(raw: unknown): ApprovalsConfig {
  if (raw === undefined || raw === null) return { ...DEFAULT_CONFIG.approvals };
  if (typeof raw !== 'object' || Array.isArray(raw)) throw new RuntimeConfigError(MSG_APPROVALS_OBJECT);
  const a = raw as Record<string, unknown>;
  const flag = (key: string, fallback: boolean): boolean => {
    const v = a[key] === undefined ? fallback : a[key];
    if (typeof v !== 'boolean') throw new RuntimeConfigError(msgApprovalsFlag(key));
    return v;
  };
  // Python's flags are checked first, in this order, so a file with several problems reports the same one.
  const enabled = flag('enabled', true);
  const showOther = flag('showOther', false);
  const denyCommentRequired = flag('denyCommentRequired', true);
  const concurrency = a['concurrency'] === undefined ? DEFAULT_CONFIG.approvals.concurrency : a['concurrency'];
  if (!isWhole(concurrency) || concurrency < 1 || concurrency > APPROVALS_CONCURRENCY_MAX) {
    throw new RuntimeConfigError(MSG_APPROVALS_CONCURRENCY);
  }
  const endpoint = a['useBulkEndpoint'] === undefined ? DEFAULT_CONFIG.approvals.useBulkEndpoint : a['useBulkEndpoint'];
  if (!BULK_ENDPOINT_MODES.includes(endpoint as BulkEndpointMode)) throw new RuntimeConfigError(MSG_APPROVALS_BULK_ENDPOINT);
  const maxRows = a['maxRows'] === undefined ? DEFAULT_CONFIG.approvals.maxRows : a['maxRows'];
  const [low, high] = APPROVALS_MAX_ROWS_RANGE;
  if (!isWhole(maxRows) || maxRows < low || maxRows > high) throw new RuntimeConfigError(MSG_APPROVALS_MAX_ROWS);
  return { enabled, concurrency, useBulkEndpoint: endpoint as BulkEndpointMode, maxRows, showOther, denyCommentRequired };
}

/** Fetch the config shipped next to index.html (relative URL: the bundle is served from a CDN path). */
export async function loadRuntimeConfig(fetchFn: typeof fetch = fetch): Promise<RuntimeConfig> {
  const resp = await fetchFn('bulk-access.config.json', { cache: 'no-store' });
  if (!resp.ok) {
    throw new RuntimeConfigError(`Could not load bulk-access.config.json (HTTP ${resp.status}). Run plugin/install.py.`);
  }
  return parseRuntimeConfig(await resp.json());
}
