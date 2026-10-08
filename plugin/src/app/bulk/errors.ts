/**
 * Where the error happened, so the hint fits: submitting needs ORG_ADMIN (the workflow test
 * endpoint), while the Approvals tab works for any approver on their own approvals.
 */
export type ErrorContext = 'submit' | 'approvals';

/** Turn an API error into a sentence, with a hint for permission and throttling errors. */
export function describeError(err: unknown, context: ErrorContext = 'submit'): string {
  const e = err as { status?: number; message?: string; body?: unknown };
  if (e?.status === 401 || e?.status === 403) {
    if (context === 'approvals') {
      return `SailPoint refused the call (HTTP ${e.status}). You can only decide approvals that are assigned to you. `
        + 'If it is yours, your session may have expired: reload the page and try again.';
    }
    return `SailPoint refused the call (HTTP ${e.status}). This page needs ORG_ADMIN (the right to test `
      + 'workflows). Use the Bulk Access Request Launcher in the Launchpad instead.';
  }
  if (e?.status === 429) {
    return 'SailPoint is limiting how fast this page may call it (HTTP 429). Wait a moment, then try again.';
  }
  const body = e?.body as { messages?: { text?: string }[]; message?: string } | undefined;
  const detail = body?.messages?.map((m) => m.text).filter(Boolean).join('; ') || body?.message;
  return detail || (err instanceof Error ? err.message : String(err));
}
