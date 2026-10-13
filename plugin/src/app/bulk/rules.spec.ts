// Mirrors core/tests/test_core.py (the rules and config sections), so the
// plugin and the Python core refuse exactly the same requests.
import {
  DEFAULT_CONFIG, MSG_LAUNCHER_ID, MSG_SUBMIT, parseRuntimeConfig, RuntimeConfigError, type DurationUnit, type RuntimeConfig,
} from './runtime-config';
import {
  accessLabel, bulkComment, catalogOptions, clip, COMMENT_SEPARATOR, endDateHours, entitlementFilter, extractInc, incIsValid,
  JUSTIFICATION_MAX, launcherFormAccess, MSG_JUSTIFICATION, MSG_JUSTIFICATION_LONG, NO_APPROVAL_LABEL, parseBulkComment,
  partLabel, removeDate, removeDuration, requestableObjectTypes, splitIntoParts, temporaryModes, validateAccess,
  validateRequest, type AccessChoice,
} from './rules';

/**
 * What plugin/install.py writes for config/bulk-access.example.json (pytest checks that
 * the committed public/bulk-access.config.json equals it). Inlined, because install.py
 * overwrites that file in a build copy with the tenant's own values.
 */
const committed = {
  prefix: 'ACME',
  mode: 'dry-run',
  submit: 'direct',
  launcherId: null,
  launcherAccessId: null,        // install.py writes the tenant's Launcher Access profile ID
  launcherAccessName: 'ACME Bulk Access Request - Launcher Access',
  incPattern: '^INC\\d{7}$',
  incMessage: 'Enter a ServiceNow incident number: INC followed by 7 digits, e.g. INC0012345.',
  incExample: 'INC0012345',
  peopleMax: null,
  partSize: 250,
  itemsMax: 25,
  catalogTypes: ['ACCESS_PROFILE', 'ROLE', 'ENTITLEMENT'],
  nameStartsWith: null,
  noApproval: [],                 // install.py lists the catalog items with no approval of their own
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
    expect(EXAMPLE.submit).toBe('direct');
    expect(EXAMPLE.noApproval).toEqual([]);
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
    [{ noApproval: 'x' }, 'noApproval'],
    [{ noApproval: [1] }, 'noApproval'],
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

  it('files the requests directly, or through the Launcher it names (plugin.submit)', () => {
    expect([EXAMPLE.submit, EXAMPLE.launcherAccessName]).toEqual(['direct', 'ACME Bulk Access Request - Launcher Access']);
    expect(cfgWith({ submit: 'launcher', launcherId: 'ln-1', launcherAccessId: 'lap-1' }))
      .toMatchObject({ submit: 'launcher', launcherId: 'ln-1', launcherAccessId: 'lap-1' });
    // A file from an older install: no `submit`, or the removed test endpoint. Both file directly now.
    const { submit: _s, ...old } = committed;
    expect(parseRuntimeConfig(old).submit).toBe('direct');
    expect(cfgWith({ submit: 'test-endpoint' }).submit).toBe('direct');
    expect(() => cfgWith({ submit: 'backend' })).toThrow(MSG_SUBMIT);
    // Launcher mode without an ID loads; submitting stops until install.py fills it in.
    expect(cfgWith({ submit: 'launcher' })).toMatchObject({ submit: 'launcher', launcherId: null });
    expect(MSG_LAUNCHER_ID).toContain('plugin/install.py');
  });

  it('reads the items with no approval of their own (written by install.py)', () => {
    expect(cfgWith({ noApproval: ['a', 'b'] }).noApproval).toEqual(['a', 'b']);
    expect(cfgWith({ noApproval: null }).noApproval).toEqual([]);
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
    const problems = validateRequest(EXAMPLE, { people: [], items: [], inc: 'nope', justification: '  ' });
    expect(problems).toEqual(['Choose at least one person.', 'Choose at least one access item.', EXAMPLE.incMessage,
      MSG_JUSTIFICATION]);
    const ok = validateRequest(EXAMPLE, {
      people: ['p1'], items: [{ id: 'a', type: 'ACCESS_PROFILE' }], inc: 'INC0012345', justification: 'Quarter-end audit',
    });
    expect(ok).toEqual([]);
    // There is no approver any more: requesting access for yourself is allowed; SailPoint's own approvals decide.
    expect(validateRequest(EXAMPLE, { people: ['me'], items: [{ id: 'a', type: 'ROLE' }], inc: 'INC0012345', justification: 'x' }))
      .toEqual([]);
  });

  it('caps the justification for readability', () => {
    const draft = { people: ['p'], items: [{ id: 'a', type: 'ROLE' }], inc: 'INC0012345' };
    expect(validateRequest(EXAMPLE, { ...draft, justification: 'x'.repeat(JUSTIFICATION_MAX) })).toEqual([]);
    expect(validateRequest(EXAMPLE, { ...draft, justification: 'x'.repeat(JUSTIFICATION_MAX + 1) }))
      .toEqual([MSG_JUSTIFICATION_LONG]);
    expect(MSG_JUSTIFICATION_LONG).toBe('Keep the justification to 1000 characters or fewer.');
  });

  it('has no people limit unless the config sets one', () => {
    const many = Array.from({ length: 1200 }, (_, i) => `p${i}`);
    const draft = { people: many, items: [{ id: 'a', type: 'ACCESS_PROFILE' }], inc: 'INC0012345', justification: 'why' };
    expect(validateRequest(EXAMPLE, draft)).toEqual([]);
    expect(validateRequest(cfgWith({ peopleMax: 1000 }), draft)).toEqual(['Choose at most 1000 people.']);
    // duplicates count once
    expect(validateRequest(cfgWith({ peopleMax: 1 }), { ...draft, people: ['a', 'a'] })).toEqual([]);
  });

  it('enforces the limits and the allowed types', () => {
    const cfg = cfgWith({ peopleMax: 2, itemsMax: 1, catalogTypes: ['ROLE'] });
    const problems = validateRequest(cfg, {
      people: ['a', 'b', 'c'], items: [{ id: 'x', type: 'ACCESS_PROFILE' }, { id: 'y', type: 'ROLE' }],
      inc: 'INC0012345', justification: 'why',
    });
    expect(problems).toEqual([
      'Choose at most 2 people.',
      'Choose at most 1 access items.',
      'One of the chosen items is not an allowed type.',
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

  it('says which items are granted without approval', () => {
    const opts = catalogOptions(cfgWith({ noApproval: ['2'] }), [
      { id: '1', type: 'ACCESS_PROFILE', name: 'A', source: { name: 'AD' } },
      { id: '2', type: 'ROLE', name: 'B' },
    ]);
    expect(opts.map((o) => o.subLabel)).toEqual(['Access profile · AD', 'Role · No approval: granted right away']);
    expect(NO_APPROVAL_LABEL).toBe('No approval: granted right away');
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

  it('finds the INC in access request comments and approval names', () => {
    expect(extractInc(EXAMPLE, 'INC0012345 | Bulk access request by admin.user | Permanent | why')).toBe('INC0012345');
    expect(extractInc(EXAMPLE, 'Bulk access INC0099999')).toBe('INC0099999');
    expect(extractInc(EXAMPLE, 'no ticket here INC12')).toBeNull();
    expect(extractInc(EXAMPLE, null)).toBeNull();
  });
});

describe('the bulk item comment (rules.parse_bulk_comment)', () => {
  const BULK = 'INC0012345 | Bulk access request by Ada Lovelace | Temporary: 30 days | Quarterly audit';
  const PARSED = {
    inc: 'INC0012345', requester: 'Ada Lovelace', approver: null,
    accessLabel: 'Temporary: 30 days', justification: 'Quarterly audit',
  };
  // Written while bulk requests had one approver: still read, so approvals already pending keep their INC grouping.
  const OLD = 'INC0012345 | Bulk access request by Ada Lovelace | Approved by Grace Hopper | Temporary: 30 days | Quarterly audit';
  const bulk = (justification: string, label = 'Permanent', approver: string | null = null) =>
    ({ inc: 'INC0012345', requester: 'Ada', approver, accessLabel: label, justification });

  it('uses the separator definitions.py writes', () => {
    expect(COMMENT_SEPARATOR).toBe(' | ');
  });

  it.each([
    [BULK, PARSED],
    [OLD, { ...PARSED, approver: 'Grace Hopper' }],
    // the justification is everything after the 3rd separator, " | " included
    ['INC0012345 | Bulk access request by Ada | Permanent | move | to | finance', bulk('move | to | finance')],
    ['INC0012345 | Bulk access request by Ada | Approved by Grace | Permanent | move | to | finance',
      bulk('move | to | finance', 'Permanent', 'Grace')],
    // extra whitespace around the separators and inside the labels
    ['  INC0012345   |  Bulk  access request   by  Ada Lovelace |  Temporary: 30 days  |  Quarterly audit  ', PARSED],
    // an empty justification (a trailing separator, with or without its space)
    ['INC0012345 | Bulk access request by Ada | Permanent | ', bulk('')],
    ['INC0012345 | Bulk access request by Ada | Permanent |', bulk('')],
    // a multi-line justification is kept as written
    ['INC0012345 | Bulk access request by Ada | Permanent | line one\nline two', bulk('line one\nline two')],
  ])('reads the bulk item comment (%j)', (text, expected) => {
    expect(parseBulkComment(EXAMPLE, text)).toEqual(expected);
  });

  it('writes what it reads (rules.bulk_comment)', () => {
    const text = bulkComment(' INC0012345 ', 'Ada Lovelace', 'Temporary: 30 days', ' Quarterly audit ');
    expect(text).toBe(BULK);
    expect(parseBulkComment(EXAMPLE, text)).toEqual(PARSED);
  });

  it.each([
    null, undefined, '', 42, 'INC0012345', 'INC0012345: Quarterly audit',          // plain comments, the INC alone
    'Please approve, thanks',
    'INC0012345 | Bulk access request by Ada | Permanent',                              // only 3 fields
    'INC12345 | Bulk access request by Ada | Permanent | why',                           // INC fails the pattern
    'CHG0012345 | Bulk access request by Ada | Permanent | why',
    'INC0012345 | Access request by Ada | Permanent | why',                              // wrong label
    'INC0012345 | Permanent | Bulk access request by Ada | why',                         // wrong order
    'INC0012345 | Bulk access request by | Permanent | why',                             // empty name or label
    'INC0012345 | Bulk access request by Ada |  | why',
    'INC0012345 | Bulk access request byAda | Permanent | why',
    'INC0012345 | Bulk access request by Ada | Approved by  | Permanent | why',          // old form, empty approver
  ])('ignores everything else (%j)', (text) => {
    expect(parseBulkComment(EXAMPLE, text)).toBeNull();
  });

  it('uses the configured INC pattern', () => {
    const custom = cfgWith({ incPattern: '^(INC|RITM)\\d{7}$', incExample: 'RITM0000001' });
    const text = BULK.replace('INC0012345', 'RITM0000001');
    expect(parseBulkComment(custom, text)?.inc).toBe('RITM0000001');
    expect(parseBulkComment(EXAMPLE, text)).toBeNull();
  });

  it('reads back a justification that itself contains separators and an INC', () => {
    const parts = { inc: 'INC0012345', requester: 'Ada Lovelace', approver: null,
      accessLabel: 'Temporary: until 2026-11-07', justification: 'Audit | see INC0099999: finance | Q4' };
    expect(parseBulkComment(EXAMPLE, bulkComment(parts.inc, parts.requester, parts.accessLabel, parts.justification)))
      .toEqual(parts);
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

  it('turns the choice into a direct request\'s removeDate', () => {
    const at = new Date(Date.UTC(2026, 0, 31, 10, 0, 0));
    expect(removeDate({ mode: 'permanent' }, at)).toBeNull();
    expect(removeDate(d(2, 'HOURS'), at)).toBe('2026-01-31T12:00:00.000Z');
    expect(removeDate(d(30, 'DAYS'), at)).toBe('2026-03-02T10:00:00.000Z');
    expect(removeDate(d(1, 'WEEKS'), at)).toBe('2026-02-07T10:00:00.000Z');
    // a calendar month, clamped to the last day of a shorter month
    expect(removeDate(d(1, 'MONTHS'), at)).toBe('2026-02-28T10:00:00.000Z');
    expect(removeDate(d(3, 'MONTHS'), at)).toBe('2026-04-30T10:00:00.000Z');
    expect(removeDate(d(12, 'MONTHS'), at)).toBe('2027-01-31T10:00:00.000Z');
    // an end date: the end of that day, local time
    expect(removeDate(until('2026-11-07'), at)).toBe(new Date(2026, 10, 7, 23, 59, 59).toISOString());
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

describe('the Launcher form fields (rules.launcher_form_access)', () => {
  it.each([
    ['', { accessType: false, duration: '', durationUnit: [] }],
    ['30d', { accessType: true, duration: '30', durationUnit: ['d'] }],
    ['720h', { accessType: true, duration: '720', durationUnit: ['h'] }],
    ['2w', { accessType: true, duration: '2', durationUnit: ['w'] }],
    ['3M', { accessType: true, duration: '3', durationUnit: ['M'] }],
  ])('maps removeDuration %j onto the form', (duration, fields) => {
    expect(launcherFormAccess(duration)).toEqual(fields);
  });

  it.each(['abc', '0d', '30', '30x', '-1d', null])('refuses %j', (bad) => {
    expect(() => launcherFormAccess(bad as string)).toThrow(RangeError);
  });

  it('sends an end date as hours', () => {
    const now = new Date(2026, 9, 8, 10, 0, 0);
    const choice: AccessChoice = { mode: 'endDate', date: '2026-10-09' };
    expect(launcherFormAccess(removeDuration(choice, now))).toEqual({ accessType: true, duration: '38', durationUnit: ['h'] });
  });
});
