// Mirrors core/tests/test_core.py (the rules and config sections), so the
// plugin and the Python core refuse exactly the same requests.
import { DEFAULT_CONFIG, parseRuntimeConfig, RuntimeConfigError, type DurationUnit, type RuntimeConfig } from './runtime-config';
import {
  accessLabel, catalogOptions, clip, COMMENT_SEPARATOR, endDateHours, entitlementFilter, extractInc, incIsValid, justificationMax,
  parseBulkComment, partLabel, requestableObjectTypes, partOf, removeDuration, splitIntoParts, temporaryModes, validateAccess, validateRequest, type AccessChoice,
} from './rules';

/**
 * What plugin/install.py writes for config/bulk-access.example.json (pytest checks that
 * the committed public/bulk-access.config.json equals it). Inlined, because install.py
 * overwrites that file in a build copy with the tenant's own values.
 */
const committed = {
  prefix: 'ACME',
  mode: 'dry-run',
  workflowName: 'ACME Bulk Access Request (Plugin)',
  workflowId: null,
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
  approvals: { enabled: true, concurrency: 4, useBulkEndpoint: 'auto', maxRows: 5000, showOther: false, denyCommentRequired: true },
};

const EXAMPLE: RuntimeConfig = parseRuntimeConfig(committed);

function cfgWith(overrides: Record<string, unknown>): RuntimeConfig {
  return parseRuntimeConfig({ ...committed, ...overrides });
}

describe('runtime config', () => {
  it('the example config loads in dry-run with the core defaults', () => {
    expect(EXAMPLE.mode).toBe('dry-run');
    expect(EXAMPLE.incPattern).toBe('^INC\\d{7}$');
    expect(EXAMPLE.itemsMax).toBe(25);
    expect(EXAMPLE.peopleMax).toBeNull();                 // no people limit
    expect(EXAMPLE.partSize).toBe(250);
    expect(EXAMPLE.temporary).toEqual(committed.temporary);
    expect(EXAMPLE.approvals).toEqual(committed.approvals);
    expect(EXAMPLE.catalogTypes).toEqual(['ACCESS_PROFILE', 'ROLE', 'ENTITLEMENT']);
    expect(EXAMPLE.workflowName).toMatch(/Bulk Access Request \(Plugin\)$/);
  });

  it.each([
    [{ incPattern: '([' }, 'regular expression'],
    [{ incExample: 'CHG123' }, 'does not match'],
    [{ catalogTypes: ['GROUP'] }, 'catalogTypes'],
    [{ itemsMax: 26 }, 'itemsMax'],
    [{ peopleMax: 0 }, 'peopleMax'],
    [{ peopleMax: 2.5 }, 'peopleMax'],
    [{ partSize: 0 }, 'partSize'],
    [{ partSize: 251 }, 'partSize'],
    [{ partSize: null }, 'partSize'],
    [{ temporary: { allow: ['forever'] } }, 'temporary.allow'],
    [{ temporary: { units: ['YEARS'] } }, 'temporary.units'],
    [{ temporary: { maxDays: 0 } }, 'temporary.maxDays'],
    [{ temporary: { enabled: 'yes' } }, 'temporary.enabled'],
    [{ temporary: 'on' }, 'temporary'],
  ])('rejects a bad config with a useful message (%o)', (override, fragment) => {
    expect(() => parseRuntimeConfig({ ...committed, ...override })).toThrow(RuntimeConfigError);
    expect(() => parseRuntimeConfig({ ...committed, ...override })).toThrow(fragment);
  });

  it('falls back to the defaults for missing keys', () => {
    expect(parseRuntimeConfig({})).toEqual(DEFAULT_CONFIG);
    expect(DEFAULT_CONFIG.peopleMax).toBeNull();
    expect(DEFAULT_CONFIG.partSize).toBe(250);
  });

  it('accepts a people limit, smaller parts and a temporary-access cap', () => {
    const cfg = cfgWith({ peopleMax: 600, partSize: 100, temporary: { enabled: true, allow: ['duration'], units: ['DAYS'], maxDays: 90 } });
    expect([cfg.peopleMax, cfg.partSize]).toEqual([600, 100]);
    expect(cfg.temporary).toEqual({ enabled: true, allow: ['duration'], units: ['DAYS'], maxDays: 90 });
  });

  it('leaves temporary access off for a config file from an older install (no `temporary` block)', () => {
    const { temporary: _t, ...old } = committed;
    expect(parseRuntimeConfig(old).temporary.enabled).toBe(false);
    expect(temporaryModes(parseRuntimeConfig(old))).toEqual([]);
    // A block with missing keys gets the Python defaults.
    expect(parseRuntimeConfig({ ...old, temporary: {} }).temporary).toEqual(committed.temporary);
  });
});

