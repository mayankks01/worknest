import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { decode, getSession } from '../accounts/api';
import { orgRequest } from '../organizations/api';
import { ErrorNotice, failureMessage, type Page } from './ui';
import './collaboration.css';

type Comment = { id: string; body: string; author_name: string; created_at: string; updated_at: string; version: number; can_edit: boolean; can_delete: boolean };
type Attachment = { id: string; filename: string; size: number; state: string; reason: string; author_name: string; created_at: string; can_download: boolean; can_edit: boolean; can_delete: boolean; cleanup_pending: boolean };
type Files = Page<Attachment> & { storage?: { used_bytes: number; limit_bytes: number; file_limit_bytes: number } };
const size = (bytes: number) => bytes === 0 ? '0 KB' : bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.ceil(bytes / 1024))} KB`;
const stateLabel: Record<string, string> = { uploading: 'Uploading', queued: 'Waiting for checks', scanning: 'Checking file', available: 'Ready to download', rejected: 'File rejected', failed: 'Processing failed' };
const reasons: Record<string, string> = { MALWARE_DETECTED: 'The malware scanner rejected this file.', INVALID_CONTENT: 'The contents do not match a supported file format.', SERVICE_UNAVAILABLE: 'A checking service is unavailable. WorkNest will retry automatically.', ACCESS_REVOKED: 'The uploader no longer has access.', ACCESS_OR_RETRY_LIMIT: 'Processing stopped because access changed or retries were exhausted.', UPLOAD_EXPIRED: 'The upload did not finish.', UPLOAD_FAILED: 'The upload did not finish.', CLEANUP_UNAVAILABLE: 'Storage cleanup is pending.' };

export default function Collaboration({ orgId, taskId, readonly, onBusy }: { orgId: string; taskId: string; readonly: boolean; onBusy: (busy: boolean) => void }) {
  const root = `${orgId}/tasks/${taskId}/`;
  const [comments, setComments] = useState<Page<Comment>>({ results: [], next_cursor: null });
  const [files, setFiles] = useState<Files>({ results: [], next_cursor: null });
  const [body, setBody] = useState('');
  const [editing, setEditing] = useState<Comment | null>(null);
  const [editBody, setEditBody] = useState('');
  const [rename, setRename] = useState<Attachment | null>(null);
  const [filename, setFilename] = useState('');
  const [removing, setRemoving] = useState<{ kind: 'comment' | 'attachment'; id: string; version?: number } | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [revision, setRevision] = useState(0);
  const commentKey = useRef({ body: '', key: crypto.randomUUID() });
  const uploadKey = useRef(crypto.randomUUID());
  const fileInput = useRef<HTMLInputElement>(null);
  useEffect(() => { onBusy(busy); return () => onBusy(false); }, [busy, onBusy]);
  useEffect(() => {
    let active = true; setLoading(true);
    Promise.all([orgRequest(`${root}comments/`), orgRequest(`${root}attachments/`)]).then(([discussion, attachments]) => { if (active) { setComments(discussion); setFiles(attachments); } }).catch(e => { if (active) setError(failureMessage(e)); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [root, revision]);
  const fetchFiles = useCallback(async (count: number) => {
    let data: Files = await orgRequest(`${root}attachments/`);
    while (data.next_cursor && data.results.length < count) {
      const page: Files = await orgRequest(`${root}attachments/?cursor=${encodeURIComponent(data.next_cursor)}`);
      data = { ...page, results: [...data.results, ...page.results] };
    }
    return data;
  }, [root]);
  const refreshFiles = useCallback(async () => { setFiles(await fetchFiles(Math.max(25, files.results.length))); }, [fetchFiles, files.results.length]);
  useEffect(() => {
    if (!files.results.some(item => ['uploading', 'queued', 'scanning'].includes(item.state))) return;
    let active = true;
    const timer = window.setInterval(() => {
      if (busy || document.visibilityState !== 'visible') return;
      Promise.all(files.results.filter(item => ['uploading', 'queued', 'scanning'].includes(item.state)).map(item => orgRequest(`${orgId}/attachments/${item.id}/`))).then(data => { if (active) setFiles(previous => ({ ...previous, results: previous.results.map(item => data.find(latest => latest.attachment.id === item.id)?.attachment ?? item) })); }).catch(() => { /* Manual refresh exposes persistent failures without interrupting drafts. */ });
    }, 5000);
    return () => { active = false; clearInterval(timer); };
  }, [files.results, orgId, busy]);
  async function action(work: () => Promise<void>) { setBusy(true); setError(''); setNotice(''); try { await work(); } catch (e) { setError(failureMessage(e)); } finally { setBusy(false); } }
  async function post(e: FormEvent) {
    e.preventDefault();
    await action(async () => {
      if (commentKey.current.body !== body) commentKey.current = { body, key: crypto.randomUUID() };
      const data = await orgRequest(`${root}comments/`, 'POST', { body }, commentKey.current.key);
      setComments(old => ({ ...old, results: [...old.results.filter(item => item.id !== data.comment.id), data.comment] })); setBody(''); commentKey.current = { body: '', key: crypto.randomUUID() }; setNotice('Comment posted.');
    });
  }
  async function saveComment(e: FormEvent) {
    e.preventDefault();
    await action(async () => { const data = await orgRequest(`${orgId}/comments/${editing!.id}/`, 'PATCH', { body: editBody, version: editing!.version }); setComments(old => ({ ...old, results: old.results.map(item => item.id === data.comment.id ? data.comment : item) })); setEditing(null); setNotice('Comment updated.'); });
  }
  async function sendFile(e: FormEvent) {
    e.preventDefault(); if (!file) return;
    await action(async () => {
      if (!/\.(pdf|png|jpe?g|txt)$/i.test(file.name)) throw new Error('Choose a PDF, PNG, JPEG or TXT file.');
      if (file.size === 0 || file.size > (files.storage?.file_limit_bytes ?? 10485760)) throw new Error('Choose a nonempty file within the upload limit.');
      const session = await getSession(); const data = new FormData(); data.append('file', file);
      const response = await fetch(`/api/v1/orgs/${root}attachments/`, { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRFToken': session.csrf_token, 'Idempotency-Key': uploadKey.current }, body: data, signal: AbortSignal.timeout(90000) });
      const uploaded = await decode(response); setFile(null); if (fileInput.current) fileInput.current.value = ''; uploadKey.current = crypto.randomUUID(); const updated = await fetchFiles(files.results.length); if (!updated.results.some(item => item.id === uploaded.attachment.id)) updated.results.push(uploaded.attachment); setFiles(updated); setNotice('Upload received. Download becomes available after the file passes its checks.');
    });
  }
  async function download(item: Attachment) {
    await action(async () => {
      const response = await fetch(`/api/v1/orgs/${orgId}/attachments/${item.id}/download/`, { credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(30000) });
      if (!response.ok) { await decode(response); return; }
      const object = URL.createObjectURL(await response.blob()); const link = document.createElement('a'); link.href = object; link.download = item.filename; link.click(); window.setTimeout(() => URL.revokeObjectURL(object), 1000); setNotice('Download ready.');
    });
  }
  async function remove() {
    if (!removing) return;
    await action(async () => { await orgRequest(`${orgId}/${removing.kind === 'comment' ? 'comments' : 'attachments'}/${removing.id}/`, 'DELETE', removing.kind === 'comment' ? { version: removing.version } : {}); if (removing.kind === 'comment') setComments(old => ({ ...old, results: old.results.filter(item => item.id !== removing.id) })); else await refreshFiles(); setRemoving(null); setNotice(removing.kind === 'comment' ? 'Comment removed.' : 'File removed. Storage is released after cleanup completes.'); });
  }
  async function more(kind: 'comments' | 'attachments') {
    await action(async () => {
      const cursor = kind === 'comments' ? comments.next_cursor : files.next_cursor;
      const data = await orgRequest(`${root}${kind}/?cursor=${encodeURIComponent(cursor!)}`);
      if (kind === 'comments') setComments(old => ({ ...data, results: [...old.results.filter(item => !data.results.some((next: Comment) => item.id === next.id)), ...data.results].sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id)) }));
      else setFiles(old => ({ ...data, results: [...old.results.filter(item => !data.results.some((next: Attachment) => item.id === next.id)), ...data.results].sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id)) }));
    });
  }
  return <section className="task-collaboration" aria-label="Task discussion and files">
    <div className="collaboration-heading"><h3>Discussion & files</h3><button className="wn-secondary" type="button" disabled={busy || loading} onClick={() => { setError(''); setRevision(value => value + 1); }}>Refresh discussion</button></div>
    <ErrorNotice message={error} />{notice && <p className="collaboration-notice" role="status">{notice}</p>}
    {loading ? <p role="status">Loading discussion…</p> : <>
      <h4>Comments <small>Oldest first</small></h4>
      <div className="task-comments">{comments.results.map(item => <article className="task-comment" key={item.id}><header><strong>{item.author_name}</strong><time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString()}</time></header>{editing?.id === item.id ? <form onSubmit={saveComment} className="account-form"><label>Edit comment<textarea required maxLength={5000} rows={4} value={editBody} onChange={e => setEditBody(e.target.value)} disabled={busy} /></label><div className="collaboration-actions"><button className="wn-secondary" type="button" disabled={busy} onClick={() => setEditing(null)}>Cancel edit</button><button className="wn-button" disabled={busy}>Save comment</button></div><p className="project-footnote">If this comment changed elsewhere, cancel your edit and refresh before trying again.</p></form> : <p className="comment-body">{item.body}</p>}{item.version > 1 && <small>Edited {new Date(item.updated_at).toLocaleString()}</small>}<div className="collaboration-actions">{item.can_edit && !readonly && !editing && <button className="collaboration-link" disabled={busy} onClick={() => { setEditing(item); setEditBody(item.body); }}>Edit comment</button>}{item.can_delete && !readonly && <button className="collaboration-link danger" disabled={busy} onClick={() => setRemoving({ kind: 'comment', id: item.id, version: item.version })}>Delete comment</button>}</div></article>)}{!comments.results.length && <p className="collaboration-empty">Start the conversation. Share an update or a question with your team.</p>}</div>
      {comments.next_cursor && <button className="wn-secondary" disabled={busy} onClick={() => void more('comments')}>Load more comments</button>}
      {!readonly && <form className="account-form comment-compose" onSubmit={post}><label>Add a comment<textarea rows={3} required maxLength={5000} placeholder="Share a thought with your team…" value={body} onChange={e => setBody(e.target.value)} disabled={busy} /></label><div className="collaboration-actions"><small>{body.length} / 5,000 · Plain text</small><button className="wn-button" disabled={busy || !body.trim()}>Post comment</button></div></form>}
      <div className="collaboration-files-heading"><h4>Attachments</h4>{files.storage && <p>{size(files.storage.used_bytes)} of {size(files.storage.limit_bytes)} used in this organization</p>}</div>
      <div className="task-files">{files.results.map(item => <article className="task-file" key={item.id}><div className="task-file-info"><strong>{item.filename}</strong><small>{size(item.size)} · {item.author_name}</small><span className={`file-state file-${item.state}`}>{stateLabel[item.state] ?? item.state}</span>{item.reason && <p>{reasons[item.reason] ?? 'This file is not ready to download.'}</p>}</div><div className="collaboration-actions">{item.can_download && <button className="wn-secondary" disabled={busy} onClick={() => void download(item)}>Download</button>}{item.can_edit && !readonly && <button className="collaboration-link" disabled={busy} onClick={() => { setRename(item); setFilename(item.filename); }}>Rename</button>}{item.can_delete && !readonly && <button className="collaboration-link danger" disabled={busy} onClick={() => setRemoving({ kind: 'attachment', id: item.id })}>Delete file</button>}</div>{rename?.id === item.id && <form className="account-form" onSubmit={e => { e.preventDefault(); void action(async () => { await orgRequest(`${orgId}/attachments/${item.id}/`, 'PATCH', { filename }); setRename(null); await refreshFiles(); }); }}><label>Filename<input required maxLength={140} value={filename} onChange={e => setFilename(e.target.value)} disabled={busy} /></label><div className="collaboration-actions"><button type="button" className="wn-secondary" disabled={busy} onClick={() => setRename(null)}>Cancel rename</button><button className="wn-button" disabled={busy}>Save filename</button></div></form>}</article>)}{!files.results.length && <p className="collaboration-empty">Keep the files your team needs alongside the task.</p>}</div>
      {files.next_cursor && <button className="wn-secondary" disabled={busy} onClick={() => void more('attachments')}>Load more files</button>}
      {!readonly && <form className="account-form file-upload" onSubmit={sendFile}><label>Attach a file<input ref={fileInput} type="file" accept=".pdf,.png,.jpg,.jpeg,.txt" disabled={busy} onChange={e => { setFile(e.target.files?.[0] ?? null); uploadKey.current = crypto.randomUUID(); }} /></label><p>PDF, PNG, JPEG or UTF-8 TXT · Up to {size(files.storage?.file_limit_bytes ?? 10485760)}. Files stay private and unavailable until content and malware checks pass. Interactive or encrypted PDFs are not supported.</p><button className="wn-button" disabled={busy || !file}>{busy ? 'Please wait…' : 'Upload attachment'}</button></form>}
    </>}
    {removing && <div className="collaboration-confirm" role="alert"><h4>Delete this {removing.kind === 'comment' ? 'comment' : 'file'}?</h4><p>It will be removed from this task. This action cannot be undone here.</p><div className="collaboration-actions"><button className="wn-secondary" disabled={busy} onClick={() => setRemoving(null)}>Keep it</button><button className="project-danger" disabled={busy} onClick={() => void remove()}>Confirm deletion</button></div></div>}
  </section>;
}
