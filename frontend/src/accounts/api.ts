export type Account = { id: string; email: string; display_name: string; verified_at: string | null };
export type Session = { user: Account | null; csrf_token: string };
export class ApiError extends Error {
  constructor(message: string, public details: Record<string, string[]> = {}, public status = 0, public currentVersion?: number) { super(message); }
}

export async function decode(response: Response) {
  if (response.status === 204) return {};
  let result;
  try { result = await response.json(); } catch { throw new ApiError('WorkNest is unavailable. Please try again shortly.'); }
  if (!response.ok) {
    const wait = response.headers.get('Retry-After');
    throw new ApiError(`${result.error?.message ?? 'Something went wrong.'}${response.status === 429 && wait ? ` Try again in ${Math.ceil(Number(wait) / 60)} minute(s).` : ''}`, result.error?.details, response.status, result.error?.details?.current_version);
  }
  return result;
}

export async function getSession(): Promise<Session> {
  try { return await decode(await fetch('/api/v1/auth/session/', { credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(10000) })); }
  catch (error) { if (error instanceof ApiError) throw error; throw new ApiError('Unable to connect to WorkNest. Check your connection and try again.'); }
}

export async function postAuth(endpoint: string, data: Record<string, string> = {}) {
  const session = await getSession();
  try {
    return await decode(await fetch(`/api/v1/auth/${endpoint}/`, {
      method: 'POST', credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(10000),
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': session.csrf_token }, body: JSON.stringify(data),
    }));
  } catch (error) { if (error instanceof ApiError) throw error; throw new ApiError('Unable to connect. Please try again.'); }
}
