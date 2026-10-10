// Presentation fixtures only. Never use these as authenticated application data.
export const statuses = ['To Do', 'In Progress', 'Blocked', 'Done'] as const;
export type Status = typeof statuses[number];
export type Project = { id: string; organizationId: string; name: string; category: string; color: string; letter: string; due: string; health: string };
export type Task = { id: string; projectId: string; title: string; status: Status; label: string; due: string; assignee: string; overdue: boolean };
export const organizations = [
  {id: 'technova', name: 'TechNova', role: 'Owner', members: 5, color: 'purple'},
  {id: 'bright', name: 'Bright Labs', role: 'Member', members: 8, color: 'blue'},
  {id: 'pixel', name: 'PixelForge', role: 'Member', members: 4, color: 'green'},
];
export const projects: Project[] = [
  {id: 'website', organizationId: 'technova', name: 'Website Redesign', category: 'Web Development', color: 'purple', letter: 'W', due: 'Apr 25', health: 'On track'},
  {id: 'mobile', organizationId: 'technova', name: 'Mobile App', category: 'Mobile Development', color: 'blue', letter: 'M', due: 'Apr 28', health: 'On track'},
  {id: 'marketing', organizationId: 'technova', name: 'Marketing Campaign', category: 'Marketing', color: 'green', letter: 'M', due: 'May 02', health: 'At risk'},
  {id: 'api', organizationId: 'technova', name: 'API Development', category: 'Backend Development', color: 'orange', letter: 'A', due: 'May 10', health: 'On track'},
  {id: 'design', organizationId: 'technova', name: 'Design System', category: 'UI / UX', color: 'blue', letter: 'D', due: 'May 20', health: 'Not started'},
  {id: 'bright-launch', organizationId: 'bright', name: 'Product Launch', category: 'Launch Planning', color: 'blue', letter: 'P', due: 'May 12', health: 'On track'},
  {id: 'pixel-brand', organizationId: 'pixel', name: 'Brand Refresh', category: 'Brand Design', color: 'green', letter: 'B', due: 'May 16', health: 'On track'},
];
const websiteTasks: [string, Status, string, string, string][] = [
  ['Setup project repository', 'To Do', 'Backend', 'Apr 18', 'Rohan Mehta'],
  ['Design landing page', 'To Do', 'Frontend', 'Apr 20', 'Priya Sharma'],
  ['Write API documentation', 'To Do', 'Documentation', 'Apr 22', 'Mayank Singh'],
  ['Implement user authentication', 'In Progress', 'Backend', 'Apr 19', 'Mayank Singh'],
  ['Create responsive navbar', 'In Progress', 'Frontend', 'Apr 21', 'Priya Sharma'],
  ['Integrate email delivery', 'In Progress', 'Backend', 'Apr 23', 'Rohan Mehta'],
  ['UI components testing', 'Blocked', 'Frontend', 'Apr 17', 'Mayank Singh'],
  ['Code review', 'Blocked', 'Backend', 'Apr 19', 'Anjali Verma'],
  ['Project setup and configuration', 'Done', 'DevOps', 'Apr 15', 'Rohan Mehta'],
  ['Database schema design', 'Done', 'Backend', 'Apr 16', 'Mayank Singh'],
  ['Initial UI mockups', 'Done', 'Design', 'Apr 14', 'Priya Sharma'],
  ['Agree on project scope', 'Done', 'Design', 'Apr 12', 'Mayank Singh'],
];
export const initialTasks: Task[] = [
  ...websiteTasks.map(([title, status, label, due, assignee], index) => ({id: `website-${index}`, projectId: 'website', title, status, label, due, assignee, overdue: Number(due.slice(4)) < 20})),
  {id: 'mobile-1', projectId: 'mobile', title: 'Design homepage UI', status: 'In Progress', label: 'Frontend', due: 'Apr 22', assignee: 'Mayank Singh', overdue: false},
  {id: 'mobile-2', projectId: 'mobile', title: 'Map the onboarding journey', status: 'Done', label: 'Design', due: 'Apr 18', assignee: 'Priya Sharma', overdue: false},
  {id: 'marketing-1', projectId: 'marketing', title: 'Create marketing assets', status: 'To Do', label: 'Design', due: 'Apr 25', assignee: 'Mayank Singh', overdue: false},
  {id: 'api-1', projectId: 'api', title: 'Update database schema', status: 'Blocked', label: 'Backend', due: 'Apr 20', assignee: 'Mayank Singh', overdue: false},
  {id: 'api-2', projectId: 'api', title: 'Draft endpoint specification', status: 'Done', label: 'Documentation', due: 'Apr 18', assignee: 'Rohan Mehta', overdue: false},
  {id: 'design-1', projectId: 'design', title: 'Define color and typography tokens', status: 'To Do', label: 'Design', due: 'May 05', assignee: 'Priya Sharma', overdue: false},
  {id: 'bright-1', projectId: 'bright-launch', title: 'Prepare launch checklist', status: 'In Progress', label: 'Documentation', due: 'May 02', assignee: 'Mayank Singh', overdue: false},
  {id: 'pixel-1', projectId: 'pixel-brand', title: 'Explore the new brand direction', status: 'To Do', label: 'Design', due: 'May 06', assignee: 'Mayank Singh', overdue: false},
];
