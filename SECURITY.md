# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.17.x  | Yes       |
| < 0.17  | No        |

## Security Model

### What we protect
- **Mesh credentials**: Never logged, stored encrypted in HA credential store
- **Provisioning keys**: Generated fresh per-device, stored in HA config entry
- **Network keys**: Generated with `os.urandom(16)` (cryptographically secure)

### Known Security Limitations

These are **protocol-level constraints** outside our control:

| Limitation | Impact | Mitigation |
|------------|--------|------------|
| Telink 2-byte MAC authentication | Weak per-device auth (16-bit) | Use strong mesh passwords |
| Fixed mesh credentials → session key | Credential rotation requires re-pairing | Network isolation recommended |
| BLE range ≈ 10-30m | Physical proximity required to attack | Apartment/building isolation |
| Bridge HTTP unencrypted | LAN traffic visible | Isolated VLAN for bridge host |
| SIG Mesh IV index (32-bit) | Long-lived network key | Rotate keys periodically |

### SSRF Protection

The config flow validates bridge hosts to prevent SSRF attacks:
- Rejects URLs (containing `://`)
- Rejects path traversal (`/`, `\\`)
- Rejects loopback IPs (`127.x.x.x`, `::1`)
- Rejects link-local IPs (`169.254.x.x`, `fe80::/10`)
- Rejects hex-encoded IPs (`0x7f000001`)

### No Secret Leakage

All mesh credentials are:
- **Never logged** (even at DEBUG level)
- **Redacted in diagnostics** (`**REDACTED**`)
- **Not exposed in entity state attributes**
- **Not stored in `/tmp`** — keys stay in HA config entry

## Reporting a Vulnerability

Please report security vulnerabilities via **GitHub Security Advisories**:

1. Go to the [repository security page](https://github.com/11z4t/tuya-ble-mesh/security)
2. Click **"Report a vulnerability"**
3. Provide: description, reproduction steps, impact assessment

**Do not** open public GitHub issues for security vulnerabilities.

### Response Timeline

- **Acknowledgment**: within 48 hours
- **Initial assessment**: within 7 days
- **Fix or mitigation**: within 30 days for critical issues

### Responsible Disclosure

We follow coordinated disclosure. We will:
1. Confirm the vulnerability
2. Develop a fix
3. Release the fix with a security advisory
4. Credit the reporter (unless they prefer anonymity)

## CVE Process

If a CVE is warranted, we will request one via MITRE and include it in the
release notes and CHANGELOG.
