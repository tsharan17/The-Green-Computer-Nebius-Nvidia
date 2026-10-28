<#
.SYNOPSIS
  Deploy The Green Computer to Google Cloud Run (free tier) with a Neon PostgreSQL database.

.DESCRIPTION
  Reads deploy\cloudrun\.env.cloudrun (copy it from env.example), then:
    1. checks gcloud, the project and its billing link
    2. enables the Google Cloud services it needs
    3. creates a service account and stores the secrets in Secret Manager (never in the image or in flags)
    4. builds the image from the Dockerfile in Cloud Build and deploys it as a public Cloud Run service
    5. creates a Cloud Scheduler job that pings /api/v1/health every 5 minutes so the service stays warm
    6. checks that the new service answers
  Safe to run again: it updates what exists and keeps secrets that have not changed.

  Run from the repository root:
    powershell -ExecutionPolicy Bypass -File deploy\cloudrun\deploy.ps1

  STATUS: never run. Written from the gcloud documentation. Read deploy\cloudrun\README.md first.
#>
param(
    [string]$EnvFile = (Join-Path $PSScriptRoot ".env.cloudrun")
)

$ErrorActionPreference = "Continue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

function Say([string]$m) { Write-Host ""; Write-Host "== $m" -ForegroundColor Green }
function Warn([string]$m) { Write-Host "  warning: $m" -ForegroundColor Yellow }
function Fail([string]$m) { Write-Host ""; Write-Host "ERROR: $m" -ForegroundColor Red; exit 1 }

# Run gcloud and stop with a clear message if it fails. Output goes to the screen, not to the caller.
function Invoke-Gcloud {
    & gcloud.cmd @args | Out-Host
    if ($LASTEXITCODE -ne 0) { Fail ("gcloud " + ($args -join " ") + " failed (exit " + $LASTEXITCODE + ")") }
}

# Run gcloud and return its output as one trimmed string.
function GcOut {
    $out = & gcloud.cmd @args
    if ($LASTEXITCODE -ne 0) { Fail ("gcloud " + ($args -join " ") + " failed (exit " + $LASTEXITCODE + ")") }
    return ($out | Out-String).Trim()
}

function Names([string]$text) {
    return @($text -split "`n" | ForEach-Object { ($_ -split "/")[-1].Trim() } | Where-Object { $_ -ne "" })
}

function New-RandomKey {
    $b = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($b)
    return (($b | ForEach-Object { $_.ToString("x2") }) -join "")
}

# --- 0. read the settings ------------------------------------------------------------------------
if (-not (Test-Path $EnvFile)) {
    Fail "Missing $EnvFile. Copy deploy\cloudrun\env.example to deploy\cloudrun\.env.cloudrun and fill it in."
}
$cfg = @{}
foreach ($raw in (Get-Content $EnvFile)) {
    $line = $raw.Trim()
    if ($line -eq "" -or $line.StartsWith("#") -or -not $line.Contains("=")) { continue }
    $i = $line.IndexOf("=")
    $cfg[$line.Substring(0, $i).Trim()] = $line.Substring($i + 1).Trim().Trim('"').Trim("'")
}
function Cfg([string]$key, [string]$default = "") {
    if ($cfg.ContainsKey($key) -and $cfg[$key] -ne "") { return $cfg[$key] }
    return $default
}

$Project = Cfg "PROJECT_ID"
$Region = Cfg "REGION" "us-east1"
$Service = Cfg "SERVICE" "green-computer"
$db = Cfg "DATABASE_URL"
$apiKey = Cfg "TOKEN_FACTORY_API_KEY"
$tavilyKey = Cfg "TAVILY_API_KEY"

