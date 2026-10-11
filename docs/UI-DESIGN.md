# WorkNest frontend design baseline

The user supplied **WorkNest SaaS Dashboard Mockup.png** and requested that it guide the frontend and UI. A copy is stored at `ui-reference.png` beside this document. Use its visual language for all later approved stages.

## Visual system

- Navy navigation rail (`#10264b` to `#0b1a2c`) with a blue active item, compact outline icons, and an organization switcher.
- Bright blue primary actions (`#2856ff`), a pale blue-gray canvas (`#f6f9fd`), white panels, and restrained blue borders (`#e0e9f7`).
- Inter typography with system fallbacks; compact headings, readable secondary labels, and restrained shadows.
- Blue, green, purple, and red statistic cards; colored project initials; subtle progress bars and rounded status badges.
- Desktop dashboard: statistics above Recent Projects, My Tasks, Your Organizations, and Recent Activity. Reflow panels on smaller screens.
- Project overview: organization header, searchable/filterable project table, progress, task counts, due dates, and status.
- Project workspace: project header, Board/List selector, task filters, and tinted board columns. Mobile columns stack vertically; tables scroll inside their panel.

## PRD takes precedence over example content

- Use To Do, In Progress, Blocked, and Done. The mockup's In Review label does not add a new task status.
- Activity replaces Messages because chat is excluded from the MVP.
- Project/task panels and their counts use one active organization. A global organization directory may list the user's memberships. Do not implement a cross-organization task query just because the mockup combines organization names.
- Example names, counts, progress, dates, labels, avatars, and project health badges are presentation fixtures. They do not define new backend fields or permissions.
- Labels and project health indicators need a requirements decision before persistence; they are not additional approved MVP scope.

## Current UI preview

The foundation's earlier green welcome screen has been replaced with a frontend-only dashboard, organizations directory, project table, task list, and project board. A persistent notice identifies the content as example data. Every mutation is held in React state, clears on refresh, and can be reset using Reset preview. No user accounts are created, no session is implied, and no example records reach the database.

Navigation, search, filters, organization switching, task completion, and status selection work within the preview. Project creation, invitations, and task creation remain disabled and labeled for later stages. Notifications, settings, and activity navigation are marked Later. Task movement uses an accessible status selector; drag-and-drop remains part of the future project/task implementation.

The example timeline is 20 April 2025. Statistics and progress derive from the fixtures rather than copying inconsistent numbers from the image. Initial avatars avoid implying real member photos.

## Next stages

Step 3 account screens now use this palette and typography. The sample dashboard is separately available at `/preview`; `/` shows real account status. Step 4 remains pending approval. Connect organization, project, task, and activity views to authenticated server data only in their corresponding approved stages. Keep preview fixtures outside the authenticated production path.
