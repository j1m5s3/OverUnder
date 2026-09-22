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

- `OU_JWT_SECRET` — JWT signing key (HS256, 32+ bytes recommended) **[REQUIRED]**
- `OU_DATABASE_URL` — PostgreSQL connection string (must NOT be SQLite; Cloud Run needs persistent DB) **[REQUIRED]**
- `OU_ANVIL_RPC_URL` — Base Sepolia RPC endpoint (chain_id 84532) **[REQUIRED]**
- `OU_CDP_API_KEY_ID` — Coinbase CDP API key id **[REQUIRED for wallets]**
- `OU_CDP_API_KEY_SECRET` — Coinbase CDP API key secret **[REQUIRED for wallets]**
- `OU_MOONPAY_SECRET` — MoonPay API secret (mapped to MOONPAY_SECRET env; PHASE2)
- `OU_OPERATOR_PRIVATE_KEY` — EOA private key for market operations (0x-prefixed hex; **Sepolia smoke only**)
- `OU_RELAYER_PRIVATE_KEY` — EOA private key for CLOB settlement (0x-prefixed hex; **Sepolia smoke only**)

**IMPORTANT**: The preflight check will **fail** if:
- `OU_JWT_SECRET`, `OU_ANVIL_RPC_URL`, or `OU_DATABASE_URL` have no versions
- `OU_DATABASE_URL` contains `sqlite` or `aiosqlite` (Cloud Run requires Cloud SQL or external PostgreSQL)

## Deployment

Deployment is automated via `.github/workflows/deploy-gcp.yml` using **manual workflow dispatch only**.

To deploy:
1. Navigate to GitHub Actions tab: `https://github.com/j1m5s3/OverUnder/actions/workflows/deploy-gcp.yml`
2. Click "Run workflow" → Select branch → "Run workflow" button

The workflow:
1. Authenticates using Workload Identity Federation (no JSON keys)
2. Runs preflight checks on Secret Manager secrets (DATABASE_URL must be PostgreSQL, not SQLite)
3. Builds and pushes `backend/Dockerfile` to Artifact Registry
4. Deploys API to Cloud Run and captures the deployed URL
5. Builds `web/Dockerfile` with the **real API URL** as build arg
6. Pushes web image and deploys to Cloud Run
7. Both services allow unauthenticated access (CORS on API, public web)

### Required GitHub Configuration

**Repository Secrets** (set at `https://github.com/j1m5s3/OverUnder/settings/secrets/actions`):
- `GCP_PROJECT_NUMBER` — GCP project number for WIF provider path (not project id) **[REQUIRED]**

**Repository Variables** (optional, set at `/settings/variables/actions`):
- `CDP_PROJECT_ID` — Coinbase CDP project id
- Contract addresses: `USDC_ADDRESS`, `CTF_ADDRESS`, `FACTORY_ADDRESS`, `EXCHANGE_ADDRESS`, `AMM_ADDRESS`, `ORACLE_ADDRESS`, `FEE_VAULT_ADDRESS`, `OU_TOKEN_ADDRESS`
- `COINBASE_ONRAMP_APP_ID` — Coinbase Pay app id

**Note**: `NEXT_PUBLIC_API_URL` is **not** used as a GitHub variable. The workflow automatically captures the deployed API URL and rebuilds the web image with it.

## Operations

### Filling Secrets

Use `gcloud` CLI to add secret versions (do **not** change your local default project):

