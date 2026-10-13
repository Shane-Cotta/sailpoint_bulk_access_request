/**
 * Business rules, ported from core/bulkaccess/rules.py so the page refuses
 * exactly what the Launcher form and the workflow refuse. rules.spec.ts mirrors
 * core/tests/test_core.py; keep the two in step.
 */
import type { DurationUnit, ItemType, RuntimeConfig, TemporaryMode } from './runtime-config';

export const TYPE_LABELS: Record<ItemType, string> = {
  ACCESS_PROFILE: 'Access profile',
  ROLE: 'Role',
  ENTITLEMENT: 'Entitlement',
};

/**
 * The justification travels in every access request's comment. SailPoint accepts long comments (4,000 characters
 * verified live); this keeps them readable for the item approvers (rules.JUSTIFICATION_MAX).
 */
export const JUSTIFICATION_MAX = 1000;
export const MSG_JUSTIFICATION = 'Enter a business justification.';
export const MSG_JUSTIFICATION_LONG = `Keep the justification to ${JUSTIFICATION_MAX} characters or fewer.`;

/**
 * Shown next to catalog items whose own approval setup has no approver (install.py works it out: `noApproval`):
 * SailPoint grants them as soon as they're requested (rules.NO_APPROVAL_LABEL).
 */
export const NO_APPROVAL_LABEL = 'No approval: granted right away';

/** One access item as an access request (and the Launcher workflow's Manage Access step) needs it. */
export interface AccessItem {
  id: string;
  type: ItemType;
  name: string;
}

type RuleConfig = Pick<RuntimeConfig, 'incPattern' | 'incMessage' | 'peopleMax' | 'itemsMax' | 'catalogTypes'>;

export function incIsValid(cfg: Pick<RuntimeConfig, 'incPattern'>, value: string | null | undefined): boolean {
  return !!value && new RegExp(cfg.incPattern).test(value.trim());
}

export interface RequestDraft {
  people: string[];
  items: { id: string; type: string }[];
  inc: string;
  justification: string;
}

/**
 * Everything wrong with a bulk request, as sentences (empty list = OK). Same order as rules.py. Each item is then
 * approved the way SailPoint has it set up; there is no approval for the request as a whole.
 */
export function validateRequest(cfg: RuleConfig, draft: RequestDraft): string[] {
  const problems: string[] = [];
  const { people, items, inc, justification } = draft;
  if (!people.length) problems.push('Choose at least one person.');
  // No upper bound unless the config sets one: big lists go out in parts (splitIntoParts).
  if (cfg.peopleMax !== null && new Set(people).size > cfg.peopleMax) problems.push(`Choose at most ${cfg.peopleMax} people.`);
  if (!items.length) problems.push('Choose at least one access item.');
  if (items.length > cfg.itemsMax) problems.push(`Choose at most ${cfg.itemsMax} access items.`);
  if (items.some((i) => !cfg.catalogTypes.includes(i.type as ItemType))) {
    problems.push('One of the chosen items is not an allowed type.');
  }
  if (!incIsValid(cfg, inc)) problems.push(cfg.incMessage);
  const text = (justification ?? '').trim();
  if (!text) problems.push(MSG_JUSTIFICATION);
  else if (text.length > JUSTIFICATION_MAX) problems.push(MSG_JUSTIFICATION_LONG);
  return problems;
}

export function clip(text: string, limit: number): string {
  const flat = String(text).split(/\s+/).filter(Boolean).join(' ');
  return flat.length <= limit ? flat : flat.slice(0, limit - 1) + '…';
}

/** A catalog entry as the Items step shows it. */
export interface CatalogOption {
  label: string;
  subLabel: string;
  description: string;
  source: string | null;
  value: AccessItem;
}

/**
 * Where the catalog comes from (CONTRACTS §8, rules.py): /requestable-objects/v1 only lists
 * access profiles and roles (ENTITLEMENT alone is a 400, next to another type it is silently
 * dropped), so requestable entitlements come from /entitlements/v1.
 */
export const REQUESTABLE_OBJECT_TYPES: readonly ItemType[] = ['ACCESS_PROFILE', 'ROLE'];

/** The configured types /requestable-objects/v1 can list (empty: don't call it). */
export function requestableObjectTypes(cfg: Pick<RuntimeConfig, 'catalogTypes'>): ItemType[] {
  return cfg.catalogTypes.filter((t) => REQUESTABLE_OBJECT_TYPES.includes(t));
}