describe('rules (core/bulkaccess/rules.py)', () => {
  it('validates the INC with the configured pattern', () => {
    expect(incIsValid(EXAMPLE, 'INC0012345')).toBe(true);
    expect(incIsValid(EXAMPLE, ' INC0012345 ')).toBe(true);
    expect(incIsValid(EXAMPLE, 'INC12345')).toBe(false);
    expect(incIsValid(EXAMPLE, '')).toBe(false);
    const custom = cfgWith({ incPattern: '^(INC|RITM)\\d{7}$', incExample: 'RITM0000001' });
    expect(incIsValid(custom, 'RITM0000001')).toBe(true);
  });

  it('lists every problem with a request', () => {
    const problems = validateRequest(EXAMPLE, { requesterId: 'me', approverId: 'me', people: [], items: [], inc: 'nope' });
    expect(problems).toContain('Choose at least one person.');
    expect(problems).toContain('Choose at least one access item.');
    expect(problems).toContain('The approver must be someone other than you.');
    expect(problems).toContain(EXAMPLE.incMessage);
    const ok = validateRequest(EXAMPLE, {
      requesterId: 'me', approverId: 'boss', people: ['p1'],
      items: [{ id: 'a', type: 'ACCESS_PROFILE' }], inc: 'INC0012345',
    });
    expect(ok).toEqual([]);
    const onTheList = validateRequest(EXAMPLE, {
      requesterId: 'me', approverId: 'boss', people: ['p1', 'boss'],
      items: [{ id: 'a', type: 'ACCESS_PROFILE' }], inc: 'INC0012345',
    });
    expect(onTheList).toEqual(["The approver can't be one of the people getting the access."]);
  });

  it('has no people limit unless the config sets one', () => {
    const many = Array.from({ length: 1200 }, (_, i) => `p${i}`);
    const draft = { requesterId: 'me', approverId: 'boss', people: many, items: [{ id: 'a', type: 'ACCESS_PROFILE' }], inc: 'INC0012345' };
    expect(validateRequest(EXAMPLE, draft)).toEqual([]);
    expect(validateRequest(cfgWith({ peopleMax: 1000 }), draft)).toEqual(['Choose at most 1000 people.']);
    // duplicates count once
    expect(validateRequest(cfgWith({ peopleMax: 1 }), { ...draft, people: ['a', 'a'] })).toEqual([]);
  });

  it('enforces the limits, the allowed types and a chosen approver', () => {
    const cfg = cfgWith({ peopleMax: 2, itemsMax: 1, catalogTypes: ['ROLE'] });
    const problems = validateRequest(cfg, {
      requesterId: 'me', approverId: null, people: ['a', 'b', 'c'],
      items: [{ id: 'x', type: 'ACCESS_PROFILE' }, { id: 'y', type: 'ROLE' }], inc: 'INC0012345',
    });
    expect(problems).toEqual([
      'Choose at most 2 people.',
      'Choose at most 1 access items.',
      'One of the chosen items is not an allowed type.',
      'Choose an approver.',
    ]);
  });

  it('builds catalog options that carry full access objects and respect the filters', () => {
    const cfg = cfgWith({ catalogTypes: ['ACCESS_PROFILE'], nameStartsWith: 'ACME' });
    const opts = catalogOptions(cfg, [
      { id: '1', type: 'ACCESS_PROFILE', name: 'ACME Bulk Test Access', source: { name: 'ACME SaaS' } },
      { id: '2', type: 'ACCESS_PROFILE', name: 'Sales Regional - AD' },
      { id: '3', type: 'ROLE', name: 'ACME Role' },
    ]);
    expect(opts.map(({ label, subLabel, value }) => ({ label, subLabel, value }))).toEqual([
      { label: 'ACME Bulk Test Access', subLabel: 'Access profile · ACME SaaS',
        value: { id: '1', type: 'ACCESS_PROFILE', name: 'ACME Bulk Test Access' } },
    ]);
  });

  it('lists entitlements through the entitlements API, not requestable-objects (like rules.py)', () => {
    expect(requestableObjectTypes(cfgWith({ catalogTypes: ['ENTITLEMENT', 'ROLE', 'ACCESS_PROFILE'] }))).toEqual(['ROLE', 'ACCESS_PROFILE']);
    expect(requestableObjectTypes(cfgWith({ catalogTypes: ['ENTITLEMENT'] }))).toEqual([]);
    expect(entitlementFilter(cfgWith({ catalogTypes: ['ACCESS_PROFILE'] }))).toBeNull();
    expect(entitlementFilter(cfgWith({ catalogTypes: ['ENTITLEMENT'], nameStartsWith: null }))).toBe('requestable eq true');
    expect(entitlementFilter(cfgWith({ catalogTypes: ['ENTITLEMENT'], nameStartsWith: 'AC"ME\\' })))
      .toBe('requestable eq true and name sw "AC\\"ME\\\\"');
  });

  it('takes sources looked up separately and sorts by name', () => {
    const opts = catalogOptions(EXAMPLE, [
      { id: 'b', type: 'ENTITLEMENT', name: 'beta' },
      { id: 'a', type: 'ROLE', name: 'Alpha' },
    ], { b: 'Active Directory' });
    expect(opts.map((o) => o.subLabel)).toEqual(['Role', 'Entitlement · Active Directory']);
  });

  it('clips long text like rules.clip', () => {
    expect(clip('a  b\n c', 10)).toBe('a b c');
    expect(clip('abcdefghij', 5)).toBe('abcd…');
  });

  it('keeps "<INC>: <justification>" within the approval comment limit', () => {
    expect(justificationMax('INC0012345')).toBe(150 - 2 - 10);
    expect(justificationMax('', 'INC0012345')).toBe(138);
  });

  it('finds the INC in access request comments and approval names', () => {
    expect(extractInc(EXAMPLE, 'INC0012345 | Bulk access request by admin.user | Approved by Aisha')).toBe('INC0012345');
    expect(extractInc(EXAMPLE, 'Bulk access INC0099999')).toBe('INC0099999');
    expect(extractInc(EXAMPLE, 'no ticket here INC12')).toBeNull();
    expect(extractInc(EXAMPLE, null)).toBeNull();
  });
});

