# TODO — Tuya BLE Mesh Project

## 1Password Setup (manual steps required)

OP CLI is installed (v2.32.1) but `OP_SERVICE_ACCOUNT_TOKEN` is NOT SET.

### Steps for human operator:

1. **Create vault** "malmbergs-bt" in 1Password
   - Or run: `bash scripts/setup-1password-vault.sh` (interactive)

2. **Create items** in the vault:
   | Item             | Type           | Status  |
   |------------------|----------------|---------|
   | `anthropic-api`  | API Credential | NEEDED  |
   | `nas-samba`      | Login          | NEEDED  |
   | `gitea`          | API Credential | NEEDED  |

3. **Create Service Account** in 1Password web UI
   - Grant access to `malmbergs-bt` vault
   - Copy the service account token

4. **Set token in tmux session** before starting Claude Code:
   ```bash
   export OP_SERVICE_ACCOUNT_TOKEN='ops_your_token_here'
   ```

5. **Verify** (safe commands — no secrets shown):
   ```bash
   op vault get malmbergs-bt > /dev/null 2>&1 && echo "VAULT: OK" || echo "VAULT: MISSING"
   op read "op://malmbergs-bt/anthropic-api/credential" > /dev/null 2>&1 && echo "API KEY: OK" || echo "API KEY: MISSING"
   ```

### Shelly Plug Authentication

The Shelly Plug S at 192.168.1.50 reports `auth: false`.
No 1Password item needed for Shelly credentials at this time.
If auth is enabled later, create a `shelly-plug` item in 1Password.

## NAS Mount

- NAS share: `//192.168.5.220/z-solutions`
- Mount point: `/mnt/solutions`
- Protocol: CIFS/SMB 3.0, sec=none, uid=1000, gid=1000
- Status: NOT MOUNTED (needs systemd automount configuration)
