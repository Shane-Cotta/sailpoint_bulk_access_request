import type { RuntimeConfig } from './runtime-config';

/**
 * Where the error happened, so the hint fits: a read (people, catalog, history, following a submission), or the
 * Approvals tab (any approver, on their own approvals). Submitting has its own wording: describeSubmitError.
 */
export type ErrorContext = 'read' | 'approvals';

type ApiErrorLike = { status?: number; message?: string; body?: unknown };

function bodyText(body: unknown): string {
  const b = body as { messages?: { text?: string }[]; message?: string } | undefined;
  return b?.messages?.map((m) => m.text).filter(Boolean).join('; ') || b?.message || '';
}

const refused = (err: unknown) => {
  const status = (err as ApiErrorLike)?.status;
  return status === 401 || status === 403;
};

/**
 * Turn an API error into a sentence, with a hint for permission and throttling errors. `what` names what couldn't
 * be loaded ("the catalog"); a refused read says so instead of guessing at the missing right.
 */
export function describeError(err: unknown, context: ErrorContext = 'read', what = 'this'): string {
  const e = err as ApiErrorLike;
  if (refused(err)) {
    if (context === 'approvals') {
      return `SailPoint refused the call (HTTP ${e.status}). You can only decide approvals that are assigned to you. `
        + 'If it is yours, your session may have expired: reload the page and try again.';
    }
    return `SailPoint refused the call (HTTP ${e.status}), so ${what} couldn't be loaded. Your session may have expired: `
      + 'reload the page. If it keeps happening, your account may not be allowed to read it; ask an administrator.';
  }
  if (e?.status === 429) {
    return 'SailPoint is limiting how fast this page may call it (HTTP 429). Wait a moment, then try again.';
  }
  return bodyText(e?.body) || (err instanceof Error ? err.message : String(err));
}

/**
 * Starting a Launcher without its access: 401/403, or (verified live for a user without Launcher access on
 * GET /launchers/v1) a 500 whose message says "insufficient authorization".
 */
export function isLauncherAccessDenied(err: unknown): boolean {
  const e = err as ApiErrorLike;
  if (refused(err)) return true;
  return e?.status === 500 && /insufficient authori[sz]ation/i.test(`${bodyText(e.body)} ${e.message ?? ''}`);
}

type SubmitConfig = Pick<RuntimeConfig, 'submit' | 'launcherAccessName'>;

/** What a user without the Launcher Access profile is told (before anything is sent). */
export function msgNeedsLauncherAccess(cfg: Pick<RuntimeConfig, 'launcherAccessName'>): string {
  return `To submit, you need '${cfg.launcherAccessName}'. Request it in the Request Center.`;
}

/**
 * An error while submitting, worded for the submit mode: through the Launcher a refusal means the user lacks the
 * Launcher Access profile; a direct request that SailPoint refuses means the user may not request access for those
 * people (the tenant's "request for others" settings), or the session expired.
 */
export function describeSubmitError(err: unknown, cfg: SubmitConfig): string {
  if (cfg.submit === 'launcher') {
    if (isLauncherAccessDenied(err)) return msgNeedsLauncherAccess(cfg);
  } else if (refused(err)) {
    return `SailPoint refused the request (HTTP ${(err as ApiErrorLike).status}). Your account may not be allowed to `
      + 'request access for these people (the tenant\'s "request for others" settings), or your session expired: '
      + 'reload the page and try again.';
  }
  return describeError(err);
}