/** A string literal for a SailPoint `filters` expression (rules.filter_quote). */
export function filterQuote(value: string): string {
  return `"${value.replace(/["\\]/g, (c) => `\\${c}`)}"`;
}

/** The /entitlements/v1 filter for the catalog's entitlements (null: not offered). */
export function entitlementFilter(cfg: Pick<RuntimeConfig, 'catalogTypes' | 'nameStartsWith'>): string | null {
  if (!cfg.catalogTypes.includes('ENTITLEMENT')) return null;
  return 'requestable eq true' + (cfg.nameStartsWith ? ` and name sw ${filterQuote(cfg.nameStartsWith)}` : '');
}

/**
 * Catalog rows -> catalog options, filtered by type and name prefix (rules.catalog_options). The profile that
 * grants this tool (the Launcher Access profile) is never offered. Rows from /entitlements/v1 carry no `type`; the
 * caller tags them ENTITLEMENT. Items in `noApproval` say so in their sub-label, so whoever picks them sees they're
 * granted at once.
 */
export function catalogOptions(
  cfg: Pick<RuntimeConfig, 'catalogTypes' | 'nameStartsWith' | 'noApproval' | 'launcherAccessName'>,
  requestable: Iterable<Record<string, unknown>>,
  sources: Record<string, string> = {},
): CatalogOption[] {
  const options: CatalogOption[] = [];
  const grantedAtOnce = new Set(cfg.noApproval);
  for (const obj of requestable) {
    const kind = obj['type'] as ItemType;
    const id = String(obj['id']);
    const name = String(obj['name'] || id);
    if (name === cfg.launcherAccessName) continue;
    if (!cfg.catalogTypes.includes(kind)) continue;
    if (cfg.nameStartsWith && !name.startsWith(cfg.nameStartsWith)) continue;
    const embedded = obj['source'] as { name?: string } | undefined;
    const source = sources[id] ?? (embedded && typeof embedded === 'object' ? embedded.name ?? null : null);
    options.push({
      label: name,
      subLabel: TYPE_LABELS[kind] + (source ? ` · ${source}` : '') + (grantedAtOnce.has(id) ? ` · ${NO_APPROVAL_LABEL}` : ''),
      description: typeof obj['description'] === 'string' ? (obj['description'] as string) : '',
      source,
      value: { id, type: kind, name },
    });
  }
  return options.sort((a, b) => {
    const byName = a.label.toLowerCase().localeCompare(b.label.toLowerCase());
    return byName || a.value.type.localeCompare(b.value.type);
  });
}

/**
 * Find the INC number in free text (an access request comment such as
 * "INC0012345 | Bulk access request by …"). Each word is tested against the configured
 * pattern, so anchors like ^…$ keep working.
 */
export function extractInc(cfg: Pick<RuntimeConfig, 'incPattern'>, text: string | null | undefined): string | null {
  if (!text) return null;
  const re = new RegExp(cfg.incPattern);
  for (const word of text.split(/[\s|:,;()[\]{}]+/)) {
    if (word && re.test(word)) return word;
  }
  return null;
}

// ── The item comment (rules.bulk_comment / rules.parse_bulk_comment, CONTRACTS §4) ──
/**
 * Every access request of a bulk request carries one comment (bulkComment here, the Launcher workflow in
 * definitions.py): "<INC> | Bulk access request by <requester> | <access label> | <justification>". Requests filed
 * while bulk requests had an approver carry "Approved by <approver>" after the requester; they are still read.
 */
export const COMMENT_SEPARATOR = ' | ';
const REQUESTER_LABEL = 'Bulk access request by';

/** A bulk request's item comment, read back; `approver` only on comments from before the approver was removed. */
export interface BulkComment {
  inc: string;
  requester: string;
  approver: string | null;
  accessLabel: string;
  justification: string;
}

// Three fields without "|", then the justification (everything after the 3rd separator, "|" allowed); the old form
// has four. The same patterns as rules.py.
const BULK_COMMENT = /^\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([\s\S]*?)\s*$/;
const OLD_BULK_COMMENT = /^\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([\s\S]*?)\s*$/;
const REQUESTER = /^Bulk\s+access\s+request\s+by\s+(\S[\s\S]*)$/;
const OLD_APPROVER_LABEL = /^Approved\s+by\b/;
const OLD_APPROVER = /^Approved\s+by\s+(\S[\s\S]*)$/;

/** The comment every access request of a bulk request carries (parseBulkComment reads it back). */
export function bulkComment(inc: string, requester: string, accessLabel: string, justification: string): string {
  return [inc.trim(), `${REQUESTER_LABEL} ${requester}`, accessLabel, justification.trim()].join(COMMENT_SEPARATOR);
}

