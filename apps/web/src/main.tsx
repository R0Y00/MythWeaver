import React from 'react';
import {createRoot} from 'react-dom/client';
import App from './App';
import './style.css';
import './assistant.css';
import './pages/quality.css';
import './pages/player-rehearsal.css';
import './pages/player-inspector.css';
import './pages/agent-progress.css';
import './workbench.css';

createRoot(document.getElementById('root')!).render(<App/>);
