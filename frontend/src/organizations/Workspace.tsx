import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { ApiError, postAuth, type Account } from '../accounts/api';
import { Icon } from '../components/Icon';
import { orgRequest, type Organization, type OrganizationPage } from './api';
import './workspace.css';
import TeamPanel from './TeamPanel';

const orgLink = (org: Organization) => `/orgs/${org.id}`;

export default function Workspace({ user }: { user: Account }) {
  const match = location.pathname.match(/^\/orgs\/([0-9a-f-]{36})(\/(?:settings|members))?\/?$/);
  const orgId = match?.[1];
  const settings = match?.[2] === '/settings';
  const team = match?.[2] === '/members';
  const creating = location.pathname === '/orgs/new';
  const [orgs, setOrgs] = useState<Organization[]>([]);
  const [org, setOrg] = useState<Organization | null>(null);
  const updateRole = useCallback((role: string) => setOrg(old => old && old.membership.role !== role ? ({ ...old, membership: { ...old.membership, role: role as Organization['membership']['role'] } }) : old), []);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [moreBusy, setMoreBusy] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  // Full-page workspace navigation intentionally discards all cached data and drafts.
  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const [page, detail]: [OrganizationPage, { organization: Organization } | null] = await Promise.all([
          orgRequest(), orgId ? orgRequest(`${orgId}/`) : Promise.resolve(null),
        ]);
        if (!active) return;
        setOrgs(page.results); setCursor(page.next_cursor); setOrg(detail?.organization ?? null); setError('');
      } catch (failure) {
        if (!active) return;
        setOrg(null); setOrgs([]); setCursor(null);
        setError(failure instanceof Error ? failure.message : 'Unable to load your organizations.');
        if (failure instanceof ApiError && failure.status === 401) location.replace('/login');
      } finally { if (active) setLoading(false); }
    }
    void load();
    const refresh = () => { if (document.visibilityState === 'visible') void load(); };
    const restored = (event: PageTransitionEvent) => { if (event.persisted) location.reload(); };
    window.addEventListener('pageshow', restored);
    window.addEventListener('focus', refresh);
    document.addEventListener('visibilitychange', refresh);
    const timer = window.setInterval(refresh, 60000);
    return () => { active = false; window.removeEventListener('pageshow', restored); window.removeEventListener('focus', refresh); document.removeEventListener('visibilitychange', refresh); clearInterval(timer); };
  }, [orgId]);
  async function more() {
    setMoreBusy(true);
    try { const page: OrganizationPage = await orgRequest(`?cursor=${encodeURIComponent(cursor!)}`); setOrgs(previous => [...previous, ...page.results]); setCursor(page.next_cursor); }
    catch (failure) { setError(failure instanceof Error ? failure.message : 'Unable to load more.'); }
    finally { setMoreBusy(false); }
  }
  async function signOut() {
    setSigningOut(true);
    try { await postAuth('logout'); location.assign('/login'); }
    catch (failure) { setError(failure instanceof Error ? failure.message : 'Unable to sign out.'); setSigningOut(false); }
  }
  const canEdit = org && ['Owner', 'Admin'].includes(org.membership.role);
  const choices = org && !orgs.some(item => item.id === org.id) ? [org, ...orgs] : orgs;
  return <div className="wn-shell">
    <aside className="wn-sidebar">
      <a className="wn-brand" href="/orgs"><span className="brand-symbol"><Icon name="logo" /></span>WorkNest</a>
      <label className="wn-switch"><span>YOUR WORKSPACE</span><select aria-label="Switch organization" value={org?.id ?? ''} onChange={event => location.assign(event.target.value ? `/orgs/${event.target.value}` : '/orgs')}><option value="">All organizations</option>{choices.map(item => <option value={item.id} key={item.id}>{item.display_name}</option>)}</select></label>
      <nav aria-label="Workspace navigation"><a className={!orgId ? 'active' : ''} href="/orgs"><Icon name="organizations" />Organizations</a>{org && <><a className={!settings && !team ? 'active' : ''} href={orgLink(org)}><Icon name="home" />Overview</a><a className={team ? 'active' : ''} href={`${orgLink(org)}/members`}><Icon name="organizations" />Members</a><a className={settings ? 'active' : ''} href={`${orgLink(org)}/settings`}><Icon name="settings" />Settings</a></>}</nav>
      <div className="wn-sidebar-bottom"><span className="wn-private"><Icon name="check" />Your private workspace</span><button onClick={() => void signOut()} disabled={signingOut}>{signingOut ? 'Signing out…' : 'Sign out'} <Icon name="arrow" /></button></div>
    </aside>
    <div className="wn-content"><header className="wn-topbar"><span>Workspace <span className="wn-divider">/</span> {creating ? 'Create organization' : org?.display_name ?? 'Organizations'}</span><div className="wn-profile"><span className="profile-avatar">{user.display_name.slice(0, 2).toUpperCase()}</span><span>{user.display_name}</span></div></header>
      <main className="wn-main">
        {loading ? <div className="wn-panel wn-empty" role="status">Opening your workspace…</div> : error ? <div className="wn-panel wn-empty" role="alert"><Icon name="info" /><h1>Let’s get you back to work.</h1><p>{error}</p><div className="wn-actions"><a className="wn-button" href="/orgs">Your organizations</a><button className="wn-secondary" onClick={() => location.reload()}>Try again</button></div></div> : creating ? <><a className="wn-back" href="/orgs">← Your organizations</a><div className="wn-heading"><div><span className="wn-eyebrow">A PLACE FOR YOUR TEAM</span><h1>Create an organization</h1><p>Give your next big thing a space of its own.</p></div></div><OrganizationForm /></> : org ? <>
          <div className="wn-heading"><div className="wn-title-group"><span className="wn-org-icon">{org.display_name[0].toUpperCase()}</span><div><h1>{org.display_name}</h1><p>{team ? 'The people behind your next big thing.' : settings ? 'Organization settings' : 'Your organization, at a glance.'}</p></div></div><span className="wn-role">{org.membership.role}</span></div>
          <div className="wn-tabs"><a className={!settings && !team ? 'active' : ''} href={orgLink(org)}>Overview</a><a className={team ? 'active' : ''} href={`${orgLink(org)}/members`}>Members</a><a className={settings ? 'active' : ''} href={`${orgLink(org)}/settings`}>Settings</a></div>
          {team ? <TeamPanel orgId={org.id} onRole={updateRole} /> : settings ? canEdit ? <OrganizationForm key={org.id} organization={org} onSaved={updated => { setOrg(updated); setOrgs(items => items.map(item => item.id === updated.id ? updated : item)); }} /> : <div className="wn-panel wn-empty"><Icon name="settings" /><h2>Managed by your organization</h2><p>An Owner or Admin can update these settings.</p><p>{org.display_name} · {org.timezone}</p></div> : <><div className="wn-stats"><div className="wn-stat"><span className="wn-stat-icon blue"><Icon name="organizations" /></span><div><small>Your access</small><strong>{org.membership.role}</strong><span>In this organization</span></div></div><div className="wn-stat"><span className="wn-stat-icon green"><Icon name="clock" /></span><div><small>Workspace timezone</small><strong>{org.timezone}</strong><span>One shared schedule</span></div></div><div className="wn-stat"><span className="wn-stat-icon purple"><Icon name="check" /></span><div><small>Workspace status</small><strong>Active</strong><span>Private to your organization</span></div></div></div>
            <section className="wn-welcome wn-panel"><div><span className="wn-eyebrow">MAKE YOURSELF AT HOME</span><h2>A fresh space for great work.</h2><p>Your organization is ready. This is where your team’s projects and progress will come together.</p>{canEdit && <a className="wn-button" href={`${orgLink(org)}/settings`}>Review organization settings <Icon name="arrow" /></a>}</div><div className="wn-illustration" aria-hidden="true"><span className="wn-orbit"><Icon name="organizations" /></span><span className="wn-note"><Icon name="check" /> A shared direction</span><span className="wn-note second"><Icon name="folder" /> Room to grow</span></div></section>
            <div className="wn-panel wn-details"><h2>Organization details</h2><dl><div><dt>Workspace address</dt><dd>{org.slug}</dd></div><div><dt>Created</dt><dd>{new Date(org.created_at).toLocaleDateString(undefined, { day: 'numeric', month: 'long', year: 'numeric' })}</dd></div><div><dt>Your role</dt><dd>{org.membership.role}</dd></div></dl><p className="wn-muted"><a href={`${orgLink(org)}/members`}>Meet your team and manage invitations →</a></p></div></>}
        </> : <><div className="wn-heading"><div><span className="wn-eyebrow">YOUR WORK, TOGETHER</span><h1>Hello, {user.display_name.split(' ')[0]} <span aria-hidden="true">👋</span></h1><p>A little clarity across every team you belong to.</p></div><a className="wn-button" href="/orgs/new"><Icon name="plus" />Create organization</a></div>
          <section className="wn-panel wn-organization-list"><div className="wn-section-heading"><h2>Your organizations</h2><span>Private workspaces</span></div>{orgs.length ? <div className="wn-org-grid">{orgs.map(item => <a className="wn-org-card" key={item.id} href={orgLink(item)}><div><span className="wn-org-icon">{item.display_name[0].toUpperCase()}</span><span className="wn-role">{item.membership.role}</span></div><h3>{item.display_name}</h3><p>{item.slug}</p><footer><span>{item.timezone}</span><span>Open workspace <Icon name="arrow" /></span></footer></a>)}</div> : <div className="wn-empty"><span className="wn-empty-icon"><Icon name="organizations" /></span><h2>Good work starts with a shared space.</h2><p>Create your first organization to bring your work together.<br />You’ll be its Owner, with settings in your hands.</p><a className="wn-button" href="/orgs/new"><Icon name="plus" />Create your first organization</a></div>}{cursor && <button className="wn-secondary wn-more" disabled={moreBusy} onClick={() => void more()}>{moreBusy ? 'Loading…' : 'Load more organizations'}</button>}</section>
          <div className="wn-tip"><Icon name="info" /><p>Each organization has its own members and workspace. Switch organizations from the menu to keep your work in the right place.</p></div></>}
      </main><footer className="wn-footer">WorkNest <span>Your team. Your space.</span></footer>
    </div>
  </div>;
}

