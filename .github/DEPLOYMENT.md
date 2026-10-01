# GitHub Actions → VPS deployment

## 1. One-time VPS setup

```bash
# On the VPS (Ubuntu/Debian example)
sudo apt update && sudo apt install -y git docker.io docker-compose-plugin
sudo usermod -aG docker deploy   # your deploy user

mkdir -p ~/el7-hiq
cd ~/el7-hiq
git clone https://github.com/YOUR_ORG/el7-hiq.git .
cp .env.example .env
# Edit .env: API keys, and for production:
#   VITE_API_BASE_URL=https://api.yourdomain.com   (or http://YOUR_IP:8000)
#   DATAMAP_CORS_ORIGINS=https://yourdomain.com,http://YOUR_IP
```

Create an SSH key **only for GitHub Actions**:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/github_actions_deploy -N ""
cat ~/.ssh/github_actions_deploy.pub >> ~/.ssh/authorized_keys
```

Copy the **private** key (`github_actions_deploy`) — you will paste it into GitHub.

## 2. Configure GitHub (repository settings)

1. Open your repo on GitHub → **Settings** → **Secrets and variables** → **Actions**.
2. Click **New repository secret** for each:

| Name | Value |
|------|--------|
| `VPS_HOST` | Server IP or DNS name |
| `VPS_USER` | `deploy` (or your SSH user) |
| `VPS_SSH_KEY` | Full private key file contents |
| `VPS_PORT` | (optional) `22` |
| `VPS_APP_DIR` | (optional) default is `~/el7-hiq` on the server |

3. **Actions** tab → ensure workflows are allowed: **Settings** → **Actions** → **General** → *Workflow permissions* → read access is enough for deploy (secrets are used at runtime).

## 3. What runs automatically

| Workflow | When | What it does |
|----------|------|----------------|
| `ci.yml` | Push/PR to `main` | Installs deps, smoke-imports API, `npm run build` |
| `deploy-vps.yml` | Push to `main` or **Run workflow** | SSH → `git pull` → `docker compose -f docker-compose.prod.yml up -d --build` |

## 4. Manual deploy test

**Actions** → **Deploy to VPS** → **Run workflow** → branch `main`.

On the server after success:

- Frontend: `http://YOUR_IP/` (port 80)
- API health: `http://YOUR_IP:8000/health`

Put nginx/Caddy in front with TLS when you have a domain.

## 5. Troubleshooting

- **Permission denied (publickey)** — wrong `VPS_SSH_KEY` or public key not in `authorized_keys`.
- **Create .env on the server** — workflow requires `.env` in `VPS_APP_DIR`.
- **Build timeout** — backend image is large; `command_timeout` is 45m in the workflow.
- **CORS errors in browser** — set `DATAMAP_CORS_ORIGINS` to your real frontend URL and rebuild if you change `VITE_API_BASE_URL`.