describe('the bulk item comment (rules.parse_bulk_comment)', () => {
  const BULK = 'INC0012345 | Bulk access request by Ada Lovelace | Approved by Grace Hopper | Temporary: 30 days | Quarterly audit';
  const PARSED = {
    inc: 'INC0012345', requester: 'Ada Lovelace', approver: 'Grace Hopper',
    accessLabel: 'Temporary: 30 days', justification: 'Quarterly audit',
  };
  const bulk = (justification: string, label = 'Permanent') =>
    ({ inc: 'INC0012345', requester: 'Ada', approver: 'Grace', accessLabel: label, justification });

  it('uses the separator definitions.py writes', () => {
    expect(COMMENT_SEPARATOR).toBe(' | ');
  });

  it.each([
    [BULK, PARSED],
    // the justification is everything after the 4th separator, " | " included
    ['INC0012345 | Bulk access request by Ada | Approved by Grace | Permanent | move | to | finance', bulk('move | to | finance')],
    // extra whitespace around the separators and inside the labels
    ['  INC0012345   |  Bulk  access request   by  Ada Lovelace |Approved   by Grace Hopper|  Temporary: 30 days  |'
      + '  Quarterly audit  ', PARSED],
    // an empty justification (a trailing separator, with or without its space)
    ['INC0012345 | Bulk access request by Ada | Approved by Grace | Permanent | ', bulk('')],
    ['INC0012345 | Bulk access request by Ada | Approved by Grace | Permanent |', bulk('')],
    // a multi-line justification is kept as written
    ['INC0012345 | Bulk access request by Ada | Approved by Grace | Permanent | line one\nline two', bulk('line one\nline two')],
  ])('reads the bulk item comment (%j)', (text, expected) => {
    expect(parseBulkComment(EXAMPLE, text)).toEqual(expected);
  });

  it.each([
    null, undefined, '', 42, 'INC0012345', 'INC0012345: Quarterly audit',          // plain comments, the INC alone
    'Please approve, thanks',
    'INC0012345 | Bulk access request by Ada | Approved by Grace | Permanent',          // only 4 fields
    'INC12345 | Bulk access request by Ada | Approved by Grace | Permanent | why',       // INC fails the pattern
    'CHG0012345 | Bulk access request by Ada | Approved by Grace | Permanent | why',
    'INC0012345 | Access request by Ada | Approved by Grace | Permanent | why',          // wrong labels
    'INC0012345 | Bulk access request by Ada | Rejected by Grace | Permanent | why',
    'INC0012345 | Approved by Grace | Bulk access request by Ada | Permanent | why',     // wrong order
    'INC0012345 | Bulk access request by | Approved by Grace | Permanent | why',          // empty names or label
    'INC0012345 | Bulk access request by Ada | Approved by  | Permanent | why',
    'INC0012345 | Bulk access request by Ada | Approved by Grace |  | why',
    'INC0012345 | Bulk access request byAda | Approved by Grace | Permanent | why',
  ])('ignores everything else (%j)', (text) => {
    expect(parseBulkComment(EXAMPLE, text)).toBeNull();
  });

  it('uses the configured INC pattern', () => {
    const custom = cfgWith({ incPattern: '^(INC|RITM)\\d{7}$', incExample: 'RITM0000001' });
    const text = BULK.replace('INC0012345', 'RITM0000001');
    expect(parseBulkComment(custom, text)?.inc).toBe('RITM0000001');
    expect(parseBulkComment(EXAMPLE, text)).toBeNull();
  });

  it('reads back what the workflow writes (definitions.py loop comment, rendered)', () => {
    const parts = {
      inc: 'INC0012345', requester: 'Ada Lovelace', approver: 'Grace Hopper',
      accessLabel: 'Temporary: until 2026-11-07', justification: 'Audit | see INC0099999: finance | Q4',
    };
    const rendered = [parts.inc, `Bulk access request by ${parts.requester}`, `Approved by ${parts.approver}`,
      parts.accessLabel, parts.justification].join(COMMENT_SEPARATOR);
    expect(parseBulkComment(EXAMPLE, rendered)).toEqual(parts);
  });
});

