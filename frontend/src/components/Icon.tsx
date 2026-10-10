export type IconName = 'logo' | 'home' | 'organizations' | 'folder' | 'check' | 'activity' | 'bell' | 'settings' | 'search' | 'chevron' | 'down' | 'arrow' | 'back' | 'plus' | 'tasks' | 'clock' | 'info' | 'tick' | 'board' | 'list' | 'dots' | 'calendar';
const paths: Record<IconName, string> = {
  logo: 'M9 5H5v5h4z M19 5h-4v5h4z M9 15H5v5h4z M19 15h-4v5h4z M7 10v5 M17 10v5 M9 7h6 M9 17h6 M12 7v10',
  home: 'm3 10 9-7 9 7 M5 9v12h5v-7h4v7h5V9',
  organizations: 'M3 21V10h6v11 M9 21V3h6v18 M15 21v-8h6v8 M1 21h22 M11 6h2 M11 9h2 M11 12h2 M5 13h2 M5 16h2 M17 16h2',
  folder: 'M3 7V5a1 1 0 0 1 1-1h5l2 3h9a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V7z M3 9h18',
  check: 'M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z m2 9 3 3 7-7',
  activity: 'M3 12h4l3-8 4 16 3-8h4', bell: 'M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9 M10 21h4',
  settings: 'm9 3-1 3-3 1-2 4 2 2v3l3 2 3-1 3 1 3-2v-3l2-2-2-4-3-1-1-3z M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0',
  search: 'M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0 m-2 5 6 6', chevron: 'm9 5 7 7-7 7', down: 'm6 9 6 6 6-6',
  arrow: 'M4 12h16 m-6-6 6 6-6 6', back: 'M20 12H4 m6-6-6 6 6 6', plus: 'M12 4v16 M4 12h16',
  tasks: 'M7 4h14v17H3V4h4 M8 2h8v5H8z M7 11h10 M7 15h7', clock: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0 M12 7v5l3 2',
  info: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0 M12 11v6 M12 7v.1', tick: 'm5 12 4 4L19 6', board: 'M3 4h7v16H3z M14 4h7v16h-7z',
  list: 'M9 6h12 M9 12h12 M9 18h12 M3 6h1 M3 12h1 M3 18h1', dots: 'M12 5v.1 M12 12v.1 M12 19v.1',
  calendar: 'M5 5h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z M7 3v4 M17 3v4 M3 10h18',
};
export function Icon({name}: {name: IconName}) {
  return <svg className="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>;
}