if ($Project -eq "" -or $Project.StartsWith("your-")) { Fail "Set PROJECT_ID in $EnvFile (the project ID, not its name)." }
if ($db -eq "" -or $db.Contains("USER:PASSWORD")) { Fail "Set DATABASE_URL in $EnvFile (the Neon connection string)." }
if ($db -match '^postgres(ql)?://') { $db = $db -replace '^postgres(ql)?://', 'postgresql+psycopg://' }
if ($db -notmatch '^postgresql\+psycopg://') { Fail "DATABASE_URL must start with postgresql:// (copy it from Neon's Connect dialog)." }
if ($db -notmatch 'sslmode=') {
    if ($db.Contains("?")) { $db = $db + "&sslmode=require" } else { $db = $db + "?sslmode=require" }
}
if ($db -match '-pooler') { Warn "This looks like Neon's pooled address. Turn 'Connection pooling' off in Neon and copy the direct string." }
if ($apiKey -eq "") { Warn "No TOKEN_FACTORY_API_KEY: the Green Agent and AI candidates will be off in the demo." }
if ($tavilyKey -eq "") { Warn "No TAVILY_API_KEY: datasheet search will be off in the demo (uploading a PDF datasheet still works)." }

# --- 1. gcloud, project, billing -------------------------------------------------------------------
Say "Checking gcloud and project $Project"
$account = GcOut auth list "--filter=status:ACTIVE" "--format=value(account)"
if ($account -eq "") { Fail "Not signed in. Run: gcloud auth login" }
Write-Host "  signed in as $account"
$number = GcOut projects describe $Project "--format=value(projectNumber)"
$billing = (& gcloud billing projects describe $Project "--format=value(billingEnabled)" | Out-String).Trim()
if ($LASTEXITCODE -eq 0 -and $billing -ne "True") {
    Fail "Billing is not linked to $Project. Link it at https://console.cloud.google.com/billing/linkedaccount?project=$Project and run this again."
}

# --- 2. services -----------------------------------------------------------------------------------
Say "Enabling Google Cloud services (a minute or two the first time)"
Invoke-Gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com cloudscheduler.googleapis.com compute.googleapis.com --project $Project

# --- 3. service account, permissions, secrets ----------------------------------------------------------
Say "Service account and permissions"
$saName = "green-computer-run"
$saEmail = "$saName@$Project.iam.gserviceaccount.com"
$haveSa = GcOut iam service-accounts list --project $Project "--format=value(email)"
if ($haveSa -notmatch [regex]::Escape($saEmail)) {
    Invoke-Gcloud iam service-accounts create $saName --project $Project "--display-name=Green Computer on Cloud Run"
}
$bound = $false
for ($try = 1; $try -le 4 -and -not $bound; $try++) {
    & gcloud projects add-iam-policy-binding $Project "--member=serviceAccount:$saEmail" --role=roles/secretmanager.secretAccessor --condition=None --quiet | Out-Null
    if ($LASTEXITCODE -eq 0) { $bound = $true } else { Start-Sleep -Seconds 8 }
}
if (-not $bound) { Fail "Could not let $saEmail read secrets." }
# Builds from source run as the default Compute service account in new projects; it needs the build role.
$compute = "$number-compute@developer.gserviceaccount.com"
& gcloud projects add-iam-policy-binding $Project "--member=serviceAccount:$compute" --role=roles/cloudbuild.builds.builder --condition=None --quiet | Out-Null
if ($LASTEXITCODE -ne 0) { Warn "Could not grant the build role to $compute. If the build is refused, see Troubleshooting in README.md." }

Say "Secrets (stored in Secret Manager, never printed)"
$existing = Names (GcOut secrets list --project $Project "--format=value(name)")
function Set-Secret([string]$name, [string]$value, [bool]$generate) {
    $exists = $existing -contains $name
    if ($value -eq "") {
        if ($exists) { Write-Host "  ${name}: kept"; return $true }
        if (-not $generate) { return $false }
        $value = New-RandomKey
        Write-Host "  ${name}: generated"
    }
    $tmp = [IO.Path]::GetTempFileName()
    try {
        [IO.File]::WriteAllText($tmp, $value, (New-Object Text.UTF8Encoding($false)))
        if (-not $exists) {
            Invoke-Gcloud secrets create $name --project $Project --replication-policy=automatic "--data-file=$tmp"
            $script:existing += $name
        } else {
            $current = (& gcloud secrets versions access latest "--secret=$name" --project $Project | Out-String).Trim()
            if ($LASTEXITCODE -eq 0 -and $current -eq $value.Trim()) { Write-Host "  ${name}: unchanged" }
            else { Invoke-Gcloud secrets versions add $name --project $Project "--data-file=$tmp" }
        }
    } finally {
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    }
    return $true
}
[void](Set-Secret "gc-db-url" $db $false)
[void](Set-Secret "gc-secret-key" (Cfg "GC_SECRET_KEY") $true)
[void](Set-Secret "gc-agent-signing-secret" (Cfg "GC_AGENT_SIGNING_SECRET") $true)
$haveKey = Set-Secret "gc-token-factory-key" $apiKey $false
$haveTavily = Set-Secret "gc-tavily-key" $tavilyKey $false

