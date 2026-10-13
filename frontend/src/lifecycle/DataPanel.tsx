import { useEffect, useRef, useState, type FormEvent } from 'react';
import { orgRequest } from '../organizations/api';
import { ErrorNotice, failureMessage, type Page } from '../projects/ui';
import '../activity/activity.css';
import './lifecycle.css';

type ExportJob = { id: string; state: string; created_at: string; expires_at: string | null; download_url: string | null };
type Lifecycle = { state: string; deletion_due_at: string | null; maintenance_state: string };
export default function DataPanel({ orgId, slug, owner, onLifecycle }: { orgId: string; slug: string; owner: boolean; onLifecycle: (data: Lifecycle) => void }) {
  const [jobs, setJobs] = useState<Page<ExportJob>>({ results: [], next_cursor: null });
  const [lifecycle, setLifecycle] = useState<Lifecycle | null>(null);
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false);
  const [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [password, setPassword] = useState(''), [confirmation, setConfirmation] = useState('');
  const key = useRef(crypto.randomUUID());
  async function refresh() {
    const [exports, status] = await Promise.all([orgRequest(`${orgId}/exports/`), orgRequest(`${orgId}/deletion/`)]);
    setJobs(exports); setLifecycle(status);
  }
  useEffect(() => { let active = true; Promise.all([orgRequest(`${orgId}/exports/`), orgRequest(`${orgId}/deletion/`)]).then(([exports, status]) => { if (active) { setJobs(exports); setLifecycle(status); } }).catch(e => { if (active) setError(failureMessage(e)); }).finally(() => { if (active) setLoading(false); }); return () => { active = false; }; }, [orgId]);
  async function action(work: () => Promise<void>) { setBusy(true); setError(''); setNotice(''); try { await work(); } catch (e) { setError(failureMessage(e)); } finally { setBusy(false); } }
  async function createExport() { await action(async () => { await orgRequest(`${orgId}/exports/`, 'POST', {}, key.current); key.current = crypto.randomUUID(); setNotice('Your export is queued. Refresh shortly to check its progress.'); await refresh(); }); }
  async function deletion(event: FormEvent) { event.preventDefault(); await action(async () => { const data = await orgRequest(`${orgId}/deletion/`, 'POST', { password, confirmation, action: lifecycle?.state === 'pending_delete' ? 'cancel' : 'schedule' }); setLifecycle(data); onLifecycle(data); setPassword(''); setConfirmation(''); setNotice(data.state === 'pending_delete' ? 'Deletion is scheduled. You can cancel before the date shown below.' : 'Deletion cancelled. Your organization will be kept.'); }); }
  return <section className="activity-panel">
    <div className="activity-heading"><div><span className="wn-eyebrow">YOUR DATA, IN YOUR HANDS</span><h2>Data & privacy</h2><p>Take a copy of your workspace or manage its lifecycle.</p></div><button className="wn-secondary" disabled={busy || loading} onClick={() => void action(refresh)}>Refresh</button></div>
    <ErrorNotice message={error} />{notice && <p className="activity-notice" role="status">{notice}</p>}
    {loading ? <p role="status">Loading workspace data…</p> : <>
      <div className="wn-panel lifecycle-card"><h3>Export your organization</h3><p>Download a ZIP containing projects, active tasks, comments, member display names and available attachments. Archived projects are included. Deleted content and files that have not passed safety checks are excluded.</p><p>Only you can download exports you request, while you remain an Owner or Admin. Downloads expire after 24 hours.</p><button className="wn-button" disabled={busy} onClick={() => void createExport()}>Create export</button></div>
      <div className="wn-panel activity-feed" aria-label="Your exports">{jobs.results.map(job => <article className="activity-item" key={job.id}><span className="activity-dot" aria-hidden="true" /><div><h3>Workspace export</h3><p className="lifecycle-state">{job.state}</p><time>{new Date(job.created_at).toLocaleString()}</time>{job.expires_at && <p>Expires {new Date(job.expires_at).toLocaleString()}</p>}</div>{job.download_url ? <a className="wn-secondary" href={job.download_url}>Download ZIP</a> : job.state === 'failed' ? <button className="wn-secondary" disabled={busy} onClick={() => void action(async () => { await orgRequest(`${orgId}/exports/${job.id}/`, 'POST', {}); await refresh(); })}>Retry export</button> : <span>{job.state === 'queued' || job.state === 'running' ? 'Preparing…' : 'Unavailable'}</span>}</article>)}{!jobs.results.length && <p className="activity-empty">Your requested exports will appear here.</p>}</div>
      {jobs.next_cursor && <button className="wn-secondary" disabled={busy} onClick={() => void action(async () => { const data = await orgRequest(`${orgId}/exports/?cursor=${encodeURIComponent(jobs.next_cursor!)}`); setJobs(old => ({ ...data, results: [...old.results, ...data.results] })); })}>Load more exports</button>}
      <div className="wn-panel lifecycle-card lifecycle-danger"><h3>{lifecycle?.state === 'pending_delete' ? 'Deletion is scheduled' : 'Delete this organization'}</h3>{lifecycle?.deletion_due_at && <p role="status"><strong>Cancellation deadline: {new Date(lifecycle.deletion_due_at).toLocaleString()}</strong></p>}<p>Your team can continue working during the seven-day cancellation window. After the deadline, access ends and live data and files are queued for permanent removal within 24 hours. Backups may retain copies for up to 30 days.</p>{lifecycle?.maintenance_state === 'failed' && <p role="alert">A maintenance check needs attention. Contact the service operator before relying on a deletion completion time.</p>}{owner ? <form onSubmit={deletion}><label>Organization address<input value={confirmation} onChange={e => setConfirmation(e.target.value)} placeholder={slug} autoComplete="off" required disabled={busy} /></label><label>Current password<input type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="current-password" required maxLength={128} disabled={busy} /></label><button className="wn-secondary" disabled={busy || confirmation !== slug || !password}>{lifecycle?.state === 'pending_delete' ? 'Cancel scheduled deletion' : 'Schedule deletion in 7 days'}</button></form> : <p>Only the organization Owner can schedule or cancel deletion.</p>}</div>
    </>}
  </section>;
}
