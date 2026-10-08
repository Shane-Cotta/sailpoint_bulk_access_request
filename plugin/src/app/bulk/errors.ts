/**
 * Where the error happened, so the hint fits: submitting through the workflow test endpoint needs ORG_ADMIN,
 * while the Approvals tab works for any approver on their own approvals. (Submitting through the Launcher
 * has its own hint: see launcherAccessMessage.)
 */
export type ErrorContext = 'submit' | 'approvals';

type ApiErrorLike = { status?: number; message?: string; body?: unknown };

function bodyText(body: unknown): string {
  const b = body as { messages?: { text?: string }[]; message?: string; detailCode?: string } | undefined;
  return b?.messages?.map((m) => m.text).filter(Boolean).join('; ') || b?.message || '';
}

/** Turn an API error into a sentence, with a hint for permission and throttling errors. */
export function describeError(err: unknown, context: ErrorContext = 'submit'): string {
  const e = err as ApiErrorLike;
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
  return bodyText(e?.body) || (err instanceof Error ? err.message : String(err));
}

/**
 * Starting a Launcher without its access: 401/403, or (verified live for a user without Launcher access on
 * GET /v2025/launchers) a 500 whose message says "insufficient authorization".
 */
export function isLauncherAccessDenied(err: unknown): boolean {
  const e = err as ApiErrorLike;
  if (e?.status === 401 || e?.status === 403) return true;
  return e?.status === 500 && /insufficient authori[sz]ation/i.test(`${bodyText(e.body)} ${e.message ?? ''}`);
}

/** What a user who may not use the Launcher is told (launcher submit mode). */
export function launcherAccessMessage(accessName: string): string {
  return `You need the ${accessName} access to submit; request it in the Request Center.`;
}

/** An error while submitting through the Launcher, as a sentence. */
export function describeLauncherError(err: unknown, accessName: string): string {
  if (isLauncherAccessDenied(err)) return launcherAccessMessage(accessName);
  return describeError(err);
}
