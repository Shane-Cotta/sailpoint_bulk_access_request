/** Turn an API error into a sentence, with the ORG_ADMIN hint for permission errors. */
export function describeError(err: unknown): string {
  const e = err as { status?: number; message?: string; body?: unknown };
  if (e?.status === 401 || e?.status === 403) {
    return `SailPoint refused the call (HTTP ${e.status}). This page needs ORG_ADMIN (the right to test `
      + 'workflows). Use the Bulk Access Request Launcher in the Launchpad instead.';
  }
  const body = e?.body as { messages?: { text?: string }[]; message?: string } | undefined;
  const detail = body?.messages?.map((m) => m.text).filter(Boolean).join('; ') || body?.message;
  return detail || (err instanceof Error ? err.message : String(err));
}
