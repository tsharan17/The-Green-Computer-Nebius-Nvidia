# Privacy Policy

**Draft for professional legal review.** This policy describes what this software stores and sends
as it is built. The operator of the server must review it, add their identity and contact details,
and confirm it matches their configuration.

## 1. What is stored

| Data | Why | Where |
|---|---|---|
| Email address, optional display name | Your account and sign-in | The server's database |
| Password | Sign-in. Stored only as a salted scrypt hash, never in plain text | Database |
| Sessions | Keep you signed in. The cookie holds a random token; only its SHA-256 hash is stored, with the browser's user agent and IP address | Database |
| Optional two-factor secret and recovery codes | Two-factor sign-in. The secret is encrypted with the server key; recovery codes are stored as hashes | Database |
| Projects, requirements, analyses, plans, measurements and exports | The features you use | Database and the server's file store |
| Uploaded files (models, datasheets, board files, RTL, logs) | Validation and analysis | The server's file store, inside your workspace |
| Terms acceptance (document, version, time, account id) | A record of what you agreed to | Database |
| Audit log (security-relevant actions, with IP address) and usage counts | Security, abuse prevention and your usage page | Database |

## 2. What is sent outside the server, and when

By default **nothing** leaves the server. Each service below is used only if the operator has
configured it **and** you use the feature that needs it.

| Service | Feature | What is sent |
|---|---|---|
| Nebius Token Factory (NVIDIA Nemotron models) | AI assistant chat | Your question, the conversation so far, and the engine's results for the project (model and device names, settings, numbers) |
| Nebius Token Factory | "Ask the AI for candidates" on Recommendations | The engine's summary only: model and device names, today's settings and metrics, failing requirements, your requirements and workload, and the ids of changes the engine can evaluate. No file contents |
| Nebius Token Factory | Reading an uploaded datasheet PDF | Up to 5,000 characters of the PDF's text, to extract hardware values that you then confirm |
| Tavily | Datasheet search by device name | The device name you type |
| Nebius (GPU jobs) | Running a benchmark on a cloud GPU | The model file and benchmark settings for that run |
| Email provider (SMTP) | Verification and password-reset emails | Your email address and the message |
| Error reporting (webhook or Sentry) | Server errors | Error type, route and request id; no request bodies, passwords or tokens |
| Telemetry | Off by default; only after the operator enables it | Aggregate counts; no personal data or file contents |

Uploaded documents are treated as data. Instructions written inside a file are never followed.

## 3. How long data is kept

Your data is kept until you delete it, unless the operator sets retention rules for your workspace.
Backups made by the operator may keep deleted data until those backups expire.

## 4. Your choices

- **Delete a project's data:** archive the project, or delete data on the workspace data page.
- **Delete your account:** Account → Delete account. Workspaces you own alone are deleted with all
  their projects and files; in shared workspaces your name is removed from records.
- **Export your data:** each project has exports with a hashed manifest.

## 5. Security

Passwords are hashed, sessions are HTTP-only cookies with CSRF protection, sign-in is rate limited,
two-factor authentication is available, and every project, file and export is checked against your
workspace on the server. No system is perfectly secure; the operator's security notes list the
known limits.

## 6. Changes

When this policy changes materially, its version changes and you are asked to accept it at your
next sign-in.
