import { ApiError, decode, getSession } from '../accounts/api';

export type Organization = {
  id: string; display_name: string; slug: string; timezone: string; state: string;
  created_at: string; membership: { id: string; role: 'Owner' | 'Admin' | 'Member' | 'Viewer' };
};
export type OrganizationPage = { results: Organization[]; next_cursor: string | null };

export async function orgRequest(path = '', method = 'GET', data?: Record<string, string>, key?: string) {
  try {
    const headers: Record<string, string> = {};
    if (method !== 'GET') {
      const session = await getSession();
      headers['X-CSRFToken'] = session.csrf_token;
      headers['Content-Type'] = 'application/json';
      if (key) headers['Idempotency-Key'] = key;
    }
    return await decode(await fetch(`/api/v1/orgs/${path}`, {
      method, headers, credentials: 'same-origin', cache: 'no-store',
      signal: AbortSignal.timeout(10000), body: data ? JSON.stringify(data) : undefined,
    }));
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError('Unable to connect. Your request may have completed. Retry with the same details to check.');
  }
}
