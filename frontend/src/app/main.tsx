import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import 'easymde/dist/easymde.min.css'
import App from './App'
import { consumeSsoTokenFromUrl } from './utils/userIdentity'

// Capture the Launchpad SSO token handed off via #lp_token before the app renders.
consumeSsoTokenFromUrl()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