describe('parts (rules.split_into_parts / part_label)', () => {
  it('splits the de-duplicated list, in order, into parts of partSize', () => {
    const people = Array.from({ length: 600 }, (_, i) => `p${i}`);
    const parts = splitIntoParts([...people, 'p0', 'p599'], 250);
    expect(parts.map((p) => p.length)).toEqual([250, 250, 100]);
    expect(parts.flat()).toEqual(people);
    expect(splitIntoParts(['a', 'b', 'a', 'c'], 2)).toEqual([['a', 'b'], ['c']]);
    expect(splitIntoParts(['a'], 250)).toEqual([['a']]);
    expect(splitIntoParts([], 250)).toEqual([]);
    expect(splitIntoParts(Array.from({ length: 250 }, (_, i) => i), 250)).toHaveLength(1);
    expect(splitIntoParts(Array.from({ length: 251 }, (_, i) => i), 250).map((p) => p.length)).toEqual([250, 1]);
  });

  it('de-duplicates objects by a key', () => {
    const p = (id: string) => ({ id });
    expect(splitIntoParts([p('a'), p('b'), p('a')], 1, (x) => x.id)).toEqual([[p('a')], [p('b')]]);
  });

  it('refuses a part size below 1', () => {
    expect(() => splitIntoParts(['a'], 0)).toThrow(RangeError);
  });

  it('labels parts only when there are several', () => {
    expect(partLabel(1, 1)).toBe('');
    expect(partLabel(2, 3)).toBe(' (2/3)');
    expect(`Bulk access INC0012345${partLabel(3, 3)}`).toBe('Bulk access INC0012345 (3/3)');
  });

  it('reads the part back from an approval name, and the INC still parses', () => {
    expect(partOf('Bulk access INC0012345 (2/3)')).toEqual({ part: 2, parts: 3 });
    expect(partOf('Bulk access INC0012345')).toBeNull();
    expect(extractInc(EXAMPLE, 'Bulk access INC0012345 (2/3)')).toBe('INC0012345');
  });
});

