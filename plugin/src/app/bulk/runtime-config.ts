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

export interface RuntimeConfig {
  /** Names every object install.py created, e.g. "ACME". */
  prefix: string;
  /** "dry-run" (approve, but request nothing) or "live". */
  mode: 'dry-run' | 'live';
  /** The plugin workflow, found by name when workflowId is empty. */
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
  /** Shown in the banner: where people without ORG_ADMIN should go instead. */
  launcherName: string;
  temporary: TemporaryConfig;
}

export const DEFAULT_CONFIG: RuntimeConfig = {
  prefix: '',
  mode: 'dry-run',
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
};

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

/** Fetch the config shipped next to index.html (relative URL: the bundle is served from a CDN path). */
export async function loadRuntimeConfig(fetchFn: typeof fetch = fetch): Promise<RuntimeConfig> {
  const resp = await fetchFn('bulk-access.config.json', { cache: 'no-store' });
  if (!resp.ok) {
    throw new RuntimeConfigError(`Could not load bulk-access.config.json (HTTP ${resp.status}). Run plugin/install.py.`);
  }
  return parseRuntimeConfig(await resp.json());
}