```bash
# Example: Add JWT secret
echo -n "your-jwt-secret-32-bytes-or-more" | \
  gcloud secrets versions add OU_JWT_SECRET \
  --data-file=- \
  --project=overunder-509107

# Example: Add database URL (Cloud SQL unix socket for overunder-pg instance)
echo -n "postgresql+asyncpg://user:pass@/overunder?host=/cloudsql/overunder-509107:us-central1:overunder-pg" | \
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

# Example: Add CDP API key secret
echo -n "cdp-api-key-secret-here" | \
  gcloud secrets versions add OU_CDP_API_KEY_SECRET \
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
- `CDP_API_KEY_ID` / `CDP_API_KEY_SECRET` — Coinbase CDP API credentials
- `OPERATOR_PRIVATE_KEY` — Operator EOA key
- `RELAYER_PRIVATE_KEY` — Relayer EOA key

**Set in workflow**:
- `CHAIN_ID=84532` — Base Sepolia chain ID
- `CDP_PROJECT_ID` — From GitHub variable
- Contract addresses (from GitHub variables or empty)

See `infra/gcp/cloudrun.env.example` for complete list.

### Frontend (overunder-web)

**Build-time**:
- `NEXT_PUBLIC_API_URL` — Backend API URL (automatically captured from deployed API service; rebuilt on each deploy)
- `NEXT_PUBLIC_CDP_PROJECT_ID` — Coinbase CDP project id (build-arg)

**Runtime**:
- `PORT` — Cloud Run sets this (default 8080)
- No secrets; web is a static Next.js build

## Security Notes

### ⚠️ Sepolia Smoke Configuration Only

This deployment configuration is **Base Sepolia testnet smoke testing only**:

- `--allow-unauthenticated` on both API and Web services (any client can call)
- CORS allows all origins in `backend/app/main.py`
- `OPERATOR_PRIVATE_KEY` and `RELAYER_PRIVATE_KEY` are mounted directly in the Cloud Run process
- Contract addresses and RPC are Base Sepolia (chain_id 84532)

### Before Mainnet (Base)

**Lock down API access**:
1. Remove `--allow-unauthenticated` from API service:
   ```bash
   gcloud run services update overunder-api \
     --no-allow-unauthenticated \
     --region=us-central1 \
     --project=overunder-509107
   ```
2. Grant Cloud Run Invoker role to web service identity:
   ```bash
   gcloud run services add-iam-policy-binding overunder-api \
     --member="serviceAccount:<web-service-account>@overunder-509107.iam.gserviceaccount.com" \
     --role="roles/run.invoker" \
     --region=us-central1 \
     --project=overunder-509107
   ```
3. Restrict CORS origins in `backend/app/main.py` to known frontends only
4. Add Cloud Armor WAF rules and rate limiting
5. Use API Gateway or Cloud Endpoints for request validation

**Upgrade private key management**:
- Move operator/relayer keys to Cloud KMS or multi-sig wallet (Gnosis Safe)
- Implement timelock for market operations
- Use dedicated relayer service with nonce/gas manager (not in-process keys)

**Additional hardening**:
- Enable VPC Connector for Cloud Run (private network)
- Set up Cloud Logging alerts for suspicious activity
- Configure Secret Manager rotation policies
- Review IAM bindings and remove overly broad permissions

### Private Keys (Current)

- Operator and relayer private keys are stored in Secret Manager and mounted as env vars
- These keys control on-chain market creation, pausing, and CLOB settlement
- **This is acceptable for Sepolia smoke testing only**
- Rotate keys if compromised
- For production, use Cloud KMS or hardware security modules

### Contract Deployment

Contract deployment is **separate** from app deployment:
1. Deploy contracts to Base Sepolia using Foundry/Hardhat
2. Record addresses in GitHub variables (see Required Setup section)
3. Redeploy via workflow dispatch to pick up new addresses

The workflow does **not** deploy contracts — only the FastAPI backend and Next.js frontend.

### Web Rebuild After API URL Changes

The web image is built with `NEXT_PUBLIC_API_URL` baked in at build time. If you manually change the API service URL or deploy to a different region:

1. The web image must be rebuilt with the new API URL
2. The workflow handles this automatically: it deploys API first, captures the URL, then rebuilds web
3. If you manually deploy API separately (e.g. `gcloud run deploy overunder-api ...`), run the full workflow again to rebuild web with the updated URL

**Note**: Changing the API service name or region after initial deployment will require updating the workflow's `API_SERVICE_NAME` or `GCP_REGION` env vars and re-running the workflow.

## Database

The workflow maps `OU_DATABASE_URL` from Secret Manager. For MVP:

- **SQLite** (`sqlite+aiosqlite:///./overunder.db`) works locally but is ephemeral on Cloud Run (file disappears on redeploy)
- **Cloud SQL** (PostgreSQL) recommended for persistent data

### Cloud SQL Connection (overunder-pg instance)

The deployed API connects to Cloud SQL instance **`overunder-pg`** in `us-central1`.

**Connection String Format** (unix socket):
```
postgresql+asyncpg://USER:PASSWORD@/overunder?host=/cloudsql/overunder-509107:us-central1:overunder-pg
```

**Important**:
- Replace `USER` and `PASSWORD` with actual database credentials (never commit real passwords to the repo)
- The unix socket form (`host=/cloudsql/...`) **requires** `--add-cloudsql-instances=overunder-509107:us-central1:overunder-pg` on the Cloud Run deploy command
- The workflow already includes this flag for the API service
- TCP connections (e.g. `postgresql://USER:PASSWORD@IP:5432/overunder`) do not require the `--add-cloudsql-instances` flag but are less secure

### Creating a New Cloud SQL Instance (if needed)

If you need to create a different Cloud SQL instance:

  ```bash
  # Create Cloud SQL instance
  gcloud sql instances create overunder-pg \
    --database-version=POSTGRES_15 \
    --tier=db-f1-micro \
    --region=us-central1 \
    --project=overunder-509107
  
  # Create database
  gcloud sql databases create overunder \
    --instance=overunder-pg \
    --project=overunder-509107
  
  # Set root password
  gcloud sql users set-password postgres \
    --instance=overunder-pg \
    --password=SECURE_PASSWORD \
    --project=overunder-509107
  
  # Get connection name
  gcloud sql instances describe overunder-pg \
    --format='value(connectionName)' \
    --project=overunder-509107
  ```

  Then update `OU_DATABASE_URL` secret with the connection string above.

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
3. Set GitHub variables for contract addresses and `CDP_PROJECT_ID`
4. Trigger workflow via push to `feat/mvp-docs` or manual dispatch
5. Verify services are running: `gcloud run services list --project=overunder-509107`
6. Test API health: `curl https://overunder-api-XXX.run.app/health`
7. Test web frontend in browser
8. Configure custom domain (optional)
9. Set up Cloud SQL for persistent database (if needed)
10. Review security (remove `--allow-unauthenticated`, add rate limits, etc.)
