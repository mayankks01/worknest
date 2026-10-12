import { useEffect, useRef, useState, type FormEvent } from 'react';
import { orgRequest } from '../organizations/api';
import { Icon } from '../components/Icon';
import { canManage, ErrorNotice, failureMessage, Modal, Progress, projectLink, type Page, type Project, type Task } from './ui';
import './projects.css';

export function Dashboard({ orgId }: { orgId: string }) {
  const [data, setData] = useState<{ counts: Record<string, number>; projects: Project[]; tasks: Task[]; timezone: string } | null>(null);
  const [error, setError] = useState('');
  useEffect(() => { let active = true; orgRequest(`${orgId}/dashboard/`).then(result => { if (active) setData(result); }).catch(e => { if (active) setError(failureMessage(e)); }); return () => { active = false; }; }, [orgId]);
  return <div className="project-dashboard"><ErrorNotice message={error} />{!data ? !error && <p role="status">Loading your workspace…</p> : <><div className="project-metrics">{[['projects', 'Active projects', 'folder'], ['assigned', 'Tasks assigned to you', 'tasks'], ['blocked', 'Blocked tasks', 'info'], ['overdue', 'Overdue tasks', 'clock']].map(([key, label, icon]) => <div className={`project-metric metric-${key}`} key={key}><span><Icon name={icon as 'folder'} /></span><div><strong>{data.counts[key]}</strong><p>{label}</p></div></div>)}</div><div className="project-dashboard-grid"><section className="wn-panel"><div className="wn-section-heading"><h2>Recent projects</h2><a href={`/orgs/${orgId}/projects`}>View all →</a></div>{data.projects.length ? data.projects.map(project => <a className="project-summary" key={project.id} href={projectLink(orgId, project.id)}><span className="project-letter">{project.name[0].toUpperCase()}</span><div><strong>{project.name}</strong><Progress project={project} /></div><Icon name="chevron" /></a>) : <div className="project-empty"><Icon name="folder" /><h3>A shared place to start.</h3><p>Create a project, invite its members, and turn your next idea into a plan.</p><a className="wn-button" href={`/orgs/${orgId}/projects`}>Open projects</a></div>}</section><section className="wn-panel"><div className="wn-section-heading"><h2>My tasks</h2><span>Next up</span></div>{data.tasks.length ? data.tasks.map(task => <a className="dashboard-task" href={`${projectLink(orgId, task.project_id)}?task=${task.id}`} key={task.id}><strong>{task.title}</strong><div><span className={`task-status ${task.status.toLowerCase().replaceAll(' ', '-')}`}>{task.status}</span><small className={task.overdue ? 'task-overdue' : ''}>{task.due_date ?? 'No due date'}</small></div></a>) : <div className="project-empty"><Icon name="check" /><h3>A clear view of your work.</h3><p>Your assigned open tasks will appear here.</p></div>}</section></div><p className="project-footnote">Showing work from your accessible, active projects. Due dates follow {data.timezone}.</p></>}</div>;
}

export default function ProjectHome({ orgId }: { orgId: string }) {
  const [page, setPage] = useState<Page<Project>>({ results: [], next_cursor: null });
  const [role, setRole] = useState('Viewer');
  const [usage, setUsage] = useState<{ active_projects: number; project_limit: number } | null>(null);
  const [state, setState] = useState('active');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [create, setCreate] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const key = useRef({ payload: '', value: crypto.randomUUID() });
  useEffect(() => {
    let active = true; setLoading(true); setError(''); setPage({ results: [], next_cursor: null });
    orgRequest(`${orgId}/projects/?state=${state}`).then(data => { if (active) { setPage(data); setRole(data.role); setUsage(data.usage ?? null); } }).catch(e => { if (active) setError(failureMessage(e)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [orgId, state]);
  async function more() {
    setBusy(true); setError('');
    try { const data: Page<Project> = await orgRequest(`${orgId}/projects/?state=${state}&cursor=${encodeURIComponent(page.next_cursor!)}`); setPage(old => ({ results: [...old.results, ...data.results], next_cursor: data.next_cursor })); }
    catch (e) { setError(failureMessage(e)); } finally { setBusy(false); }
  }
  async function submit(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError('');
    const payload = JSON.stringify({ name, description });
    if (key.current.payload !== payload) key.current = { payload, value: crypto.randomUUID() };
    try { const data = await orgRequest(`${orgId}/projects/`, 'POST', { name, description }, key.current.value); location.assign(projectLink(orgId, data.project.id)); }
    catch (e) { setError(failureMessage(e)); setBusy(false); }
  }
  return <><div className="project-toolbar"><div><h2>Projects</h2><p>{usage ? `${usage.active_projects} of ${usage.project_limit} active project places used` : 'A clear plan for every team.'}</p></div><div className="project-toolbar-actions"><label className="sr-only" htmlFor="project-state">Project state</label><select id="project-state" value={state} onChange={e => setState(e.target.value)}><option value="active">Active projects</option><option value="archived">Archived projects</option><option value="all">All projects</option></select>{canManage(role) && <button className="wn-button" onClick={() => { setError(''); setCreate(true); }}><Icon name="plus" />New project</button>}</div></div>{!create && <ErrorNotice message={error} />}<section className="wn-panel">{loading ? <p className="project-empty" role="status">Loading projects…</p> : page.results.length ? <div className="project-table-wrap" role="region" aria-label="Projects table" tabIndex={0}><table className="project-table"><thead><tr><th>Name</th><th>Progress</th><th>Tasks</th><th>Status</th><th><span className="sr-only">Open</span></th></tr></thead><tbody>{page.results.map(project => <tr key={project.id}><td><a className="project-name" href={projectLink(orgId, project.id)}><span className="project-letter">{project.name[0].toUpperCase()}</span><span><strong>{project.name}</strong><small>{project.description || 'Private project'}</small></span></a></td><td><Progress project={project} /></td><td>{project.task_count}</td><td><span className={`task-status ${project.archived_at ? 'archived' : 'done'}`}>{project.archived_at ? 'Archived' : 'Active'}</span></td><td><a href={projectLink(orgId, project.id)} aria-label={`Open ${project.name}`}><Icon name="chevron" /></a></td></tr>)}</tbody></table></div> : <div className="project-empty"><Icon name="folder" /><h3>{state === 'archived' ? 'No archived projects.' : 'Room for your next big thing.'}</h3><p>{canManage(role) ? 'Create a private project to start planning with your team.' : 'Your Owner or Admin can add you to a project.'}</p></div>}{page.next_cursor && <button className="wn-secondary wn-more" disabled={busy} onClick={() => void more()}>Load more projects</button>}</section>{create && <Modal title="Create a project" busy={busy} close={() => setCreate(false)}><p className="project-subtitle">Give your team a shared direction. Projects are private.</p><ErrorNotice message={error} /><form className="account-form" onSubmit={submit}><label>Project name<input autoFocus required maxLength={120} value={name} placeholder="e.g. Website redesign" onChange={e => setName(e.target.value)} /></label><label>Description<textarea maxLength={5000} rows={4} value={description} onChange={e => setDescription(e.target.value)} placeholder="What are we working toward?" /></label><div className="project-form-actions"><button className="wn-secondary" type="button" disabled={busy} onClick={() => setCreate(false)}>Cancel</button><button className="wn-button" disabled={busy}>{busy ? 'Creating…' : 'Create project'}</button></div></form></Modal>}</>;
}
