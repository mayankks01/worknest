import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { ApiError } from '../accounts/api';
import { orgRequest } from './api';
import './team.css';

type Member = { id: string; display_name: string; email: string; role: string };
type Invitation = { id: string; email: string; role: string; state: string; delivery_state: string; expires_at: string };
type Page<T> = { results: T[]; next_cursor: string | null };
type Roster = Page<Member> & { role: string; usage: { active_members: number; member_limit: number } };
type Action = { kind: 'remove' | 'transfer' | 'role'; member: Member; role?: string };
const messageOf = (e: unknown) => e instanceof Error ? e.message : 'Please try again.';

export default function TeamPanel({ orgId, onRole }: { orgId: string; onRole: (role: string) => void }) {
  const [roster, setRoster] = useState<Roster | null>(null);
  const [invites, setInvites] = useState<Page<Invitation>>({ results: [], next_cursor: null });
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [email, setEmail] = useState('');
  const [role, setRole] = useState('Member');
  const [action, setAction] = useState<Action | null>(null);
  const [password, setPassword] = useState('');
  const dialog = useRef<HTMLDialogElement>(null);
  const requestKey = useRef({ value: '', key: crypto.randomUUID() });
  const manager = roster?.role === 'Owner' || roster?.role === 'Admin';
  const owner = roster?.role === 'Owner';
  const canManage = (memberRole: string) => memberRole !== 'Owner' && (owner || (manager && ['Member', 'Viewer'].includes(memberRole)));
  const load = useCallback(async () => {
    try {
      const next: Roster = await orgRequest(`${orgId}/members/`);
      setRoster(next);
      if (next.role !== 'Owner') setRole(value => value === 'Admin' ? 'Member' : value);
      if (['Owner', 'Admin'].includes(next.role)) setInvites(await orgRequest(`${orgId}/invitations/`));
      else setInvites({ results: [], next_cursor: null });
    } catch (e) {
      setError(messageOf(e));
      if (e instanceof ApiError && [401, 403, 404].includes(e.status)) {
        setRoster(null); setInvites({ results: [], next_cursor: null });
        location.assign(e.status === 401 ? '/login' : '/orgs');
      }
    }
  }, [orgId]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (roster) onRole(roster.role); }, [roster?.role, onRole]);
  useEffect(() => {
    if (action) dialog.current?.showModal();
    else { dialog.current?.close(); setPassword(''); }
  }, [action]);
  async function run(work: () => Promise<unknown>, success: string) {
    setBusy(true); setError(''); setNotice('');
    try { await work(); setAction(null); setNotice(success); await load(); return true; }
    catch (e) { setError(messageOf(e)); if (e instanceof ApiError && [401, 403, 404].includes(e.status)) await load(); return false; }
    finally { setBusy(false); setPassword(''); }
  }
  async function invite(event: FormEvent) {
    event.preventDefault();
    const value = `${email.trim().toLowerCase()}:${role}`;
    if (requestKey.current.value !== value) requestKey.current = { value, key: crypto.randomUUID() };
    if (await run(() => orgRequest(`${orgId}/invitations/`, 'POST', { email, role }, requestKey.current.key), 'Invitation queued. Refresh to check delivery.')) {
      setEmail(''); requestKey.current = { value: '', key: crypto.randomUUID() };
    }
  }
  async function confirm(event: FormEvent) {
    event.preventDefault(); if (!action) return;
    const { kind, member } = action;
    await run(() => kind === 'transfer'
      ? orgRequest(`${orgId}/transfer-ownership/`, 'POST', { member_id: member.id, password })
      : orgRequest(`${orgId}/members/${member.id}/`, kind === 'remove' ? 'DELETE' : 'PATCH', kind === 'role' ? { role: action.role! } : undefined),
      kind === 'transfer' ? 'Ownership transferred. You are now an Admin.' : kind === 'remove' ? 'Member removed. Their organization access has ended.' : 'Role updated.');
  }
  async function more(kind: 'members' | 'invitations') {
    const cursor = kind === 'members' ? roster?.next_cursor : invites.next_cursor;
    if (!cursor) return;
    setBusy(true); setError('');
    try {
      const page = await orgRequest(`${orgId}/${kind}/?cursor=${encodeURIComponent(cursor)}`);
      if (kind === 'members') setRoster(old => old && ({ ...old, results: [...old.results, ...page.results], next_cursor: page.next_cursor }));
      else setInvites(old => ({ results: [...old.results, ...page.results], next_cursor: page.next_cursor }));
    } catch (e) { setError(messageOf(e)); } finally { setBusy(false); }
  }
  return <div className="team-layout">
    {notice && <p className="account-message success" role="status">{notice}</p>}
    {error && !action && <p className="account-message error" role="alert">{error}</p>}
    <section className="wn-panel team-roster"><div className="wn-section-heading"><div><h2>Your people</h2><p className="wn-muted">{roster ? `${roster.usage.active_members} of ${roster.usage.member_limit} member places used` : 'Loading your team…'}</p></div><button className="wn-secondary" disabled={busy} onClick={() => { setError(''); void load(); }}>Refresh</button></div>
      <div className="team-table-wrap" tabIndex={0} role="region" aria-label="Scrollable team table"><table className="team-table"><thead><tr><th>Member</th><th>Role</th><th>Actions</th></tr></thead><tbody>{roster?.results.map(member => <tr key={member.id}><td><div className="team-person"><span className="team-avatar">{member.display_name.slice(0, 2).toUpperCase()}</span><div><strong>{member.display_name}</strong><small>{member.email}</small></div></div></td><td>{canManage(member.role) ? <select aria-label={`Role for ${member.email}`} value={member.role} disabled={busy} onChange={e => { setError(''); setAction({ kind: 'role', member, role: e.target.value }); }}>{(owner ? ['Admin', 'Member', 'Viewer'] : ['Member', 'Viewer']).map(value => <option key={value}>{value}</option>)}</select> : <span className="wn-role">{member.role}</span>}</td><td><div className="team-actions">{canManage(member.role) && <button disabled={busy} onClick={() => { setError(''); setAction({ kind: 'remove', member }); }}>Remove</button>}{owner && member.role !== 'Owner' && <button disabled={busy} onClick={() => { setError(''); setAction({ kind: 'transfer', member }); }}>Make Owner</button>}{member.role === 'Owner' && <span className="wn-muted">Workspace owner</span>}</div></td></tr>)}</tbody></table></div>
      {roster?.next_cursor && <button disabled={busy} className="wn-secondary wn-more" onClick={() => void more('members')}>Load more members</button>}
    </section>
    {manager && <section className="wn-panel team-invite"><span className="wn-eyebrow">GROW YOUR TEAM</span><h2>Invite a teammate</h2><p className="wn-muted">They’ll receive a personal link, valid for 72 hours.</p><form className="account-form" onSubmit={invite}><label>Email address<input type="email" autoComplete="email" required maxLength={254} placeholder="teammate@company.com" value={email} onChange={e => setEmail(e.target.value)} /></label><label>Organization role<select value={owner ? role : role === 'Admin' ? 'Member' : role} onChange={e => setRole(e.target.value)}>{(owner ? ['Admin', 'Member', 'Viewer'] : ['Member', 'Viewer']).map(value => <option key={value}>{value}</option>)}</select></label><p className="team-role-help">Admins manage the workspace and team. Members contribute to work. Viewers have read-only access.</p><button className="wn-button" disabled={busy || !email}>{busy ? 'Please wait…' : 'Send invitation'}</button></form>{['localhost', '127.0.0.1'].includes(location.hostname) && <p className="team-local-note">Local preview: emails arrive in the <a href="http://127.0.0.1:8025" target="_blank" rel="noreferrer">test inbox ↗</a>.</p>}</section>}
    {manager && <section className="wn-panel team-invitations"><div className="wn-section-heading"><div><h2>Invitations</h2><p className="wn-muted">Track delivery and manage outstanding invitations.</p></div></div>{invites.results.length ? <div className="team-table-wrap" tabIndex={0} role="region" aria-label="Scrollable team table"><table className="team-table"><thead><tr><th>Email</th><th>Role</th><th>Status</th><th>Actions</th></tr></thead><tbody>{invites.results.map(item => <tr key={item.id}><td><strong>{item.email}</strong><small>Expires {new Date(item.expires_at).toLocaleString()}</small></td><td>{item.role}</td><td><span className={`team-state ${item.state}`}>{item.state}</span><small>{item.state === 'pending' ? `Email ${item.delivery_state === 'sent' ? 'sent' : item.delivery_state === 'failed' ? 'failed — resend to retry' : item.delivery_state}` : ''}</small></td><td><div className="team-actions">{canManage(item.role) && ['pending', 'expired'].includes(item.state) && <><button disabled={busy} onClick={() => void run(() => orgRequest(`${orgId}/invitations/${item.id}/resend/`, 'POST', {}), 'A fresh invitation is queued. The old link no longer works.')}>Resend</button><button disabled={busy} onClick={() => void run(() => orgRequest(`${orgId}/invitations/${item.id}/revoke/`, 'POST', {}), 'Invitation revoked.')}>Revoke</button></>}</div></td></tr>)}</tbody></table></div> : <div className="team-empty"><h3>A seat at the table starts here.</h3><p>Invite someone to join your organization. Their invitation will appear here.</p></div>}{invites.next_cursor && <button disabled={busy} className="wn-secondary wn-more" onClick={() => void more('invitations')}>Load more invitations</button>}</section>}
    <dialog className="team-dialog" ref={dialog} onCancel={event => { if (busy) event.preventDefault(); else setAction(null); }} aria-labelledby="team-dialog-title"><form className="account-form" onSubmit={confirm}><h2 id="team-dialog-title">{action?.kind === 'transfer' ? 'Transfer ownership?' : action?.kind === 'remove' ? 'Remove this member?' : 'Change this role?'}</h2><p>{action?.kind === 'transfer' ? `${action.member.display_name} will become the Owner. Your role will become Admin. Enter your current password to confirm.` : action?.kind === 'remove' ? `${action.member.display_name} will immediately lose access to this organization. Their other workspaces remain available.` : `${action?.member.display_name} will become ${action?.role}. Their permissions change immediately.`}</p>{action?.kind === 'transfer' && <label>Current password<input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required maxLength={128} /></label>}{error && <p role="alert" className="account-message error">{error}</p>}<div className="wn-actions"><button type="button" className="wn-secondary" disabled={busy} onClick={() => setAction(null)}>Cancel</button><button className="wn-button" disabled={busy}>{busy ? 'Saving…' : 'Confirm'}</button></div></form></dialog>
  </div>;
}
