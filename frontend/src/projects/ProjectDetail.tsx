import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { orgRequest } from '../organizations/api';
import { Icon } from '../components/Icon';
import { canManage, ErrorNotice, failureMessage, Modal, priorities, Progress, statusClass, statuses, type Member, type Page, type Project, type Task } from './ui';
import TaskEditor from './TaskEditor';
import ProjectMembers from './ProjectMembers';
import './projects.css';

export default function ProjectDetail({ orgId, projectId }: { orgId: string; projectId: string }) {
  const [project, setProject] = useState<Project | null>(null);
  const [role, setRole] = useState('Viewer');
  const [members, setMembers] = useState<Member[]>([]);
  const [tab, setTab] = useState('Board');
  const [page, setPage] = useState<Page<Task>>({ results: [], next_cursor: null });
  const [filters, setFilters] = useState({ status: '', priority: '', assignee: '', overdue: 'false' });
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [editor, setEditor] = useState<{ taskId?: string; initialStatus?: string } | null>(() => { const taskId = new URLSearchParams(location.search).get('task'); return taskId && /^[0-9a-f-]{36}$/.test(taskId) ? { taskId } : null; });
  const dragging = useRef<string | null>(null);
  const moving = useRef(false);
  const root = `${orgId}/projects/${projectId}/`;
  const query = new URLSearchParams(filters).toString();
  const refresh = useCallback(async () => {
    const [detail, roster] = await Promise.all([orgRequest(root), orgRequest(`${root}members/?limit=100`)]);
    setProject(detail.project); setRole(detail.role); setMembers(roster.results);
  }, [root]);
  useEffect(() => {
    let active = true; setLoading(true); setError(''); setPage({ results: [], next_cursor: null });
    Promise.all([orgRequest(root), orgRequest(`${root}members/?limit=100`), orgRequest(`${root}tasks/?${query}`)]).then(([detail, roster, tasks]) => {
      if (active) { setProject(detail.project); setRole(detail.role); setMembers(roster.results); setPage(tasks); }
    }).catch(e => { if (active) { setProject(null); setError(failureMessage(e)); } }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [root, query, revision]);
  useEffect(() => { const taskId = new URLSearchParams(location.search).get('task'); if (taskId) setEditor({ taskId }); }, []);
  const readonly = role === 'Viewer' || !!project?.archived_at;
  async function more() {
    setBusy(true); setError('');
    try { const result: Page<Task> = await orgRequest(`${root}tasks/?${query}&cursor=${encodeURIComponent(page.next_cursor!)}`); setPage(old => ({ results: [...old.results, ...result.results], next_cursor: result.next_cursor })); }
    catch (e) { setError(failureMessage(e)); } finally { setBusy(false); }
  }
  async function move(task: Task, status: string) {
    if (readonly || moving.current || busy || task.status === status) return;
    moving.current = true; setBusy(true); setError('');
    setPage(old => ({ ...old, results: old.results.map(item => item.id === task.id ? { ...item, status } : item) }));
    try {
      await orgRequest(`${orgId}/tasks/${task.id}/`, 'PATCH', { version: task.version, status });
      setRevision(value => value + 1);
    } catch (e) {
      setPage(old => ({ ...old, results: old.results.map(item => item.id === task.id ? task : item) }));
      setError(`${failureMessage(e)} The card has returned to its previous position. Refresh to see the latest task.`);
    } finally { moving.current = false; setBusy(false); }
  }
  function closeEditor() { setEditor(null); if (location.search) history.replaceState(null, '', location.pathname); }
  function saved() { closeEditor(); setRevision(value => value + 1); }
  return <><a className="wn-back" href={`/orgs/${orgId}/projects`}>← All projects</a><ErrorNotice message={error} />{loading ? <p role="status">Loading project…</p> : !project ? <button className="wn-secondary" onClick={() => setRevision(value => value + 1)}>Try again</button> : <>
    <div className="project-detail-heading"><span className="project-letter">{project.name[0].toUpperCase()}</span><div><h2>{project.name}</h2><p>{members.length} project members · {project.task_count} tasks</p></div><Progress project={project} /></div>
    {project.description && <p className="project-description">{project.description}</p>}
    {project.archived_at && <div className="project-archive-banner"><Icon name="info" /><span>This project is archived. Its tasks and members are read-only.</span>{canManage(role) && <button className="wn-secondary" onClick={() => setTab('Settings')}>Restore project</button>}</div>}
    <div className="project-tabs" role="tablist" aria-label="Project views">{['Board', 'List', 'Members', ...(canManage(role) ? ['Settings'] : [])].map(value => <button key={value} role="tab" id={`project-tab-${value}`} aria-controls="project-tab-panel" tabIndex={tab === value ? 0 : -1} aria-selected={tab === value} onKeyDown={event => { const choices = ['Board', 'List', 'Members', ...(canManage(role) ? ['Settings'] : [])]; const index = choices.indexOf(value); const next = event.key === 'ArrowRight' ? choices[(index + 1) % choices.length] : event.key === 'ArrowLeft' ? choices[(index + choices.length - 1) % choices.length] : event.key === 'Home' ? choices[0] : event.key === 'End' ? choices[choices.length - 1] : null; if (next) { event.preventDefault(); setTab(next); document.getElementById(`project-tab-${next}`)?.focus(); } }} onClick={() => setTab(value)} disabled={busy}>{value === 'Board' && <Icon name="board" />}{value === 'List' && <Icon name="list" />}{value}</button>)}</div>
    <div role="tabpanel" id="project-tab-panel" aria-labelledby={`project-tab-${tab}`} tabIndex={0}>
    {tab === 'Members' ? <ProjectMembers orgId={orgId} project={project} role={role} members={members} refresh={refresh} /> : tab === 'Settings' && canManage(role) ? <ProjectSettings key={`${project.id}:${project.archived_at}`} root={root} project={project} refresh={() => setRevision(value => value + 1)} /> : <>
      <div className="project-filters"><label>Status<select value={filters.status} disabled={busy} onChange={e => setFilters({ ...filters, status: e.target.value })}><option value="">All statuses</option>{statuses.map(value => <option key={value}>{value}</option>)}</select></label><label>Assignee<select value={filters.assignee} disabled={busy} onChange={e => setFilters({ ...filters, assignee: e.target.value })}><option value="">All assignees</option><option value="me">Assigned to me</option><option value="unassigned">Unassigned</option>{members.map(member => <option key={member.id} value={member.id}>{member.display_name}</option>)}</select></label><label>Priority<select value={filters.priority} disabled={busy} onChange={e => setFilters({ ...filters, priority: e.target.value })}><option value="">All priorities</option>{priorities.map(value => <option key={value}>{value}</option>)}</select></label><label className="project-checkbox"><input type="checkbox" disabled={busy} checked={filters.overdue === 'true'} onChange={e => setFilters({ ...filters, overdue: String(e.target.checked) })} />Overdue only</label><div className="project-toolbar-actions"><button className="wn-secondary" disabled={busy} onClick={() => setRevision(value => value + 1)}>Refresh</button>{!readonly && <button className="wn-button" disabled={busy} onClick={() => setEditor({})}><Icon name="plus" />Add task</button>}</div></div>
      <p className="project-footnote">{page.results.length} loaded tasks{page.next_cursor ? ' · More tasks available below' : ''}. {readonly ? 'Read-only access.' : 'Drag cards between columns, or use the status selector on each task.'}</p>
      {tab === 'Board' ? <div className="project-board" aria-label="Task board">{statuses.map(status => <section className={`project-column ${statusClass(status)}`} key={status} aria-label={`${status} column`} onDragOver={e => { if (!readonly && !busy) e.preventDefault(); }} onDrop={e => { e.preventDefault(); const task = page.results.find(item => item.id === dragging.current); dragging.current = null; if (task) void move(task, status); }}><header><h3>{status}</h3><span>{page.results.filter(task => task.status === status).length}</span></header><div className="project-column-cards">{page.results.filter(task => task.status === status).map(task => <article className="project-task-card" draggable={!readonly && !busy} onDragStart={e => { dragging.current = task.id; e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', task.id); }} onDragEnd={() => { dragging.current = null; }} key={task.id}><button className="project-task-title" disabled={busy} onClick={() => setEditor({ taskId: task.id })}>{task.title}</button><div className="project-card-meta"><span className={`task-priority priority-${task.priority.toLowerCase()}`}>{task.priority}</span><span className={task.overdue ? 'task-overdue' : ''}>{task.due_date ?? 'No due date'}</span></div><div className="project-card-footer"><small>{task.assignee_name ?? 'Unassigned'}</small>{!readonly && <select aria-label={`Status for ${task.title}`} value={task.status} disabled={busy} onChange={e => void move(task, e.target.value)}>{statuses.map(value => <option key={value}>{value}</option>)}</select>}</div></article>)}</div>{!readonly && <button className="project-column-add" disabled={busy} onClick={() => setEditor({ initialStatus: status })}><Icon name="plus" />Add task</button>}</section>)}</div> : <section className="wn-panel project-table-wrap" tabIndex={0} role="region" aria-label="Task list"><table className="project-table"><thead><tr><th>Task</th><th>Status</th><th>Assignee</th><th>Priority</th><th>Due date</th></tr></thead><tbody>{page.results.map(task => <tr key={task.id}><td><button className="project-task-title" disabled={busy} onClick={() => setEditor({ taskId: task.id })}>{task.title}</button></td><td>{readonly ? <span className={`task-status ${statusClass(task.status)}`}>{task.status}</span> : <select aria-label={`Status for ${task.title}`} value={task.status} disabled={busy} onChange={e => void move(task, e.target.value)}>{statuses.map(value => <option key={value}>{value}</option>)}</select>}</td><td>{task.assignee_name ?? 'Unassigned'}</td><td>{task.priority}</td><td className={task.overdue ? 'task-overdue' : ''}>{task.due_date ?? '—'}</td></tr>)}</tbody></table>{!page.results.length && <p className="project-empty">No tasks match these filters.</p>}</section>}
      {page.next_cursor && <button className="wn-secondary project-load-more" disabled={busy} onClick={() => void more()}>{busy ? 'Loading…' : 'Load more tasks'}</button>}
    </>}
    </div>
    {editor && <TaskEditor orgId={orgId} project={project} role={role} members={members} {...editor} close={closeEditor} saved={saved} />}
  </>}</>;
}

function ProjectSettings({ root, project, refresh }: { root: string; project: Project; refresh: () => void }) {
  const [name, setName] = useState(project.name);
  const [description, setDescription] = useState(project.description);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [confirm, setConfirm] = useState(false);
  async function save(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError('');
    try { await orgRequest(root, 'PATCH', { name, description }); refresh(); }
    catch (e) { setError(failureMessage(e)); } finally { setBusy(false); }
  }
  async function archive() {
    setBusy(true); setError('');
    try { await orgRequest(`${root}${project.archived_at ? 'restore' : 'archive'}/`, 'POST', {}); setConfirm(false); refresh(); }
    catch (e) { setError(failureMessage(e)); } finally { setBusy(false); }
  }
  return <section className="wn-panel project-settings"><h2>Project settings</h2>{!confirm && <ErrorNotice message={error} />}<form className="account-form" onSubmit={save}><fieldset className="project-fields" disabled={busy || !!project.archived_at}><label>Project name<input required maxLength={120} value={name} onChange={e => setName(e.target.value)} /></label><label>Description<textarea rows={4} maxLength={5000} value={description} onChange={e => setDescription(e.target.value)} /></label>{!project.archived_at && <button className="wn-button">Save project</button>}</fieldset></form><div className="project-archive-setting"><h3>{project.archived_at ? 'Restore project' : 'Archive project'}</h3><p>{project.archived_at ? 'Restore this project to make it editable again. This uses one active project place.' : 'Keep the project and its history, while making tasks and membership read-only. Archiving frees an active project place.'}</p><button className="wn-secondary" disabled={busy} onClick={() => { setError(''); setConfirm(true); }}>{project.archived_at ? 'Restore project' : 'Archive project'}</button></div>{confirm && <Modal title={project.archived_at ? 'Restore this project?' : 'Archive this project?'} busy={busy} close={() => setConfirm(false)}><ErrorNotice message={error} /><p>{project.archived_at ? 'Your team will be able to update tasks and project members again.' : 'Your team can still read this project, but cannot change its tasks or members until it is restored.'}</p><div className="project-form-actions"><button className="wn-secondary" disabled={busy} onClick={() => setConfirm(false)}>Cancel</button><button className="wn-button" disabled={busy} onClick={() => void archive()}>{busy ? 'Saving…' : project.archived_at ? 'Confirm restore' : 'Confirm archive'}</button></div></Modal>}</section>;
}
