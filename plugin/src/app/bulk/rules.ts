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

/** SailPoint limits on a generic approval task's text fields (rules.py). */
export const APPROVAL_NAME_MAX = 50;
export const APPROVAL_DESCRIPTION_MAX = 150;
export const APPROVAL_COMMENT_MAX = 150;

/** One access item as the workflow's Manage Access step needs it. */
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
  requesterId: string;
  approverId: string | null | undefined;
  people: string[];
  items: { id: string; type: string }[];
  inc: string;
}

/** Everything wrong with a bulk request, as sentences (empty list = OK). Same order as rules.py. */
export function validateRequest(cfg: RuleConfig, draft: RequestDraft): string[] {
  const problems: string[] = [];
  const { requesterId, approverId, people, items, inc } = draft;
  if (!people.length) problems.push('Choose at least one person.');
  // No upper bound unless the config sets one: big lists go out in parts (splitIntoParts).
  if (cfg.peopleMax !== null && new Set(people).size > cfg.peopleMax) problems.push(`Choose at most ${cfg.peopleMax} people.`);
  if (!items.length) problems.push('Choose at least one access item.');
  if (items.length > cfg.itemsMax) problems.push(`Choose at most ${cfg.itemsMax} access items.`);
  if (items.some((i) => !cfg.catalogTypes.includes(i.type as ItemType))) {
    problems.push('One of the chosen items is not an allowed type.');
  }
  if (!approverId) {
    problems.push('Choose an approver.');
  } else if (approverId === requesterId) {
    // SailPoint reassigns self-approvals to another admin, so refuse up front.
    problems.push('The approver must be someone other than you.');
  } else if (people.includes(approverId)) {
    // Nobody approves access for themselves, even inside a bulk request.
    problems.push("The approver can't be one of the people getting the access.");
  }
  if (!incIsValid(cfg, inc)) problems.push(cfg.incMessage);
  return problems;
}

export function clip(text: string, limit: number): string {
  const flat = String(text).split(/\s+/).filter(Boolean).join(' ');
  return flat.length <= limit ? flat : flat.slice(0, limit - 1) + '…';
}

/**
 * The workflow writes "<INC>: <justification>" into the generic approval's
 * comment, which SailPoint caps at APPROVAL_COMMENT_MAX characters.
 */
export function justificationMax(inc: string, example = ''): number {
  return APPROVAL_COMMENT_MAX - 2 - Math.max(inc.trim().length, example.length);
}

/** A catalog entry as the Items step shows it. */
export interface CatalogOption {
  label: string;
  subLabel: string;
  description: string;
  source: string | null;
  value: AccessItem;
}

/** /v3/requestable-objects rows -> catalog options, filtered by type and name prefix (rules.catalog_options). */
export function catalogOptions(
  cfg: Pick<RuntimeConfig, 'catalogTypes' | 'nameStartsWith'>,
  requestable: Iterable<Record<string, unknown>>,
  sources: Record<string, string> = {},
): CatalogOption[] {
  const options: CatalogOption[] = [];
  for (const obj of requestable) {
    const kind = obj['type'] as ItemType;
    const id = String(obj['id']);
    const name = String(obj['name'] || id);
    if (!cfg.catalogTypes.includes(kind)) continue;
    if (cfg.nameStartsWith && !name.startsWith(cfg.nameStartsWith)) continue;
    const embedded = obj['source'] as { name?: string } | undefined;
    const source = sources[id] ?? (embedded && typeof embedded === 'object' ? embedded.name ?? null : null);
    options.push({
      label: name,
      subLabel: TYPE_LABELS[kind] + (source ? ` · ${source}` : ''),
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
 * "INC0012345 | Bulk access request by …", or an approval name such as
 * "Bulk access INC0012345"). Each word is tested against the configured
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

/** The generic approval name the workflow gives each bulk request (definitions.py: "Bulk access {inc}"). */
export const APPROVAL_NAME_PREFIX = 'Bulk access ';

// ── Parts (rules.split_into_parts / rules.part_label) ─────────────────────────
/**
 * The de-duplicated list, in order, cut into chunks of `partSize`. Each chunk is one
 * workflow run with its own approval: SailPoint's Loop takes at most 250 items.
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

/** "" for a single part, else " (i/n)" (leading space, 1-based): appended to the INC in the approval name. */
export function partLabel(i: number, n: number): string {
  return n === 1 ? '' : ` (${i}/${n})`;
}

/** The part a workflow-created approval belongs to, read back from its name ("Bulk access INC… (2/3)"). */
export function partOf(approvalName: string): { part: number; parts: number } | null {
  const m = /\((\d+)\/(\d+)\)\s*$/.exec(approvalName);
  return m ? { part: Number(m[1]), parts: Number(m[2]) } : null;
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

/** "Permanent", "Temporary: 30 days", "Temporary: until 2026-11-07". */
export function accessLabel(choice: AccessChoice): string {
  switch (choice.mode) {
    case 'permanent': return 'Permanent';
    case 'duration': return `Temporary: ${choice.n} ${UNIT_WORDS[choice.unit!]}${choice.n === 1 ? '' : 's'}`;
    case 'endDate': return `Temporary: until ${choice.date}`;
  }
}

/** The unit word for a picker ("days"). */
export function unitWord(unit: DurationUnit, n = 2): string {
  return UNIT_WORDS[unit] + (n === 1 ? '' : 's');
}
