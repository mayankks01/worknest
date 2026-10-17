import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import Preview from '../App';
import { Icon } from '../components/Icon';
import { ApiError, getSession, postAuth, type Account } from './api';
import './accounts.css';
import Workspace from '../organizations/Workspace';
import InviteAccept from '../organizations/InviteAccept';

const paths = ['/login', '/register', '/forgot-password', '/reset-password', '/verify-email', '/resend-verification'];

function Frame({ children }: { children: ReactNode }) {
  return <div className="account-layout"><a className="skip-link" href="#account-main">Skip to sign-in content</a>
    <aside className="account-story">
      <a className="account-brand" href="/"><span className="brand-symbol"><Icon name="logo" /></span>WorkNest</a>
      <div className="story-content"><span className="story-eyebrow">A LITTLE CLARITY. A LOT OF PROGRESS.</span><h1>Your team.<br />Your space.<br /><span>Your next big thing.</span></h1><p>A private place to bring your people, projects, and everyday progress together.</p>
        <div className="story-board" aria-hidden="true"><div className="story-board-title"><span className="project-mark purple">W</span><div><strong>Website Redesign</strong><small>Great work starts with a clear plan.</small></div><span className="story-dot" /></div><div className="story-columns">{['To do', 'In progress', 'Done'].map((title, i) => <div key={title}><small>{title}</small><div className={`story-card story-card-${i}`}><span /><span /><i /></div><div className="story-card"><span /><i /></div></div>)}</div><div className="story-board-footer"><span>One shared direction.</span><span className="story-avatars">PS&nbsp; RM&nbsp; MK</span></div></div>
      </div><div className="story-footer"><span>WorkNest</span><span>Built around your team.</span></div>
    </aside>
    <main className="account-main" id="account-main" tabIndex={-1}><div className="account-top"><span>Your workspace starts here</span><a href="/preview">Explore the design preview <span aria-hidden="true">↗</span></a></div><div className="account-card">{children}</div><footer className="account-footer">Your team. Your space. <span>WorkNest</span></footer></main>
  </div>;
}

export default function AccountApp() {
  const path = location.pathname.replace(/\/$/, '') || '/';
  const [user, setUser] = useState<Account | null>(null);
  const [loading, setLoading] = useState(path !== '/preview');
  const [offline, setOffline] = useState('');
  // Keep bearer links in memory and remove them from the address bar immediately.
  const [token] = useState(() => location.hash.slice(1));
  useEffect(() => { if (location.hash && path !== '/preview') history.replaceState(null, '', location.pathname); }, [path]);
  async function refresh() {
    try { const result = await getSession(); setUser(result.user); setOffline(''); }
    catch (error) { setOffline(error instanceof Error ? error.message : 'Unable to connect.'); setUser(null); }
    finally { setLoading(false); }
  }
  useEffect(() => {
    if (path === '/preview') return;
    void refresh();
    const check = () => { if (document.visibilityState === 'visible') void refresh(); };
    window.addEventListener('focus', check); document.addEventListener('visibilitychange', check);
    const timer = window.setInterval(check, 60000);
    return () => { window.removeEventListener('focus', check); document.removeEventListener('visibilitychange', check); clearInterval(timer); };
  }, [path]);
  if (path === '/preview') return <><div className="preview-return"><a href="/">← Back to your account</a><span>Sample data only</span></div><Preview /></>;
  const route = paths.includes(path) ? path : user ? '/account' : '/login';
  if (loading && !paths.includes(path)) return <div className="wn-opening" role="status"><span className="brand-symbol"><Icon name="logo" /></span>Opening WorkNest…</div>;
  if (!loading && !offline && path === '/invitations/accept') return <Frame><InviteAccept user={user} token={token} refresh={refresh} /></Frame>;
  if (!loading && !offline && route === '/account' && user?.verified_at) return <Workspace user={user} />;
  return <Frame>{loading ? <div role="status" className="account-loading">Opening WorkNest…</div> : offline ? <><h2>Let’s reconnect</h2><p className="account-description">{offline}</p><button className="account-primary" onClick={() => { setLoading(true); void refresh(); }}>Try again</button></> : route === '/account' && user ? <AccountHome user={user} refresh={refresh} /> : <AccountForm key={route} route={route} token={token} />}</Frame>;
}

