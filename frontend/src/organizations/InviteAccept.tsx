import { useState, type FormEvent } from 'react';
import { ApiError, decode, getSession, postAuth, type Account } from '../accounts/api';

async function invitationRequest(action: string, token: string) {
  const session = await getSession();
  try {
    return await decode(await fetch(`/api/v1/invitations/${action}/`, { method: 'POST', credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(10000), headers: { 'Content-Type': 'application/json', 'X-CSRFToken': session.csrf_token }, body: JSON.stringify({ token }) }));
  } catch (e) { if (e instanceof ApiError) throw e; throw new ApiError('Unable to connect. Please try again.'); }
}
export default function InviteAccept({ user, token, refresh }: { user: Account | null; token: string; refresh: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [preview, setPreview] = useState<{ organization_name: string; role: string; expires_at: string } | null>(null);
  async function act(work: () => Promise<unknown>) {
    setBusy(true); setMessage('');
    try { await work(); } catch (e) { setMessage(e instanceof Error ? e.message : 'Please try again.'); } finally { setBusy(false); }
  }
  async function login(e: FormEvent<HTMLFormElement>) {
    e.preventDefault(); const form = new FormData(e.currentTarget);
    await act(async () => { await postAuth('login', { email: String(form.get('email')), password: String(form.get('password')) }); await refresh(); });
  }
  return <><span className="account-kicker">A PLACE ON THE TEAM</span><h2>{preview ? `Join ${preview.organization_name}` : 'You’re invited.'}</h2><p className="account-description">Sign in with the email address that received this invitation.</p>
    {!token ? <p className="account-message">Reopen the invitation link from your email. If it has expired, ask the organization’s Owner or Admin to resend it.</p> : !user ? <><form className="account-form" onSubmit={login}><label>Email address<input type="email" name="email" required autoComplete="email" maxLength={254} /></label><label>Password<input type="password" name="password" required autoComplete="current-password" maxLength={128} /></label><button className="account-primary" disabled={busy}>Sign in to review invitation</button></form><p className="account-alternate">New here? <a href="/register" target="_blank" rel="noreferrer">Create and verify an account ↗</a>, then return to this invitation.</p></> : !user.verified_at ? <><p>Verify <strong>{user.email}</strong> before joining.</p><button className="account-primary" disabled={busy} onClick={() => void act(async () => { const result = await postAuth('resend-verification', { email: user.email }); setMessage(result.message); })}>Resend verification email</button><button className="account-link" disabled={busy} onClick={() => void act(refresh)}>I’ve verified my email — refresh</button></> : <><p className="account-identity">Signed in as {user.email}</p>{preview ? <><p>Your invited role is <strong>{preview.role}</strong>. If you already belong to this organization, your current role stays the same.</p><p className="account-security-note">Expires {new Date(preview.expires_at).toLocaleString()}.</p><button className="account-primary" disabled={busy} onClick={() => void act(async () => { const result = await invitationRequest('accept', token); location.assign(`/orgs/${result.organization_id}/members`); })}>{busy ? 'Joining…' : 'Accept invitation'}</button></> : <button className="account-primary" disabled={busy} onClick={() => void act(async () => setPreview(await invitationRequest('preview', token)))}>{busy ? 'Checking…' : 'Review invitation'}</button>}</>}
    {message && <p className="account-message" role="alert">{message}</p>}{user && <button className="account-link" disabled={busy} onClick={() => void act(async () => { await postAuth('logout'); setPreview(null); await refresh(); })}>Use a different account</button>}
  </>;
}
