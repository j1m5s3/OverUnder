# GCP Deployment — OverUnder

This directory documents the GCP Cloud Run deployment for OverUnder prediction markets (Base Sepolia chain).

## Pre-provisioned Infrastructure

The following resources are already created in GCP — **do not recreate via terraform**:

- **Project**: `overunder-509107`
- **Region**: `us-central1`
- **Artifact Registry**: `us-central1-docker.pkg.dev/overunder-509107/overunder`
- **Deploy Service Account**: `github-deploy@overunder-509107.iam.gserviceaccount.com`
- **Workload Identity Federation**:
  - Pool: `github-pool`
  - Provider: `github`
  - Bound repository: `j1m5s3/OverUnder`
- **Cloud Run Services**:
  - `overunder-api` (FastAPI backend)
  - `overunder-web` (Next.js frontend)

### Secret Manager Secrets

The following secret names exist with **empty versions** (James fills later):

- `OU_JWT_SECRET` — JWT signing key (HS256, 32+ bytes recommended)
- `OU_DATABASE_URL` — PostgreSQL or SQLite connection string
- `OU_ANVIL_RPC_URL` — Base Sepolia RPC endpoint (chain_id 84532)
- `OU_PRIVY_APP_SECRET` — Privy app secret for auth validation
- `OU_MOONPAY_SECRET` — MoonPay API secret (PHASE2)
- `OU_OPERATOR_PRIVATE_KEY` — EOA private key for market operations (0x-prefixed hex)
- `OU_RELAYER_PRIVATE_KEY` — EOA private key for CLOB settlement (0x-prefixed hex)

## Deployment

Deployment is automated via `.github/workflows/deploy-gcp.yml` on push to `feat/mvp-docs` or manual trigger.

The workflow:
1. Authenticates using Workload Identity Federation (no JSON keys)
2. Builds `backend/Dockerfile` and `web/Dockerfile`
3. Pushes images to Artifact Registry with git SHA and `latest` tags
4. Deploys to Cloud Run with Secret Manager secrets and environment variables
5. Both services allow unauthenticated access (CORS on API, public web)

### Required GitHub Configuration

**Repository Secrets** (set at `https://github.com/j1m5s3/OverUnder/settings/secrets/actions`):
- `GCP_PROJECT_NUMBER` — GCP project number for WIF provider path (not project id)

**Repository Variables** (optional, set at `/settings/variables/actions`):
- `PRIVY_APP_ID` — Privy app identifier
- `NEXT_PUBLIC_API_URL` — API URL for Next.js build (defaults to placeholder)
- Contract addresses: `USDC_ADDRESS`, `CTF_ADDRESS`, `FACTORY_ADDRESS`, `EXCHANGE_ADDRESS`, `AMM_ADDRESS`, `ORACLE_ADDRESS`, `FEE_VAULT_ADDRESS`, `OU_TOKEN_ADDRESS`
- `COINBASE_ONRAMP_APP_ID` — Coinbase Pay app id

If contract addresses are not set as GitHub variables, you may alternatively:
- Create `deployments/84532.json` with deployment addresses
- Update the workflow to read from this file and set env vars

## Operations

### Filling Secrets

Use `gcloud` CLI to add secret versions (do **not** change your local default project):

```bash
# Example: Add JWT secret
echo -n "your-jwt-secret-32-bytes-or-more" | \
  gcloud secrets versions add OU_JWT_SECRET \
  --data-file=- \
  --project=overunder-509107

# Example: Add database URL (Cloud SQL or external PostgreSQL)
echo -n "postgresql://user:pass@host/overunder" | \
  gcloud secrets versions add OU_DATABASE_URL \
  --data-file=- \
  --project=overunder-509107

# Example: Add Base Sepolia RPC URL
echo -n "https://sepolia.base.org" | \
  gcloud secrets versions add OU_ANVIL_RPC_URL \
  --data-file=- \
  --project=overunder-509107

# Example: Add operator private key (0x-prefixed hex)
echo -n "0xabcdef1234567890..." | \
  gcloud secrets versions add OU_OPERATOR_PRIVATE_KEY \
  --data-file=- \
  --project=overunder-509107

# Example: Add relayer private key
echo -n "0x1234567890abcdef..." | \
  gcloud secrets versions add OU_RELAYER_PRIVATE_KEY \
  --data-file=- \
  --project=overunder-509107

# Example: Add Privy app secret
echo -n "privy-secret-here" | \
  gcloud secrets versions add OU_PRIVY_APP_SECRET \
  --data-file=- \
  --project=overunder-509107
```

