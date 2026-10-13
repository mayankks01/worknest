import { useEffect, useState } from 'react';
import { orgRequest } from '../organizations/api';
import { ErrorNotice, failureMessage, type Page } from '../projects/ui';
import './activity.css';

type Alert = { id: string; kind: string; task_title: string; created_at: string; read_at: string | null; href: string };
type Audit = { id: string; action: string; actor_name: string; created_at: string; resource_id: string };
type Job = { id: string; kind: string; label: string; attempts: number; reason: string };
type Recovery = Page<Job> & { pending_events: number; oldest_event_at: string | null; reconciliation: { state: string; checked_at: string | null; corrected_counters: string[]; orphans_removed: number } | null };
const time = (value: string) => new Date(value).toLocaleString();
const label = (value: string) => value.replace(/[._]/g, ' ').replace(/^./, letter => letter.toUpperCase());

export default function ActivityPanel({ orgId, mode }: { orgId: string; mode: 'notifications' | 'audit' | 'operations' }) {
  const [alerts, setAlerts] = useState<Page<Alert> & { unread_count: number }>({ results: [], next_cursor: null, unread_count: 0 });
  const [audit, setAudit] = useState<Page<Audit>>({ results: [], next_cursor: null });
  const [recovery, setRecovery] = useState<Recovery | null>(null);
  const [emails, setEmails] = useState(true);
  const [unread, setUnread] = useState(false);
  const [kind, setKind] = useState('event');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [revision, setRevision] = useState(0);
  const route = mode === 'notifications' ? `notifications/?unread=${unread}` : mode === 'operations' ? `operations/?kind=${kind}` : 'audit/?';
  useEffect(() => {
    let active = true; setLoading(true); setError('');
    const read = async () => {
      try {
        const data = await orgRequest(`${orgId}/${route}`);
        const preferences = mode === 'notifications' ? await orgRequest(`${orgId}/notifications/preferences/`) : null;
        if (!active) return;
        if (mode === 'notifications') { setAlerts(data); setEmails(preferences.assignment_email); }
        else if (mode === 'audit') setAudit(data);
        else setRecovery(data);
      } catch (e) { if (active) setError(failureMessage(e)); }
      finally { if (active) setLoading(false); }
    };
    void read();
    return () => { active = false; };
  }, [orgId, mode, route, revision]);
  async function action(work: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('');
    try { await work(); } catch (e) { setError(failureMessage(e)); } finally { setBusy(false); }
  }
  async function more() {
    const cursor = mode === 'notifications' ? alerts.next_cursor : mode === 'audit' ? audit.next_cursor : recovery?.next_cursor;
    await action(async () => {
      const data = await orgRequest(`${orgId}/${route}&cursor=${encodeURIComponent(cursor!)}`);
      if (mode === 'notifications') setAlerts(old => ({ ...data, results: [...old.results, ...data.results] }));
      else if (mode === 'audit') setAudit(old => ({ ...data, results: [...old.results, ...data.results] }));
      else setRecovery(old => ({ ...data, results: [...(old?.results ?? []), ...data.results] }));
    });
  }
  const cursor = mode === 'notifications' ? alerts.next_cursor : mode === 'audit' ? audit.next_cursor : recovery?.next_cursor;
  return <section className="activity-panel">
    <div className="activity-heading"><div><span className="wn-eyebrow">{mode === 'notifications' ? 'STAY IN THE LOOP' : 'YOUR WORKSPACE RECORD'}</span><h2>{mode === 'notifications' ? 'Notifications' : mode === 'audit' ? 'Audit history' : 'Delivery & recovery'}</h2><p>{mode === 'notifications' ? 'Assignments and conversations from tasks you can access.' : mode === 'audit' ? 'A record of workspace actions, visible to Owners and Admins.' : 'Review delayed work and retry failed jobs after their service is restored.'}</p></div><button className="wn-secondary" disabled={busy || loading} onClick={() => setRevision(value => value + 1)}>Refresh</button></div>
    <ErrorNotice message={error} />{notice && <p className="activity-notice" role="status">{notice}</p>}
    {loading ? <div className="wn-panel activity-empty" role="status">Loading {mode === 'notifications' ? 'your updates' : 'workspace records'}…</div> : error ? <div className="wn-panel activity-empty"><p>The latest records could not be loaded. Refresh to try again.</p></div> : <>
      {mode === 'notifications' ? <>
        <div className="wn-panel activity-preferences"><div><h3>Assignment emails</h3><p>Receive an email when someone assigns a task to you in this organization. Invitations and account emails are still sent.</p></div><label className="activity-checkbox"><input type="checkbox" checked={emails} disabled={busy} onChange={e => { const enabled = e.target.checked; void action(async () => { const data = await orgRequest(`${orgId}/notifications/preferences/`, 'PATCH', { assignment_email: enabled }); setEmails(data.assignment_email); setNotice('Email preference saved for this organization.'); }); }} />Email me about assignments</label></div>
        <div className="activity-toolbar"><span className="activity-count">{alerts.unread_count} unread</span><label className="activity-checkbox"><input type="checkbox" checked={unread} disabled={busy} onChange={e => setUnread(e.target.checked)} />Unread only</label></div>
        <div className="wn-panel activity-feed">{alerts.results.map(item => <article className={`activity-item ${item.read_at ? '' : 'activity-unread'}`} key={item.id}><span className="activity-dot" aria-hidden="true" /><div><span className="activity-kind">{item.kind === 'assignment' ? 'Assigned to you' : 'New comment'}</span><h3><a href={item.href}>{item.task_title}</a></h3><time dateTime={item.created_at}>{time(item.created_at)}</time></div><button className="wn-secondary" disabled={busy} onClick={() => void action(async () => { await orgRequest(`${orgId}/notifications/${item.id}/`, 'PATCH', { read: !item.read_at }); setRevision(value => value + 1); })}>{item.read_at ? 'Mark unread' : 'Mark read'}</button></article>)}{!alerts.results.length && <div className="activity-empty"><h3>{unread ? 'You’re all caught up.' : 'Your next update will land here.'}</h3><p>Assignments and comments appear after processing. Use Refresh to check for new updates.</p></div>}</div>
      </> : mode === 'audit' ? <div className="wn-panel activity-feed">{audit.results.map(item => <article className="activity-item" key={item.id}><span className="activity-dot" aria-hidden="true" /><div><h3>{label(item.action)}</h3><p>{item.actor_name}</p><time dateTime={item.created_at}>{time(item.created_at)}</time></div></article>)}{!audit.results.length && <p className="activity-empty">No recorded actions yet.</p>}</div> : recovery && <>
        <div className="activity-summary"><div className="wn-panel"><span>Pending events</span><strong>{recovery.pending_events}</strong><small>{recovery.oldest_event_at ? `Oldest: ${time(recovery.oldest_event_at)}` : 'No queued events'}</small></div><div className="wn-panel"><span>Last storage & quota check</span><strong className="activity-date">{recovery.reconciliation?.checked_at ? time(recovery.reconciliation.checked_at) : 'Waiting for first check'}</strong><small>{recovery.reconciliation?.state === 'failed' ? 'Needs attention — see Reconciliation below' : 'Checks run automatically every five minutes'}</small></div></div>
        <div className="activity-toolbar"><label>Failed job type <select value={kind} disabled={busy} onChange={e => setKind(e.target.value)}><option value="event">Events & invitations</option><option value="email">Assignment emails</option><option value="file">File cleanup</option><option value="reconcile">Reconciliation</option></select></label></div>
        <div className="wn-panel activity-feed">{recovery.results.map(item => <article className="activity-item" key={item.id}><span className="activity-dot activity-failed" aria-hidden="true" /><div><h3>{label(item.label)}</h3><p>{item.attempts} attempts · {item.kind === 'file' ? 'Storage cleanup could not finish. Reserved space remains held.' : 'Processing could not finish. Check the supporting service before retrying.'}</p></div><button className="wn-secondary" disabled={busy} onClick={() => void action(async () => { await orgRequest(`${orgId}/operations/${item.kind}/${item.id}/retry/`, 'POST', {}); setNotice('Retry queued. Refresh shortly to check the result.'); setRevision(value => value + 1); })}>Retry job</button></article>)}{!recovery.results.length && <div className="activity-empty"><h3>No failed jobs in this category.</h3><p>WorkNest retries temporary failures automatically before listing them here.</p></div>}</div>
      </>}
      {cursor && <button className="wn-secondary activity-more" disabled={busy} onClick={() => void more()}>Load more</button>}
    </>}
  </section>;
}