# --- 4. build and deploy -----------------------------------------------------------------------------
$publicUrl = "https://$Service-$number.$Region.run.app"
$envVars = @(
    "GC_ENV=production", "GC_PUBLIC_DEMO=1", "GC_TRUST_PROXY=1", "GC_ALLOW_SIGNUP=1",
    "GC_REQUIRE_EMAIL_VERIFICATION=0", "GC_AUTO_MIGRATE=1", "GC_PUBLIC_URL=$publicUrl"
)
foreach ($k in "GC_MODEL_ULTRA", "GC_MODEL_SUPER", "GC_MODEL_NANO", "TOKEN_FACTORY_BASE_URL") {
    if ((Cfg $k) -ne "") { $envVars += ($k + "=" + (Cfg $k)) }
}
$secrets = @("GC_DB_URL=gc-db-url:latest", "GC_SECRET_KEY=gc-secret-key:latest", "GC_AGENT_SIGNING_SECRET=gc-agent-signing-secret:latest")
if ($haveKey) { $secrets += "TOKEN_FACTORY_API_KEY=gc-token-factory-key:latest" }
if ($haveTavily) { $secrets += "TAVILY_API_KEY=gc-tavily-key:latest" }

Say "Building the image and deploying $Service to $Region (5 to 10 minutes the first time)"
Invoke-Gcloud run deploy $Service --project $Project --region $Region --source $RepoRoot --quiet `
    --allow-unauthenticated --port 8000 --cpu 2 --memory 2Gi --cpu-boost `
    --min-instances 0 --max-instances 3 --concurrency 20 --timeout 300 `
    "--service-account=$saEmail" ("--set-env-vars=" + ($envVars -join ",")) ("--set-secrets=" + ($secrets -join ","))

$url = GcOut run services describe $Service --project $Project --region $Region "--format=value(status.url)"
if ($url -ne $publicUrl) {
    Say "Setting GC_PUBLIC_URL to the address Cloud Run gave"
    Invoke-Gcloud run services update $Service --project $Project --region $Region --quiet "--update-env-vars=GC_PUBLIC_URL=$url"
}

# --- 5. keep it warm -----------------------------------------------------------------------------------
Say "Keep-warm job (Cloud Scheduler pings /api/v1/health every 5 minutes; it does not touch the database)"
$jobName = "$Service-keepwarm"
$jobs = Names (GcOut scheduler jobs list --project $Project --location $Region "--format=value(name)")
$verb = "create"
if ($jobs -contains $jobName) { $verb = "update" }
Invoke-Gcloud scheduler jobs $verb http $jobName --project $Project --location $Region "--schedule=*/5 * * * *" "--uri=$url/api/v1/health" --http-method=GET --attempt-deadline=60s --time-zone=Etc/UTC "--description=Keeps the demo warm"

# --- 6. check it -----------------------------------------------------------------------------------------
Say "Checking $url"
$ok = $false
for ($i = 1; $i -le 12 -and -not $ok; $i++) {
    try {
        $r = Invoke-WebRequest "$url/api/v1/ready" -UseBasicParsing -TimeoutSec 60
        if ($r.StatusCode -eq 200) { $ok = $true }
    } catch {
        Start-Sleep -Seconds 10
    }
}
if ($ok) { Write-Host "  ready: the database answers and the schema is up to date" -ForegroundColor Green }
else { Warn "The service did not answer /api/v1/ready in two minutes. Check the logs: gcloud run services logs read $Service --project $Project --region $Region --limit 50" }

Write-Host ""
Write-Host "Demo URL: $url" -ForegroundColor Green
Write-Host "Next: open it, sign up, open the example project and press Analyse. Then:"
Write-Host "  backend\.venv\Scripts\python scripts\acceptance.py $url"