function AccountHome({ user, refresh }: { user: Account; refresh: () => Promise<void> }) {
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  async function act(action: 'logout' | 'resend-verification') {
    setBusy(true); setMessage('');
    try { const result = await postAuth(action, action === 'logout' ? {} : { email: user.email }); if (action === 'logout') location.assign('/login'); else setMessage(result.message); }
    catch (error) { setMessage(error instanceof Error ? error.message : 'Please try again.'); }
    finally { setBusy(false); }
  }
  return <><span className={`account-status ${user.verified_at ? 'verified' : ''}`}>{user.verified_at ? '✓ Email verified' : 'Email verification needed'}</span><h2>Welcome, {user.display_name.split(' ')[0]}.</h2><p className="account-description">You’re signed in to WorkNest.</p><div className="account-identity"><span className="profile-avatar">{user.display_name.slice(0, 2).toUpperCase()}</span><div><strong>{user.display_name}</strong><span>{user.email}</span></div></div>
    {!user.verified_at ? <div className="account-next"><h3>Check your inbox</h3><p>Verify your email before creating or joining an organization. Your verification link expires after 24 hours.</p><button disabled={busy} className="account-primary" onClick={() => void act('resend-verification')}>Resend verification email</button><button className="account-link" onClick={() => void refresh()}>I’ve verified my email — refresh</button></div> : <div className="account-next"><Icon name="organizations" /><h3>Your account is ready</h3><p>Organization setup is coming in the next build step. You can explore the dashboard design while we build your workspace.</p><a className="account-primary" href="/preview">Explore the design preview</a></div>}
    {message && <p role="status" className="account-message">{message}</p>}<button disabled={busy} className="account-link signout" onClick={() => void act('logout')}>{busy ? 'Please wait…' : 'Sign out'}</button></>;
}