/**
 * The parts of a bulk request's item comment, or null for any other comment. The INC must match the configured
 * pattern; requester and access label must not be empty; the justification may be empty and may itself contain
 * " | ". Extra whitespace is tolerated.
 */
export function parseBulkComment(cfg: Pick<RuntimeConfig, 'incPattern'>, text: unknown): BulkComment | null {
  if (typeof text !== 'string') return null;
  let inc: string, by: string, label: string, justification: string;
  let approver: string | null = null;
  const old = OLD_BULK_COMMENT.exec(text);
  if (old && OLD_APPROVER_LABEL.test(old[3])) {   // the old form: its approver must be named
    const named = OLD_APPROVER.exec(old[3]);
    if (!named) return null;
    [, inc, by, , label, justification] = old;
    approver = named[1];
  } else {
    const m = BULK_COMMENT.exec(text);
    if (!m) return null;
    [, inc, by, label, justification] = m;
  }
  const requester = REQUESTER.exec(by);
  if (!incIsValid(cfg, inc) || !requester || !label) return null;
  return { inc, requester: requester[1], approver, accessLabel: label, justification };
}

// ── Parts (rules.split_into_parts / rules.part_label) ─────────────────────────
/**
 * The de-duplicated list, in order, cut into chunks of `partSize`: one Launcher run each (SailPoint's Loop takes
 * at most 250 items), or one direct access request each (at most 10 people).
 * `key` says what makes two entries the same person (default: the value itself).
 */
export function splitIntoParts<T>(people: readonly T[], partSize: number, key: (p: T) => unknown = (p) => p): T[][] {
  if (!Number.isInteger(partSize) || partSize < 1) throw new RangeError('partSize must be a whole number of at least 1.');
  const seen = new Set<unknown>();
  const unique = people.filter((p) => {
    const k = key(p);
    return !seen.has(k) && !!seen.add(k);
  });
  const parts: T[][] = [];
  for (let i = 0; i < unique.length; i += partSize) parts.push(unique.slice(i, i + partSize));
  return parts;
}

/** "" for a single part, else " (i/n)" (leading space, 1-based): appended to the INC on a Launcher run's emails. */
export function partLabel(i: number, n: number): string {
  return n === 1 ? '' : ` (${i}/${n})`;
}

// ── Temporary access (rules.py, CONTRACTS §2) ─────────────────────────────────
/** Manage Access v2 `removeDuration` suffixes (config.DURATION_UNITS). */
export const DURATION_UNITS: Record<DurationUnit, string> = { HOURS: 'h', DAYS: 'd', WEEKS: 'w', MONTHS: 'M' };
/** Upper bound in days of one unit, for the maxDays cap (config.UNIT_MAX_DAYS). */
export const UNIT_MAX_DAYS: Record<DurationUnit, number> = { HOURS: 1 / 24, DAYS: 1, WEEKS: 7, MONTHS: 31 };
const UNIT_WORDS: Record<DurationUnit, string> = { HOURS: 'hour', DAYS: 'day', WEEKS: 'week', MONTHS: 'month' };

/** What the requester chose: permanent access, a duration, or an end date (YYYY-MM-DD, plugin only). */
export type AccessChoice =
  | { mode: 'permanent' }
  | { mode: 'duration'; n: number | null; unit: DurationUnit | null }
  | { mode: 'endDate'; date: string };

export const PERMANENT: AccessChoice = { mode: 'permanent' };

const DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

