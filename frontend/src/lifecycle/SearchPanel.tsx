import { useState, type FormEvent } from 'react';
import { orgRequest } from '../organizations/api';
import { ErrorNotice, failureMessage, type Page } from '../projects/ui';
import '../activity/activity.css';
import './lifecycle.css';

type Result = { id: string; project_id: string; title: string; status: string; priority: string; due_date: string | null };
export default function SearchPanel({ orgId }: { orgId: string }) {
  const [query, setQuery] = useState(''), [state, setState] = useState('active');
  const [results, setResults] = useState<(Page<Result> & { count: number }) | null>(null);
  const [searched, setSearched] = useState(''), [route, setRoute] = useState('');
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function search(event: FormEvent) { event.preventDefault(); setBusy(true); setError(''); setResults(null); const path = `${orgId}/search/?q=${encodeURIComponent(query.trim())}&state=${state}`; try { setResults(await orgRequest(path)); setRoute(path); setSearched(query.trim()); } catch (e) { setError(failureMessage(e)); } finally { setBusy(false); } }
  async function more() { setBusy(true); setError(''); try { const data = await orgRequest(`${route}&cursor=${encodeURIComponent(results!.next_cursor!)}`); setResults(old => ({ ...data, results: [...(old?.results ?? []), ...data.results] })); } catch (e) { setError(failureMessage(e)); } finally { setBusy(false); } }
  return <section className="activity-panel"><div className="activity-heading"><div><span className="wn-eyebrow">FIND YOUR NEXT MOVE</span><h2>Search tasks</h2><p>Search titles and descriptions across projects you can access.</p></div></div><form className="wn-panel lifecycle-search" onSubmit={search}><label>Search terms<input value={query} onChange={e => setQuery(e.target.value)} minLength={2} maxLength={200} required placeholder="Find a task or description…" disabled={busy} /></label><label>Projects<select value={state} onChange={e => setState(e.target.value)} disabled={busy}><option value="active">Active projects</option><option value="archived">Archived projects</option><option value="all">All accessible projects</option></select></label><button className="wn-button" disabled={busy || query.trim().length < 2}>{busy ? 'Searching…' : 'Search'}</button></form><ErrorNotice message={error} />{results && <><p role="status">{results.count} {results.count === 1 ? 'result' : 'results'} for “{searched}”</p><div className="wn-panel activity-feed">{results.results.map(task => <article className="activity-item" key={task.id}><span className="activity-dot" aria-hidden="true" /><div><h3><a href={`/orgs/${orgId}/projects/${task.project_id}?task=${task.id}`}>{task.title}</a></h3><p>{task.status} · {task.priority} priority · {task.due_date ?? 'No due date'}</p></div></article>)}{!results.results.length && <div className="activity-empty"><h3>No matching tasks.</h3><p>Try another phrase or include archived projects.</p></div>}</div>{results.next_cursor && <button className="wn-secondary" disabled={busy} onClick={() => void more()}>Load more results</button>}</>}</section>;
}
