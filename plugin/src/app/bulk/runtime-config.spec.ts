// The `approvals` block of the runtime config. Mirrors the approvals cases in core/tests/test_core.py:
// same defaults, same ranges and the same problem messages, word for word.
import { DEFAULT_CONFIG, parseRuntimeConfig, RuntimeConfigError } from './runtime-config';

const block = { enabled: true, concurrency: 4, useBulkEndpoint: 'auto', maxRows: 5000, showOther: false, denyCommentRequired: true };

describe('runtime config: approvals (config.py `approvals`)', () => {
  it('reads the block plugin/install.py writes', () => {
    expect(parseRuntimeConfig({ approvals: block }).approvals).toEqual(block);
    const custom = { enabled: true, concurrency: 8, useBulkEndpoint: 'never', maxRows: 250, showOther: true, denyCommentRequired: false };
    expect(parseRuntimeConfig({ approvals: custom }).approvals).toEqual(custom);
    expect(parseRuntimeConfig({ approvals: { ...block, concurrency: 1, maxRows: 20000 } }).approvals.maxRows).toBe(20000);
    expect(parseRuntimeConfig({ approvals: { ...block, useBulkEndpoint: 'always' } }).approvals.useBulkEndpoint).toBe('always');
  });

  it('is off for a file from an older install (no `approvals` block)', () => {
    expect(parseRuntimeConfig({}).approvals).toEqual(DEFAULT_CONFIG.approvals);
    expect(DEFAULT_CONFIG.approvals).toEqual({ ...block, enabled: false });
    expect(parseRuntimeConfig({ approvals: null }).approvals.enabled).toBe(false);
  });

  it('gives missing keys the Python defaults (enabled true)', () => {
    expect(parseRuntimeConfig({ approvals: {} }).approvals).toEqual(block);
    expect(parseRuntimeConfig({ approvals: { enabled: false } }).approvals).toEqual({ ...block, enabled: false });
  });

  it.each([
    ['on', '`approvals` must be an object.'],
    [[1], '`approvals` must be an object.'],
    [{ enabled: 'yes' }, '`approvals.enabled` must be true or false.'],
    [{ showOther: 1 }, '`approvals.showOther` must be true or false.'],
    [{ denyCommentRequired: null }, '`approvals.denyCommentRequired` must be true or false.'],
    [{ concurrency: 0 }, '`approvals.concurrency` must be a whole number between 1 and 8.'],
    [{ concurrency: 9 }, '`approvals.concurrency` must be a whole number between 1 and 8.'],
    [{ concurrency: 2.5 }, '`approvals.concurrency` must be a whole number between 1 and 8.'],
    [{ concurrency: true }, '`approvals.concurrency` must be a whole number between 1 and 8.'],
    [{ useBulkEndpoint: 'sometimes' }, '`approvals.useBulkEndpoint` must be "auto", "always" or "never".'],
    [{ maxRows: 249 }, '`approvals.maxRows` must be a whole number between 250 and 20000.'],
    [{ maxRows: 20001 }, '`approvals.maxRows` must be a whole number between 250 and 20000.'],
    [{ maxRows: '5000' }, '`approvals.maxRows` must be a whole number between 250 and 20000.'],
  ])('rejects %j with the same message as config.py', (approvals, message) => {
    const parse = () => parseRuntimeConfig({ approvals });
    expect(parse).toThrow(RuntimeConfigError);
    try {
      parse();
    } catch (err) {
      expect((err as Error).message).toBe(message);
    }
  });
});
