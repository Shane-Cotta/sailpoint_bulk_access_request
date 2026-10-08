/**
 * Which demo scenario the URL asks for (?demo=<scenario>). Kept apart from demo.ts so the
 * production bundle only carries this check: main.ts loads demo.ts and its fixtures with a
 * dynamic import() when a scenario is named.
 */
export const DEMO_SCENARIOS = [
  'new', 'people', 'items', 'approver', 'approver-error', 'temporary', 'review', 'parts-review', 'submitted',
  'parts-submitted', 'history', 'approvals', 'approvals-partial',
] as const;

export type DemoScenario = (typeof DEMO_SCENARIOS)[number];

/** The scenario named in the URL, or null. Never inside an iframe (that is ISC). */
export function demoScenario(loc: Pick<Location, 'search' | 'hash'>, top = window.top === window.self): DemoScenario | null {
  if (!top) return null;
  const params = new URLSearchParams(loc.search || loc.hash.split('?')[1] || '');
  const value = params.get('demo');
  if (value === null) return null;
  return (DEMO_SCENARIOS as readonly string[]).includes(value) ? (value as DemoScenario) : 'new';
}