function AccountForm({ route, token }: { route: string; token: string }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [success, setSuccess] = useState(false);
  const [errors, setErrors] = useState<Record<string, string[]>>({});
  const [showPassword, setShowPassword] = useState(false);
  const notice = useRef<HTMLDivElement>(null);
  const registration = route === '/register';
  const login = route === '/login';
  const reset = route === '/reset-password';
  const verify = route === '/verify-email';
  const resend = route === '/resend-verification';
  const title = registration ? 'Make room for great work.' : login ? 'Welcome back.' : reset ? 'Choose a new password.' : verify ? 'Verify your email.' : resend ? 'A fresh link, on its way.' : 'Forgot your password?';
  const description = registration ? 'Create your account and bring your team a little closer.' : login ? 'Sign in to pick up where you left off.' : reset ? 'Choose a strong password you haven’t used before.' : verify ? 'Confirm your email to get your WorkNest account ready.' : resend ? 'Enter your account email to request a new verification link.' : 'It happens. We’ll email you a link to get back in.';
  const label = registration ? 'Create account' : login ? 'Sign in' : reset ? 'Reset password' : verify ? 'Verify email' : resend ? 'Send verification link' : 'Send reset link';
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setMessage(''); setErrors({});
    const form = new FormData(event.currentTarget);
    const data: Record<string, string> = {};
    for (const [key, value] of form.entries()) data[key] = String(value);
    if (reset || verify) data.token = token;
    const endpoint = registration ? 'register' : login ? 'login' : reset ? 'password-reset/confirm' : verify ? 'verify-email' : resend ? 'resend-verification' : 'password-reset';
    try {
      const result = await postAuth(endpoint, data);
      if (login) { location.assign('/'); return; }
      setSuccess(true); setMessage(result.message);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Please try again.');
      if (error instanceof ApiError) setErrors(error.details);
    } finally { setBusy(false); requestAnimationFrame(() => notice.current?.focus()); }
  }
  function fieldError(name: string) { return errors[name] ? <span id={`${name}-error`} className="field-error">{errors[name].join(' ')}</span> : null; }
  return <><div className="account-kicker">{registration ? 'GET STARTED' : login ? 'YOUR WORKSPACE AWAITS' : 'YOUR ACCOUNT'}</div><h2>{success ? verify ? 'Email verified.' : reset ? 'You’re ready to sign in.' : 'Check your inbox.' : title}</h2><p className="account-description">{success ? reset ? 'Your password has been updated. Sign in with your new password.' : verify ? 'Your email is confirmed. Continue to your account.' : 'If this address is eligible, an email will arrive shortly. Check your spam folder too.' : description}</p>
    {message && <div ref={notice} tabIndex={-1} role={success ? 'status' : 'alert'} className={`account-message ${success ? 'success' : 'error'}`}>{message}</div>}
    {success ? <div className="account-success-actions"><a className="account-primary" href={verify ? '/' : '/login'}>{verify ? 'Continue to WorkNest' : 'Back to sign in'}</a>{registration && <a href="/resend-verification">Need another verification link?</a>}</div> : (reset || verify) && !token ? <div className="account-next"><p>This link is missing or has already been opened. Request a fresh email to continue.</p><a className="account-primary" href={reset ? '/forgot-password' : '/resend-verification'}>Request a new link</a></div> : <form onSubmit={submit} className="account-form">
      {registration && <label htmlFor="display_name">Full name<input id="display_name" name="display_name" autoComplete="name" placeholder="Your name" required maxLength={80} aria-invalid={!!errors.display_name} aria-describedby={errors.display_name ? 'display_name-error' : undefined} />{fieldError('display_name')}</label>}
      {!reset && !verify && <label htmlFor="email">Email address<input id="email" name="email" type="email" autoComplete="email" placeholder="you@company.com" required maxLength={254} aria-invalid={!!errors.email} aria-describedby={errors.email ? 'email-error' : undefined} />{fieldError('email')}</label>}
      {(registration || login || reset) && <label htmlFor="password"><span className="password-label">Password{login && <a href="/forgot-password">Forgot password?</a>}</span><span className="password-input"><input id="password" name="password" aria-label="Password" type={showPassword ? 'text' : 'password'} autoComplete={login ? 'current-password' : 'new-password'} required maxLength={128} minLength={login ? 1 : 12} placeholder={login ? 'Enter your password' : 'At least 12 characters'} aria-invalid={!!errors.password} aria-describedby={errors.password ? 'password-error' : !login ? 'password-hint' : undefined} /><button type="button" onClick={() => setShowPassword(!showPassword)} aria-label={showPassword ? 'Hide password' : 'Show password'}>{showPassword ? 'Hide' : 'Show'}</button></span>{fieldError('password')}{!login && <small id="password-hint">Use 12–128 characters. Avoid common passwords and personal details.</small>}</label>}
      {reset && <p className="account-security-note">This link expires after 30 minutes. Resetting signs you out on all devices.</p>}
      {verify && <p className="account-security-note">Verification links can be used once and expire after 24 hours.</p>}
      <button className="account-primary" type="submit" disabled={busy}>{busy ? 'Please wait…' : label}<span aria-hidden="true"> →</span></button>
    </form>}
    {!success && (reset || verify) && token && <div className="account-alternate"><a href={reset ? '/forgot-password' : '/resend-verification'}>Request a new link</a></div>}
    {!success && <div className="account-alternate">{login ? <>New to WorkNest? <a href="/register">Create an account</a><p><a href="/resend-verification">Resend verification email</a></p></> : registration ? <>Already have an account? <a href="/login">Sign in</a></> : <a href="/login">← Back to sign in</a>}</div>}
  </>;
}
