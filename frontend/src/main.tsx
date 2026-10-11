import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './accounts/AccountApp';
import './styles.css';

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>);
