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
 * GET /v2025/launchers) a 500 whose message says "insufficient authorization".
 */
export function isLauncherAccessDenied(err: unknown): boolean {
  const e = err as ApiErrorLike;
  if (refused(err)) return true;
  return e?.status === 500 && /insufficient authori[sz]ation/i.test(`${bodyText(e.body)} ${e.message ?? ''}`);
}

type SubmitConfig = Pick<RuntimeConfig, 'submit' | 'launcherName' | 'launcherAccessName'>;

/**
 * An error while submitting, worded for the submit mode: through the Launcher a refusal means the user lacks the
 * Launcher Access profile; through the workflow test endpoint it means they aren't ORG_ADMIN.
 */
export function describeSubmitError(err: unknown, cfg: SubmitConfig): string {
  if (cfg.submit === 'launcher') {
    if (isLauncherAccessDenied(err)) {
      return `To submit, you need '${cfg.launcherAccessName}'. Request it in the Request Center.`;
    }
  } else if (refused(err)) {
    return `SailPoint refused the call (HTTP ${(err as ApiErrorLike).status}). Submitting from this page needs ORG_ADMIN `
      + `(the right to test workflows). Use the ${cfg.launcherName} Launcher in the Launchpad instead.`;
  }
  return describeError(err);
}
