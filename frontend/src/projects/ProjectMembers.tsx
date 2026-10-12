import { useEffect, useState } from 'react';
import { orgRequest } from '../organizations/api';
import { canManage, ErrorNotice, failureMessage, Modal, type Member, type Project } from './ui';

export default function ProjectMembers({ orgId, project, role, members, refresh }: { orgId: string; project: Project; role: string; members: Member[]; refresh: () => Promise<void> }) {
  const [roster, setRoster] = useState<Member[]>([]);
  const [selected, setSelected] = useState('');
  const [removing, setRemoving] = useState<Member | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const editable = canManage(role) && !project.archived_at;
  useEffect(() => { let active = true; if (editable) orgRequest(`${orgId}/members/?limit=100`).then(data => { if (active) setRoster(data.results); }).catch(e => { if (active) setError(failureMessage(e)); }); return () => { active = false; }; }, [orgId, editable]);
  async function change(remove?: Member) {
    setBusy(true); setError('');
    try { await orgRequest(`${orgId}/projects/${project.id}/members/${remove ? `${remove.id}/` : ''}`, remove ? 'DELETE' : 'POST', remove ? {} : { membership_id: selected }); await refresh(); setSelected(''); setRemoving(null); }
    catch (e) { setError(failureMessage(e)); } finally { setBusy(false); }
  }
  return <section className="wn-panel project-members"><div className="wn-section-heading"><h2>Project members</h2><span>{members.length} members</span></div><p className="project-subtitle">Members and Viewers need project membership to see this project. Owners and Admins can see all projects, but must join to receive task assignments.</p>{!removing && <ErrorNotice message={error} />}
    {editable && <form className="project-add-member" onSubmit={e => { e.preventDefault(); void change(); }}><label>Add an organization member<select required value={selected} onChange={e => setSelected(e.target.value)}><option value="">Choose a member</option>{roster.filter(member => !members.some(current => current.id === member.id)).map(member => <option value={member.id} key={member.id}>{member.display_name} · {member.role}</option>)}</select></label><button className="wn-button" disabled={busy || !selected}>Add member</button></form>}
    {members.map(member => <div className="project-member" key={member.id}><span className="profile-avatar">{member.display_name.slice(0, 2).toUpperCase()}</span><div><strong>{member.display_name}</strong><small>{member.role}</small></div>{editable && <button className="wn-secondary" disabled={busy} onClick={() => { setError(''); setRemoving(member); }} aria-label={`Remove ${member.display_name}`}>Remove</button>}</div>)}{!members.length && <p className="project-empty">No explicit project members yet.</p>}
    {removing && <Modal title="Remove project member?" busy={busy} close={() => setRemoving(null)}><ErrorNotice message={error} /><p>{removing.display_name} will leave this project. Their open tasks will become unassigned. Completed tasks retain their history.</p>{canManage(removing.role) && <p>As an {removing.role}, they will still have organization-wide project access.</p>}<div className="project-form-actions"><button className="wn-secondary" disabled={busy} onClick={() => setRemoving(null)}>Cancel</button><button className="project-danger" disabled={busy} onClick={() => void change(removing)}>Remove member</button></div></Modal>}
  </section>;
}