/** YYYY-MM-DD of `now` in the user's local time. */
export function localDate(now: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

/** 23:59:59 local time on that day, or null if `date` isn't a real YYYY-MM-DD. */
export function endOfDay(date: string): Date | null {
  const m = DATE.exec(date);
  if (!m) return null;
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const end = new Date(y, mo - 1, d, 23, 59, 59);
  return end.getFullYear() === y && end.getMonth() === mo - 1 && end.getDate() === d ? end : null;
}

/** Whole hours from `now` until the end of `date` (local time), rounded up. */
export function endDateHours(date: string, now: Date = new Date()): number {
  const end = endOfDay(date);
  if (!end) throw new RangeError(`Not a date: ${date}`);
  return Math.ceil((end.getTime() - now.getTime()) / 3_600_000);
}

/** The modes this installation offers (none when temporary access is off). */
export function temporaryModes(cfg: Pick<RuntimeConfig, 'temporary'>): TemporaryMode[] {
  return cfg.temporary.enabled ? cfg.temporary.allow : [];
}

/** Everything wrong with an access choice, as sentences (empty list = OK). Same text as rules.py. */
export function validateAccess(cfg: Pick<RuntimeConfig, 'temporary'>, choice: AccessChoice, now: Date = new Date()): string[] {
  if (choice.mode === 'permanent') return [];
  const t = cfg.temporary;
  if (!temporaryModes(cfg).includes(choice.mode)) return ["Temporary access isn't available."];
  const cap = (days: number) => (t.maxDays !== null && days > t.maxDays ? [`Temporary access can last at most ${t.maxDays} days.`] : []);
  if (choice.mode === 'duration') {
    const problems: string[] = [];
    const wholeN = typeof choice.n === 'number' && Number.isInteger(choice.n) && choice.n >= 1;
    const knownUnit = !!choice.unit && t.units.includes(choice.unit);
    if (!wholeN) problems.push('Enter the duration as a whole number of 1 or more.');
    if (!knownUnit) problems.push('Choose a unit for the duration.');
    if (wholeN && knownUnit) problems.push(...cap(choice.n! * UNIT_MAX_DAYS[choice.unit!]));
    return problems;
  }
  if (!endOfDay(choice.date) || choice.date <= localDate(now)) return ['Choose an end date after today.'];
  return cap(endDateHours(choice.date, now) / 24);
}

/** Manage Access v2 `removeDuration`: "" (permanent), "30d", or "<hours>h" for an end date. Validate first. */
export function removeDuration(choice: AccessChoice, now: Date = new Date()): string {
  switch (choice.mode) {
    case 'permanent': return '';
    case 'duration': return `${choice.n}${DURATION_UNITS[choice.unit!]}`;
    case 'endDate': return `${endDateHours(choice.date, now)}h`;
  }
}

/**
 * When a direct request's access is removed (its `removeDate`, an ISO instant), or null for permanent access.
 * A duration counts from `now` the way Manage Access counts it (a month is a calendar month, clamped to the
 * month's last day); an end date is the end of that day, local time. Validate first.
 */
export function removeDate(choice: AccessChoice, now: Date = new Date()): string | null {
  switch (choice.mode) {
    case 'permanent': return null;
    case 'endDate': return endOfDay(choice.date)!.toISOString();
    case 'duration': {
      const n = choice.n!;
      const hours = { HOURS: n, DAYS: 24 * n, WEEKS: 168 * n, MONTHS: 0 }[choice.unit!];
      if (hours) return new Date(now.getTime() + hours * 3_600_000).toISOString();
      const end = new Date(now.getTime());
      const day = end.getUTCDate();
      end.setUTCDate(1);
      end.setUTCMonth(end.getUTCMonth() + n);
      const last = new Date(Date.UTC(end.getUTCFullYear(), end.getUTCMonth() + 1, 0)).getUTCDate();
      end.setUTCDate(Math.min(day, last));
      return end.toISOString();
    }
  }
}

/** "Permanent", "Temporary: 30 days", "Temporary: until 2026-11-07". */
export function accessLabel(choice: AccessChoice): string {
  switch (choice.mode) {
    case 'permanent': return 'Permanent';
    case 'duration': return `Temporary: ${choice.n} ${UNIT_WORDS[choice.unit!]}${choice.n === 1 ? '' : 's'}`;
    case 'endDate': return `Temporary: until ${choice.date}`;
  }
}

/** The Launcher form's access fields (rules.launcher_form_access, CONTRACTS §9). */
export interface LauncherFormAccess {
  accessType: boolean;
  duration: string;
  /** A one-item list of the duration suffix ("d"): the unit SELECT's option values are the suffixes. */
  durationUnit: string[];
}

const REMOVE_DURATION = /^([1-9][0-9]*)([hdwM])$/;

/**
 * The Launcher form's access fields for a validated `removeDuration`, for submitting through the Launcher:
 * "" → permanent; "30d" → temporary, duration "30", unit ["d"]; an end date arrives as hours ("720h").
 */
export function launcherFormAccess(duration: string): LauncherFormAccess {
  if (duration === '') return { accessType: false, duration: '', durationUnit: [] };
  const m = REMOVE_DURATION.exec(typeof duration === 'string' ? duration : '');
  if (!m) throw new RangeError(`Not a duration: ${duration}`);
  return { accessType: true, duration: m[1], durationUnit: [m[2]] };
}

/** The unit word for a picker ("days"). */
export function unitWord(unit: DurationUnit, n = 2): string {
  return UNIT_WORDS[unit] + (n === 1 ? '' : 's');
}
