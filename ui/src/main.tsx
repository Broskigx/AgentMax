import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { Widget } from './components/Widget/Widget';
import { RecoveryTestWindow } from './components/RecoveryTest/RecoveryTestWindow';
import { installFrontendRecoveryLogging } from './lib/recoveryService';
import './styles/tailwind.css';

const isWidget = window.location.hash === '#widget';
const isRecoveryTest = window.location.hash === '#recovery-test';

installFrontendRecoveryLogging(isRecoveryTest ? 'recovery-test' : isWidget ? 'widget' : 'main');

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    {isRecoveryTest ? <RecoveryTestWindow /> : isWidget ? <Widget /> : <App />}
  </React.StrictMode>
);