### Viewing Service Status

```bash
# List Cloud Run services
gcloud run services list \
  --platform=managed \
  --region=us-central1 \
  --project=overunder-509107

# Get API service URL
gcloud run services describe overunder-api \
  --platform=managed \
  --region=us-central1 \
  --format='value(status.url)' \
  --project=overunder-509107

# Get Web service URL
gcloud run services describe overunder-web \
  --platform=managed \
  --region=us-central1 \
  --format='value(status.url)' \
  --project=overunder-509107
```

### Viewing Logs

```bash
# Stream API logs
gcloud logs tail --project=overunder-509107 \
  --filter='resource.labels.service_name="overunder-api"'

# Stream Web logs
gcloud logs tail --project=overunder-509107 \
  --filter='resource.labels.service_name="overunder-web"'
```

### Rollback to Previous Revision

Cloud Run maintains multiple revisions. To rollback:

```bash
# List revisions
gcloud run revisions list \
  --service=overunder-api \
  --region=us-central1 \
  --project=overunder-509107

# Rollback API to specific revision
gcloud run services update-traffic overunder-api \
  --to-revisions=overunder-api-00042-xyz=100 \
  --region=us-central1 \
  --project=overunder-509107

# Rollback Web to specific revision
gcloud run services update-traffic overunder-web \
  --to-revisions=overunder-web-00023-abc=100 \
  --region=us-central1 \
  --project=overunder-509107
```

Alternatively, use the GCP Console:
1. Navigate to Cloud Run → Select service
2. Click "Manage Traffic"
3. Select desired revision and set to 100%

### Manual Deployment

To deploy manually without GitHub Actions:

```bash
# Build and push images
docker build -t us-central1-docker.pkg.dev/overunder-509107/overunder/api:manual ./backend
docker build -t us-central1-docker.pkg.dev/overunder-509107/overunder/web:manual ./web
docker push us-central1-docker.pkg.dev/overunder-509107/overunder/api:manual
docker push us-central1-docker.pkg.dev/overunder-509107/overunder/web:manual

# Deploy API
gcloud run deploy overunder-api \
  --image=us-central1-docker.pkg.dev/overunder-509107/overunder/api:manual \
  --region=us-central1 \
  --project=overunder-509107

# Deploy Web
gcloud run deploy overunder-web \
  --image=us-central1-docker.pkg.dev/overunder-509107/overunder/web:manual \
  --region=us-central1 \
  --project=overunder-509107
```

## Environment Variables

### Backend (overunder-api)

**From Secret Manager**:
- `JWT_SECRET` — JWT signing key
- `DATABASE_URL` — Database connection string
- `ANVIL_RPC_URL` — Base Sepolia RPC (mapped from OU_ANVIL_RPC_URL)
- `PRIVY_APP_SECRET` — Privy auth secret
- `OPERATOR_PRIVATE_KEY` — Operator EOA key
- `RELAYER_PRIVATE_KEY` — Relayer EOA key

**Set in workflow**:
- `CHAIN_ID=84532` — Base Sepolia chain ID
- `PRIVY_APP_ID` — From GitHub variable
- Contract addresses (from GitHub variables or empty)

See `infra/gcp/cloudrun.env.example` for complete list.

### Frontend (overunder-web)

**Build-time**:
- `NEXT_PUBLIC_API_URL` — Backend API URL (set during build)

**Runtime**:
- `PORT` — Cloud Run sets this (default 8080)

## Security Notes

### API Service

The API is deployed with `--allow-unauthenticated` for MVP smoke testing. This means:
- Any client can call the API without GCP IAM authentication
- CORS is configured to allow all origins in `backend/app/main.py`
- For production, consider:
  - Removing `--allow-unauthenticated` and using Cloud Run IAM
  - Adding Cloud Armor WAF rules
  - Restricting CORS origins to known frontends
  - Rate limiting via Cloud Endpoints or API Gateway

