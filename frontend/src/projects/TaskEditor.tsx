import { useEffect, useRef, useState, type FormEvent } from 'react';
import Collaboration from './Collaboration';
import { ApiError } from '../accounts/api';
import { orgRequest } from '../organizations/api';
import { canManage, ErrorNotice, failureMessage, Modal, priorities, statuses, type Member, type Project, type Task } from './ui';

export default function TaskEditor({ orgId, project, taskId, initialStatus, role, members, close, saved }: { orgId: string; project: Project; taskId?: string; initialStatus?: string; role: string; members: Member[]; close: () => void; saved: () => void }) {
  const [original, setOriginal] = useState<Task | null>(null);
  const [fields, setFields] = useState({ title: '', description: '', status: initialStatus ?? 'To Do', priority: 'Medium', due_date: '', assignee_id: '' });
  const [loading, setLoading] = useState(!!taskId);
  const [busy, setBusy] = useState(false);
  const [collabBusy, setCollabBusy] = useState(false);
  const [error, setError] = useState('');
  const [conflict, setConflict] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [revision, setRevision] = useState(0);
  const key = useRef({ payload: '', value: crypto.randomUUID() });
  const readonly = role === 'Viewer' || !!project.archived_at;
  const eligible = members.filter(member => member.role !== 'Viewer');
  useEffect(() => {
    if (!taskId) return;
    let active = true; setLoading(true); setError('');
    orgRequest(`${orgId}/tasks/${taskId}/`).then(({ task }: { task: Task }) => {
      if (!active) return;
      if (task.project_id !== project.id) throw new Error('This task is not in this project.');
      setOriginal(task); setFields({ title: task.title, description: task.description ?? '', status: task.status, priority: task.priority, due_date: task.due_date ?? '', assignee_id: task.assignee_id ?? '' }); setConflict(false); setDeleting(false);
    }).catch(e => { if (active) setError(failureMessage(e)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [orgId, project.id, taskId, revision]);
  function failed(e: unknown) { setError(failureMessage(e)); if (e instanceof ApiError && e.currentVersion) setConflict(true); }
  async function submit(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError('');
    const values: Record<string, unknown> = { ...fields, assignee_id: fields.assignee_id || null, due_date: fields.due_date || null };
    try {
      if (original) {
        const changed = Object.fromEntries(Object.entries(values).filter(([field, value]) => value !== original[field as keyof Task]));
        if (!Object.keys(changed).length) { close(); return; }
        await orgRequest(`${orgId}/tasks/${original.id}/`, 'PATCH', { ...changed, version: original.version });
      } else {
        const payload = JSON.stringify(values);
        if (key.current.payload !== payload) key.current = { payload, value: crypto.randomUUID() };
        await orgRequest(`${orgId}/projects/${project.id}/tasks/`, 'POST', values, key.current.value);
      }
      saved();
    } catch (e) { failed(e); } finally { setBusy(false); }
  }
  async function remove() {
    setBusy(true); setError('');
    try { await orgRequest(`${orgId}/tasks/${original!.id}/`, 'DELETE', { version: original!.version }); saved(); }
    catch (e) { failed(e); } finally { setBusy(false); }
  }
  return <Modal title={taskId ? readonly ? 'Task details' : 'Edit task' : 'Create task'} busy={busy || collabBusy} close={close}>
    <ErrorNotice message={error} />
    {conflict && <div className="project-conflict"><p>Your draft is still here. Reload the latest task before trying again; reloading discards this draft.</p><button className="wn-secondary" disabled={busy} onClick={() => setRevision(value => value + 1)}>Discard draft and reload task</button></div>}
    {loading ? <p role="status">Loading task…</p> : taskId && !original ? <button className="wn-secondary" onClick={() => setRevision(value => value + 1)}>Try again</button> : <form className="account-form" onSubmit={submit}>
      {readonly && <p className="project-subtitle">{project.archived_at ? 'This project is archived. Tasks are read-only.' : 'You have read-only access to this project.'}</p>}
      <fieldset disabled={readonly || busy || collabBusy || deleting} className="project-fields">
        <label>Task title<input autoFocus required maxLength={200} value={fields.title} onChange={e => setFields({ ...fields, title: e.target.value })} /></label>
        <label>Description<textarea rows={5} maxLength={20000} value={fields.description} onChange={e => setFields({ ...fields, description: e.target.value })} /></label>
        <div className="project-field-grid"><label>Status<select value={fields.status} onChange={e => setFields({ ...fields, status: e.target.value })}>{statuses.map(value => <option key={value}>{value}</option>)}</select></label><label>Priority<select value={fields.priority} onChange={e => setFields({ ...fields, priority: e.target.value })}>{priorities.map(value => <option key={value}>{value}</option>)}</select></label>
        <label>Assignee<select value={fields.assignee_id} onChange={e => setFields({ ...fields, assignee_id: e.target.value })}><option value="">Unassigned</option>{eligible.map(member => <option key={member.id} value={member.id}>{member.display_name}</option>)}{fields.assignee_id && !eligible.some(member => member.id === fields.assignee_id) && <option value={fields.assignee_id}>{original?.assignee_name ?? 'Former member'} (historical)</option>}</select></label><label>Due date<input type="date" value={fields.due_date} onChange={e => setFields({ ...fields, due_date: e.target.value })} /></label></div>
      </fieldset>
      {original?.completed_at && <p className="project-footnote">Completed {new Date(original.completed_at).toLocaleString()}</p>}
      {deleting ? <div className="project-confirm"><h3>Delete this task?</h3><p>It will disappear from the project. Deleted task data is retained for 30 days.</p><div className="project-form-actions"><button type="button" className="wn-secondary" disabled={busy} onClick={() => setDeleting(false)}>Keep task</button><button type="button" className="project-danger" disabled={busy || collabBusy || conflict} onClick={() => void remove()}>{busy ? 'Deleting…' : 'Confirm delete'}</button></div></div> : <div className="project-form-actions">{original && canManage(role) && !readonly && <button className="project-danger-link" type="button" disabled={busy || collabBusy || conflict} onClick={() => setDeleting(true)}>Delete task</button>}<button type="button" className="wn-secondary" disabled={busy || collabBusy} onClick={close}>{readonly ? 'Close' : 'Cancel'}</button>{!readonly && <button className="wn-button" disabled={busy || collabBusy || conflict}>{busy ? 'Saving…' : taskId ? 'Save task' : 'Create task'}</button>}</div>}
    </form>}
    {original && !loading && !deleting && <Collaboration orgId={orgId} taskId={original.id} readonly={readonly || busy} onBusy={setCollabBusy} />}
  </Modal>;
}
