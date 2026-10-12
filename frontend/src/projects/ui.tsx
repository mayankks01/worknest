import { useEffect, useRef, type ReactNode } from 'react';
import { ApiError } from '../accounts/api';

export type Project = { id: string; name: string; description: string; archived_at: string | null; task_count: number; completed_count: number };
export type Task = { id: string; project_id: string; title: string; description?: string; status: string; priority: string; version: number; assignee_id: string | null; assignee_name: string | null; due_date: string | null; overdue: boolean; completed_at: string | null };
export type Member = { id: string; display_name: string; email: string; role: string };
export type Page<T> = { results: T[]; next_cursor: string | null };
export const statuses = ['To Do', 'In Progress', 'Blocked', 'Done'];
export const priorities = ['Low', 'Medium', 'High', 'Urgent'];
export const statusClass = (status: string) => status.toLowerCase().replaceAll(' ', '-');
export function failureMessage(e: unknown) {
  if (e instanceof ApiError && e.currentVersion) return `${e.message} Latest version: ${e.currentVersion}. Your changes have not been saved.`;
  if (e instanceof ApiError) {
    const details = Object.entries(e.details ?? {}).filter(([, value]) => Array.isArray(value)).map(([field, value]) => `${field.replaceAll('_', ' ')}: ${value.join(' ')}`).join(' ');
    return details ? `${e.message} ${details}` : e.message;
  }
  return e instanceof Error ? e.message : 'Please try again.';
}
export const canManage = (role: string) => ['Owner', 'Admin'].includes(role);
export const projectLink = (orgId: string, projectId: string) => `/orgs/${orgId}/projects/${projectId}`;
export function ErrorNotice({ message }: { message: string }) { return message ? <div role="alert" className="account-message error">{message}</div> : null; }
export function Progress({ project }: { project: Project }) {
  const percent = project.task_count ? Math.round(project.completed_count / project.task_count * 100) : 0;
  return <div className="project-progress"><span role="progressbar" aria-label={`${project.name} completion`} aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}><i style={{ width: `${percent}%` }} /></span><small>{project.completed_count} / {project.task_count} tasks</small></div>;
}
export function Modal({ title, busy, close, children }: { title: string; busy: boolean; close: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { const dialog = ref.current; dialog?.showModal(); return () => dialog?.close(); }, []);
  return <dialog className="project-modal" ref={ref} onCancel={e => { e.preventDefault(); if (!busy) close(); }} aria-label={title}><div className="project-modal-heading"><h2>{title}</h2><button type="button" className="project-close" aria-label="Close dialog" onClick={close} disabled={busy}>×</button></div>{children}</dialog>;
}