describe('temporary access (CONTRACTS §2)', () => {
  const d = (n: number | null, unit: DurationUnit | null): AccessChoice => ({ mode: 'duration', n, unit });
  const until = (date: string): AccessChoice => ({ mode: 'endDate', date });
  // 8 Oct 2026, 10:00 local time
  const NOW = new Date(2026, 9, 8, 10, 0, 0);

  it('permanent is always allowed and sends an empty removeDuration', () => {
    const off = cfgWith({ temporary: { enabled: false } });
    expect(validateAccess(off, { mode: 'permanent' }, NOW)).toEqual([]);
    expect(removeDuration({ mode: 'permanent' })).toBe('');
    expect(accessLabel({ mode: 'permanent' })).toBe('Permanent');
  });

  it('converts durations to "<n><suffix>" with a readable label', () => {
    expect(removeDuration(d(30, 'DAYS'))).toBe('30d');
    expect(removeDuration(d(2, 'HOURS'))).toBe('2h');
    expect(removeDuration(d(1, 'WEEKS'))).toBe('1w');
    expect(removeDuration(d(3, 'MONTHS'))).toBe('3M');
    expect(accessLabel(d(1, 'DAYS'))).toBe('Temporary: 1 day');
    expect(accessLabel(d(30, 'DAYS'))).toBe('Temporary: 30 days');
    expect(accessLabel(d(2, 'HOURS'))).toBe('Temporary: 2 hours');
    expect(accessLabel(d(1, 'WEEKS'))).toBe('Temporary: 1 week');
    expect(accessLabel(d(3, 'MONTHS'))).toBe('Temporary: 3 months');
  });

  it('converts an end date to whole hours until 23:59:59 local time that day', () => {
    // 8 Oct 10:00 -> 9 Oct 23:59:59 = 37 h 59 m 59 s, rounded up
    expect(endDateHours('2026-10-09', NOW)).toBe(38);
    expect(removeDuration(until('2026-10-09'), NOW)).toBe('38h');
    // (computed the same way, so a daylight-saving change in the test machine's zone is fine)
    const hours = Math.ceil((new Date(2026, 10, 7, 23, 59, 59).getTime() - NOW.getTime()) / 3_600_000);
    expect(hours).toBeGreaterThanOrEqual(30 * 24 + 13);
    expect(removeDuration(until('2026-11-07'), NOW)).toBe(`${hours}h`);
    expect(accessLabel(until('2026-11-07'))).toBe('Temporary: until 2026-11-07');
    expect(endDateHours('2026-10-09', new Date(2026, 9, 9, 23, 0, 0))).toBe(1);
  });

  it('accepts valid choices', () => {
    expect(validateAccess(EXAMPLE, d(30, 'DAYS'), NOW)).toEqual([]);
    expect(validateAccess(EXAMPLE, until('2026-10-09'), NOW)).toEqual([]);
  });

  it('refuses what the config does not offer', () => {
    const off = cfgWith({ temporary: { enabled: false } });
    expect(validateAccess(off, d(30, 'DAYS'), NOW)).toEqual(["Temporary access isn't available."]);
    const durationOnly = cfgWith({ temporary: { allow: ['duration'] } });
    expect(validateAccess(durationOnly, until('2026-10-09'), NOW)).toEqual(["Temporary access isn't available."]);
    expect(validateAccess(durationOnly, d(1, 'DAYS'), NOW)).toEqual([]);
  });

  it('checks the duration: a whole number of 1 or more, in an offered unit', () => {
    const whole = 'Enter the duration as a whole number of 1 or more.';
    expect(validateAccess(EXAMPLE, d(0, 'DAYS'), NOW)).toEqual([whole]);
    expect(validateAccess(EXAMPLE, d(1.5, 'DAYS'), NOW)).toEqual([whole]);
    expect(validateAccess(EXAMPLE, d(null, 'DAYS'), NOW)).toEqual([whole]);
    expect(validateAccess(EXAMPLE, d(-2, 'DAYS'), NOW)).toEqual([whole]);
    const daysOnly = cfgWith({ temporary: { units: ['DAYS'] } });
    expect(validateAccess(daysOnly, d(2, 'HOURS'), NOW)).toEqual(['Choose a unit for the duration.']);
    expect(validateAccess(daysOnly, d(2, null), NOW)).toEqual(['Choose a unit for the duration.']);
  });

  it('checks the end date is after today', () => {
    const msg = 'Choose an end date after today.';
    expect(validateAccess(EXAMPLE, until('2026-10-08'), NOW)).toEqual([msg]);   // today
    expect(validateAccess(EXAMPLE, until('2026-10-01'), NOW)).toEqual([msg]);
    expect(validateAccess(EXAMPLE, until(''), NOW)).toEqual([msg]);
    expect(validateAccess(EXAMPLE, until('2026-02-30'), NOW)).toEqual([msg]);   // not a real day
  });

  it('caps the length at maxDays (n × the unit\'s upper bound in days, or hours / 24)', () => {
    const cfg = cfgWith({ temporary: { maxDays: 30 } });
    const cap = 'Temporary access can last at most 30 days.';
    expect(validateAccess(cfg, d(30, 'DAYS'), NOW)).toEqual([]);
    expect(validateAccess(cfg, d(31, 'DAYS'), NOW)).toEqual([cap]);
    expect(validateAccess(cfg, d(4, 'WEEKS'), NOW)).toEqual([]);
    expect(validateAccess(cfg, d(5, 'WEEKS'), NOW)).toEqual([cap]);
    expect(validateAccess(cfg, d(1, 'MONTHS'), NOW)).toEqual([cap]);           // a month counts as 31 days
    expect(validateAccess(cfg, d(720, 'HOURS'), NOW)).toEqual([]);
    expect(validateAccess(cfg, d(721, 'HOURS'), NOW)).toEqual([cap]);
    expect(validateAccess(cfg, until('2026-11-06'), NOW)).toEqual([]);         // 29 days 14 h
    expect(validateAccess(cfg, until('2026-11-07'), NOW)).toEqual([cap]);      // 30 days 14 h
  });
});

describe('assignedApproverNames', () => {
  it('shows who was asked, not the admin who decided on their behalf', async () => {
    const { assignedApproverNames } = await import('./bulk-api.service');
    expect(assignedApproverNames({ assignedTo: [{ name: 'Aisha Bello' }], approvers: [{ name: 'admin.user' }] })).toEqual(['Aisha Bello']);
    expect(assignedApproverNames({ approvers: [{ name: 'admin.user' }] })).toEqual(['admin.user']);
    // decided by an admin on the approver's behalf: SailPoint records a reassignment
    expect(assignedApproverNames({ assignedTo: null, approvers: [{ name: 'admin.user' }],
      reassignmentHistory: [{ reassignedFrom: { name: 'Aisha Bello' } }] })).toEqual(['Aisha Bello']);
  });
});