### Private Keys

- Operator and relayer private keys are stored in Secret Manager
- These keys control on-chain market creation, pausing, and CLOB settlement
- Rotate keys if compromised
- Consider multi-sig or timelock upgrades for production (PHASE2)

### Contract Deployment

Contract deployment is **separate** from app deployment:
1. Deploy contracts to Base Sepolia using Foundry/Hardhat
2. Record addresses in GitHub variables or `deployments/84532.json`
3. Redeploy Cloud Run services to pick up new addresses

The current workflow does **not** deploy contracts — only the FastAPI backend and Next.js frontend.

## Database

The workflow maps `OU_DATABASE_URL` from Secret Manager. For MVP:

- **SQLite** (`sqlite+aiosqlite:///./overunder.db`) works locally but is ephemeral on Cloud Run (file disappears on redeploy)
- **Cloud SQL** (PostgreSQL) recommended for persistent data:
  ```bash
  # Create Cloud SQL instance (if needed)
  gcloud sql instances create overunder-db \
    --database-version=POSTGRES_15 \
    --tier=db-f1-micro \
    --region=us-central1 \
    --project=overunder-509107
  
  # Create database
  gcloud sql databases create overunder \
    --instance=overunder-db \
    --project=overunder-509107
  
  # Set root password
  gcloud sql users set-password postgres \
    --instance=overunder-db \
    --password=SECURE_PASSWORD \
    --project=overunder-509107
  
  # Get connection name
  gcloud sql instances describe overunder-db \
    --format='value(connectionName)' \
    --project=overunder-509107
  ```

  Then update `OU_DATABASE_URL` secret with:
  ```
  postgresql+asyncpg://postgres:PASSWORD@/overunder?host=/cloudsql/overunder-509107:us-central1:overunder-db
  ```

  And add Cloud SQL connection to Cloud Run deploy step:
  ```bash
  --add-cloudsql-instances=overunder-509107:us-central1:overunder-db
  ```

## Project Number vs Project ID

- **Project ID**: `overunder-509107` (used in most commands)
- **Project Number**: Numeric ID (used in WIF provider path)

To get project number:
```bash
gcloud projects describe overunder-509107 --format='value(projectNumber)'
```

Store this as `GCP_PROJECT_NUMBER` GitHub secret.

## Troubleshooting

### Deployment fails with authentication error

- Verify Workload Identity Federation is configured correctly
- Check that `GCP_PROJECT_NUMBER` secret is set in GitHub
- Ensure service account `github-deploy@overunder-509107.iam.gserviceaccount.com` has necessary roles:
  - Cloud Run Admin
  - Artifact Registry Writer
  - Service Account User
  - Secret Manager Secret Accessor

### Service returns 500 errors

- Check Cloud Run logs for stack traces
- Verify all required secrets have versions in Secret Manager
- Ensure `CHAIN_ID=84532` and contract addresses are set
- Check that `ANVIL_RPC_URL` points to a valid Base Sepolia RPC

### Database connection errors

- If using SQLite, data is ephemeral — migrate to Cloud SQL
- If using Cloud SQL, verify connection string format and Cloud SQL proxy connection
- Check that service account has Cloud SQL Client role

### CORS errors in browser

- API allows all origins in `backend/app/main.py` for MVP
- Ensure `NEXT_PUBLIC_API_URL` in web build points to deployed API URL
- Check browser console for actual error (may be auth, not CORS)

## Next Steps

1. Fill Secret Manager secrets (JWT, DATABASE_URL, RPC, private keys)
2. Deploy contracts to Base Sepolia and record addresses
3. Set GitHub variables for contract addresses and Privy app ID
4. Trigger workflow via push to `feat/mvp-docs` or manual dispatch
5. Verify services are running: `gcloud run services list --project=overunder-509107`
6. Test API health: `curl https://overunder-api-XXX.run.app/health`
7. Test web frontend in browser
8. Configure custom domain (optional)
9. Set up Cloud SQL for persistent database (if needed)
10. Review security (remove `--allow-unauthenticated`, add rate limits, etc.)