function OrganizationForm({ organization, onSaved }: { organization?: Organization; onSaved?: (org: Organization) => void }) {
  const [name, setName] = useState(organization?.display_name ?? '');
  const [slug, setSlug] = useState(organization?.slug ?? '');
  const [slugEdited, setSlugEdited] = useState(!!organization);
  const [zone, setZone] = useState(organization?.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [success, setSuccess] = useState(false);
  const [errors, setErrors] = useState<Record<string, string[]>>({});
  const key = useRef(crypto.randomUUID());
  const uncertain = useRef(false);
  const notice = useRef<HTMLDivElement>(null);
  function edited() { setMessage(''); setErrors({}); if (!uncertain.current) key.current = crypto.randomUUID(); }
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setMessage(''); setErrors({}); setSuccess(false);
    try {
      const result = await orgRequest(organization ? `${organization.id}/` : '', organization ? 'PATCH' : 'POST', { display_name: name, slug, timezone: zone }, key.current);
      uncertain.current = false;
      if (!organization) { location.assign(orgLink(result.organization)); return; }
      onSaved?.(result.organization); setSuccess(true); setMessage('Organization settings saved.');
    } catch (failure) {
      uncertain.current = failure instanceof ApiError && (!failure.status || failure.status >= 500);
      setMessage(failure instanceof Error ? failure.message : 'Please try again.');
      if (failure instanceof ApiError) setErrors(failure.details);
      if (failure instanceof ApiError && failure.status === 409) setMessage('This creation request already completed with other details. Open your organizations to find it before starting a new request.');
      if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) location.assign('/orgs');
    } finally { setBusy(false); requestAnimationFrame(() => notice.current?.focus()); }
  }
  return <section className="wn-panel wn-form-panel"><div className="wn-form-intro"><span className="wn-stat-icon blue"><Icon name="organizations" /></span><div><h2>{organization ? 'General settings' : 'The essentials'}</h2><p>{organization ? 'Keep your organization details up to date.' : 'You can update these details as your team grows.'}</p></div></div>
    {message && <div ref={notice} tabIndex={-1} role={success ? 'status' : 'alert'} className={`account-message ${success ? 'success' : 'error'}`}>{message}</div>}
    <form className="account-form wn-org-form" onSubmit={submit}>
      <label htmlFor="org-name">Organization name<input id="org-name" value={name} required maxLength={80} placeholder="e.g. TechNova" aria-invalid={!!errors.display_name} aria-describedby="org-name-error" onChange={event => { edited(); setName(event.target.value); if (!slugEdited) setSlug(event.target.value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 63)); }} /><span id="org-name-error" className="field-error">{errors.display_name?.join(' ')}</span></label>
      <label htmlFor="org-slug">Workspace address<input id="org-slug" value={slug} required minLength={3} maxLength={63} pattern="[a-z0-9]+(-[a-z0-9]+)*" placeholder="e.g. technova" aria-invalid={!!errors.slug} aria-describedby="org-slug-hint org-slug-error" onChange={event => { edited(); setSlugEdited(true); setSlug(event.target.value); }} /><small id="org-slug-hint">A unique name using lowercase letters, numbers, and hyphens.</small><span id="org-slug-error" className="field-error">{errors.slug?.join(' ')}</span></label>
      <label htmlFor="org-timezone">Timezone<input id="org-timezone" list="timezones" value={zone} required maxLength={64} aria-invalid={!!errors.timezone} aria-describedby="org-zone-error" onChange={event => { edited(); setZone(event.target.value); }} /><datalist id="timezones">{['UTC', 'Asia/Kolkata', 'Asia/Calcutta', 'Asia/Singapore', 'Europe/London', 'Europe/Berlin', 'America/New_York', 'America/Los_Angeles', 'Australia/Sydney'].map(value => <option key={value} value={value} />)}</datalist><span id="org-zone-error" className="field-error">{errors.timezone?.join(' ')}</span></label>
      {!organization && <div className="wn-form-note"><Icon name="check" /><span>You’ll be the <strong>Owner</strong> of this organization. Each workspace keeps its data separate.</span></div>}
      <div className="wn-form-actions"><a className="wn-secondary" href={organization ? orgLink(organization) : '/orgs'}>Cancel</a><button className="wn-button" type="submit" disabled={busy}>{busy ? 'Saving…' : organization ? 'Save changes' : 'Create organization'}<Icon name="arrow" /></button></div>
    </form></section>;
}
