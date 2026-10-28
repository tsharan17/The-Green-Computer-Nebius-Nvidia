# Host the demo free on Google Cloud Run + Neon

**Status: deployed and working (27 Oct 2026).** The first run exposed one bug (sign-up refused behind the HTTPS proxy), now fixed. Allow an hour on a fresh project.

What you get: a public `https://green-computer-….run.app` link (HTTPS, no sign-in needed to open it), accounts and projects kept in a free Neon PostgreSQL database, and a free scheduled ping that keeps the app awake.

```
judge's browser ──https──> Cloud Run (the app, 2 vCPU / 2 GB, scales to zero)
                              │            ▲
                              │            └── Cloud Scheduler: GET /api/v1/health every 5 min (keeps it warm)
                              ├── Neon PostgreSQL (accounts, projects, plans)
                              └── Nebius Token Factory (Nemotron), key kept in Secret Manager
```

## What it costs

Nothing, if the demo stays inside the free allowances: Cloud Run 2 million requests, 180,000 vCPU-seconds and 360,000 GiB-seconds a month; Neon Free 0.5 GB storage and 100 compute-hours a month, no card; Cloud Scheduler 3 free jobs; Secret Manager 6 active secret versions. Google Cloud still needs a billing account with a card linked, so set the budget alert in step 6. Check the current limits on the [Cloud Run pricing](https://cloud.google.com/run/pricing) and [Neon pricing](https://neon.com/pricing) pages before you rely on them.

## Limits to know

- **Uploaded files and exports are kept on the instance's disk, which is wiped when Cloud Run restarts it.** Accounts, projects, analyses and plans survive (they are in Neon). A judge who generates an export and downloads it straight away is fine; one who comes back after a restart will find the old export's download fails and should create it again.
- **A cold start takes 5 to 10 seconds** if the keep-warm ping ever misses. Neon pauses after 5 idle minutes and wakes in about a second on the next request.
- The demo runs with `GC_PUBLIC_DEMO=1`: GPU jobs are refused; the Green Agent (40 an hour) and datasheet import (5 an hour) are rate limited per visitor address; uploads are capped.

## Step by step

You need: a Google account, a card for Google Cloud's identity check, and a Neon account (sign in with Google or GitHub). `gcloud` is already installed and signed in on this machine.

### 1. Make the free database (Neon, about 5 minutes)

1. Go to [neon.com](https://neon.com) and sign up.
2. Create a project named `green-computer`. Choose the region **AWS US East (N. Virginia)**. Keep the default database name.
3. Open **Connect**. **Turn Connection pooling OFF.** Copy the connection string (it starts with `postgresql://`). Keep it private: it contains the password.

### 2. Make a Google Cloud project just for this demo (about 10 minutes)

Use a new project so that nothing you already have is touched. Your `gcloud` is currently pointed at a different project (`myfood-494011`); the script always names its project explicitly, so it will not use that one.

1. Open [console.cloud.google.com/projectcreate](https://console.cloud.google.com/projectcreate). Name it `green-computer-demo`. Look at the **Project ID** under the name: it has a suffix (for example `green-computer-demo-472910`). You need that ID, not the name.
2. Link billing: [console.cloud.google.com/billing](https://console.cloud.google.com/billing). Add a card if asked, then open **My projects** and link `green-computer-demo`. This is Google's identity check; you are not charged inside the free allowance.

### 3. Fill in the settings file

From the project folder (`C:\MY PROJECTS\The Green Computer\The Green Computer`), in cmd or PowerShell:

```
copy deploy\cloudrun\env.example deploy\cloudrun\.env.cloudrun
notepad deploy\cloudrun\.env.cloudrun
```

Fill in these lines and save (the name must stay `.env.cloudrun`, not `.env.cloudrun.txt`):

- `PROJECT_ID=` the project ID from step 2
- `DATABASE_URL=` the Neon string from step 1
- `TOKEN_FACTORY_API_KEY=` the same key as in your local `.env`
- `TAVILY_API_KEY=` the same key as in your local `.env` (turns on datasheet search for judges)

This file is git-ignored. Do not paste its contents anywhere.

### 4. Deploy

```
powershell -ExecutionPolicy Bypass -File deploy\cloudrun\deploy.ps1
```

The script prints each stage. The first run takes 5 to 10 minutes, mostly the build. At the end it prints `Demo URL:` and checks `/api/v1/ready` (database reachable, migrations applied). Run it again any time to deploy new code; it keeps secrets that have not changed.

### 5. Check the demo like a judge

1. Open the URL in a private window. Sign up (no email step), open **Example: YOLOv8s on a robot**, press **Analyse**, and walk the six steps.
2. Press **Ask the assistant** and ask "Why does it fail today?".
3. Under **Try changes**, press **Ask the AI for candidates →**.
4. Run the automatic check:

```
backend\.venv\Scripts\python scripts\acceptance.py https://YOUR-URL
```

5. Wait 20 minutes without visiting, then open the URL again. It should still load in a second or two (this proves the keep-warm job works).

Tell me the URL and I will fill in the `[DEMO URL]` placeholders in the docs.

### 6. Protect yourself from surprise bills

1. [console.cloud.google.com/billing/budgets](https://console.cloud.google.com/billing/budgets) > **Create budget**, limited to the `green-computer-demo` project, amount **$1**, alerts at 50% and 100%.
2. The deploy already caps the service at 3 instances.

### 7. After judging (15 December)

Delete the project (**IAM & Admin > Settings > Shut down**) and the Neon project. Everything stops, including the scheduler job.

## Troubleshooting

| What you see | What to do |
|---|---|
| `Billing is not linked` | Step 2.2, then run the script again. |
| Build fails with `permission denied` or `does not have permission to access ... storage` | In IAM, give the **Compute Engine default service account** (`<number>-compute@developer.gserviceaccount.com`) the roles *Cloud Build Service Account*, *Storage Object Viewer* and *Artifact Registry Writer*, wait a minute, run again. |
| Build fails inside the Dockerfile | Read the log line it prints (`gcloud builds log <id>`). The Dockerfile has never been built before, so send me the last 30 lines. |
| `/api/v1/ready` times out or answers 503 | `gcloud run services logs read green-computer --project <id> --region us-east1 --limit 50`. Usually the Neon string: copy it again with pooling off. |
| "Organization policy ... allUsers" when deploying | A Google Workspace organisation is blocking public services. Use a personal Google account for the project. |
| Agent says it is not configured | `TOKEN_FACTORY_API_KEY` was empty in `.env.cloudrun`. Fill it and run the script again. |
| The first visit after a quiet hour is slow | Check the job: `gcloud scheduler jobs list --project <id> --location us-east1`, and run it by hand: `gcloud scheduler jobs run green-computer-keepwarm --project <id> --location us-east1`. |
