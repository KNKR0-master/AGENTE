# Errors

Command failures and integration errors.

---

## [ERR-20260709-006] parallel-tempdir-race

**Logged**: 2026-07-09T17:20:43-03:00
**Priority**: low
**Status**: pending
**Area**: infra

### Summary
Parallel tool calls failed because follow-up commands read a temporary directory before the command that created it had completed.

### Error
```text
rg: ...\clawhub-word-docx-vet: IO error ... O sistema não pode encontrar o arquivo especificado.
Get-Content : Não é possível localizar o caminho ...\clawhub-word-docx-vet\SKILL.md porque ele não existe.
```

### Context
- Occurred during ClawHub skill vetting.
- The temp directory creation/download command was launched in parallel with commands that depended on that directory.
- A follow-up attempt to initialize logs also failed because a PowerShell here-string was placed after other tokens on the same line.

### Suggested Fix
Do not parallelize dependent filesystem steps. Create/download temp artifacts first, then run parallel reads/scans. For multiline text edits, prefer `apply_patch` over fragile one-line PowerShell here-strings.

### Metadata
- Reproducible: yes
- Related Files: .learnings/ERRORS.md
- Pattern-Key: fs.no-such-file
- Recurrence-Count: 1
- First-Seen: 2026-07-09
- Last-Seen: 2026-07-09

---

## [ERR-20260709-002] npm-powershell-execution-policy

**Logged**: 2026-07-09T16:20:00-03:00
**Priority**: low
**Status**: pending
**Area**: config

### Summary
PowerShell bloqueia `npm.ps1` pela política de execução, mas `npm.cmd` pode ser usado como alternativa.

### Error
```text
npm.ps1 não pode ser carregado porque a execução de scripts foi desabilitada neste sistema.
```

### Context
- Ocorreu ao consultar pacotes npm durante instalação/auditoria de skill do ClawHub.
- Ambiente: Windows PowerShell.

### Suggested Fix
Usar `npm.cmd` ou `npx.cmd` em vez de `npm`/`npx` em comandos PowerShell neste ambiente.

### Metadata
- Reproducible: yes
- Related Files: N/A
- Pattern-Key: shell.execution-policy
- Recurrence-Count: 1
- First-Seen: 2026-07-09
- Last-Seen: 2026-07-09

---

## [ERR-20260709-001] install-skill-from-github

**Logged**: 2026-07-09T00:00:00-03:00
**Priority**: medium
**Status**: resolved
**Area**: infra

### Summary
The Codex skill installer fell back to Git over SSH for a public repository and failed host-key verification.

### Error
```
Host key verification failed.
fatal: Could not read from remote repository.
```

### Context
- Attempted to install `skills/steipete/gog` from the public `openclaw/skills` repository.
- Direct download did not complete and the automatic Git fallback selected SSH.

### Suggested Fix
Retry with an explicit public HTTPS GitHub URL or download the reviewed skill file over HTTPS.

### Metadata
- Reproducible: unknown
- Related Files: C:\Users\TTHome\.codex\skills\.system\skill-installer\scripts\install-skill-from-github.py
- Pattern-Key: vcs.fatal-error
- Recurrence-Count: 2
- First-Seen: 2026-07-09
- Last-Seen: 2026-07-09

### Resolution
- **Resolved**: 2026-07-09T00:00:00-03:00
- **Notes**: Installed the reviewed registry artifact directly with the official ClawHub CLI and verified its published SHA-256.

---

## [ERR-20260709-002] gog-config-key

**Logged**: 2026-07-09T00:00:00-03:00
**Priority**: low
**Status**: resolved
**Area**: config

### Summary
The current gogcli version renamed the documented `default_timezone` configuration key to `timezone`.

### Error
```
unknown config key: default_timezone
```

### Context
- The installed gogcli version is 0.33.0.
- The current CLI reports `timezone` as the supported key.

### Suggested Fix
Use `gog config set timezone <IANA timezone>`.

### Metadata
- Reproducible: yes
- Pattern-Key: config.invalid-json
- Recurrence-Count: 1
- First-Seen: 2026-07-09
- Last-Seen: 2026-07-09

### Resolution
- **Resolved**: 2026-07-09T00:00:00-03:00
- **Notes**: Switched to the supported `timezone` key.

---

## [ERR-20260709-003] in-app-browser-google-cloud

**Logged**: 2026-07-09T00:00:00-03:00
**Priority**: low
**Status**: resolved
**Area**: infra

### Summary
The first DOM snapshot of Google Cloud failed because an incremental accessibility API was unavailable.

### Error
```
incrementalAriaSnapshot is not a function
```

### Context
- Occurred after navigating the in-app browser to Google Cloud credentials.
- No form data had been entered.

### Suggested Fix
Reconnect to the in-app browser, obtain a fresh tab, and use screenshot-based orientation.

### Metadata
- Reproducible: unknown
- Pattern-Key: runtime.type-error
- Recurrence-Count: 4
- First-Seen: 2026-07-09
- Last-Seen: 2026-07-09

### Resolution
- **Resolved**: 2026-07-09T00:00:00-03:00
- **Notes**: Reconnected successfully after Google Cloud DOM, interstitial-navigation, and tab-load failures; fresh tabs plus visible-DOM controls completed the workflow.

---

## [ERR-20260709-004] gog-remote-oauth

**Logged**: 2026-07-09T00:00:00-03:00
**Priority**: low
**Status**: resolved
**Area**: config

### Summary
The gog remote OAuth exchange rejected the callback because step 2 did not repeat the `--force-consent` option used in step 1.

### Error
```
manual auth state mismatch; start a new manual flow or run remote step 1 again
```

### Context
- Remote OAuth step 1 used `--force-consent`.
- Step 2 initially omitted that option, so gog rejected the pending state as mismatched.
- No authorization codes or tokens are stored in this log.

### Suggested Fix
Repeat all state-defining OAuth options, including `--force-consent`, in remote step 2.

### Metadata
- Reproducible: yes
- Pattern-Key: auth.missing-scope
- Recurrence-Count: 2
- First-Seen: 2026-07-09
- Last-Seen: 2026-07-09

### Resolution
- **Resolved**: 2026-07-09T00:00:00-03:00
- **Notes**: Re-ran remote step 2 with `--force-consent`; the refresh token was stored successfully.

---

## [ERR-20260709-005] oauth-json-download

**Logged**: 2026-07-09T00:00:00-03:00
**Priority**: low
**Status**: resolved
**Area**: infra

### Summary
The in-app browser did not expose the Google OAuth JSON download event or save the file to the user Downloads folder.

### Error
```
Timed out waiting for download.
```

### Context
- The OAuth desktop client had already been created successfully.
- The browser download button was present, but no local JSON artifact appeared.
- No credential values are stored in this log.

### Suggested Fix
Read the credential fields from the authenticated page in memory and pipe a standard installed-app JSON document directly to `gog auth credentials set -`.

### Metadata
- Reproducible: unknown
- Pattern-Key: runtime.type-error
- Recurrence-Count: 1
- First-Seen: 2026-07-09
- Last-Seen: 2026-07-09

### Resolution
- **Resolved**: 2026-07-09T00:00:00-03:00
- **Notes**: Stored the OAuth client directly through stdin; gog placed the client secret in the OS keyring.

---
