# Phase 4: Hårdvaruvalidering — Instruktion för Börje

**Ansvarig:** Börje Malmgren
**Datum:** 2026-03-02
**Status:** Redo att köra
**Förutsättning:** Phase 3 klar (495 tester, 7/7 checks)

---

## Snabbstart

```bash
source ~/malmbergs-ble/bin/activate && cd ~/malmbergs-bt

# 1. Verifiera att enheten hittas
python scripts/scan.py

# 2. Kör full hårdvaruvalidering
python scripts/hw_validate.py --mac DC:23:4D:21:43:A5

# 3. Kör pytest-baserade hårdvarutester (valfritt, mer detaljerat)
pytest tests/hardware/ -v -s
```

---

## Förberedelser

```
☐ RPi 4 igång, venv aktiverat (source ~/malmbergs-ble/bin/activate)
☐ Malmbergs LED Driver 9952126 strömsatt och inom 3m från RPi
☐ Enheten fabriksåterställd (annonserar som "out_of_mesh")
☐ btmon i separat tmux-fönster: sudo btmon (valfritt, för debugging)
```

### Fabriksåterställning

Om enheten redan är provisionerad (visar "tymesh..."):
```bash
python scripts/factory_reset.py
```
Eller manuellt: slå av/på ström snabbt 3-5 gånger.

---

## Steg-för-steg validering

### Steg 1: BLE Scan

```bash
python scripts/scan.py
```

**Förväntat resultat:**
- Enheten `DC:23:4D:21:43:A5` dyker upp
- Namn: `out_of_mesh` (eller `tymesh...` om redan provisionerad)
- RSSI: bättre än -80 dBm

```
☐ Enhet hittad
☐ RSSI OK
```

### Steg 2: Full validering

```bash
python scripts/hw_validate.py --mac DC:23:4D:21:43:A5
```

Skriptet kör automatiskt: scan → connect → provision → kommandon → disconnect.

**Visuell verifiering (du ser "VERIFY:" i terminalen):**

```
☐ VERIFY: Light should be ON       → Lampa tänds
☐ VERIFY: Light should be DIM      → Synlig dimning (~25%)
☐ VERIFY: Light should be FULL     → Full ljusstyrka
☐ VERIFY: Light should be WARM     → Varm (gulaktig) färgton
☐ VERIFY: Light should be COOL     → Kall (blåaktig) färgton
☐ VERIFY: Light should be OFF      → Lampa släcks
```

### Steg 3: Detaljerade pytest-tester (valfritt)

```bash
# Alla i sekvens
pytest tests/hardware/ -v -s

# Eller individuellt
pytest tests/hardware/test_01_scan.py -v -s
pytest tests/hardware/test_02_connect.py -v -s
pytest tests/hardware/test_03_provision.py -v -s
pytest tests/hardware/test_04_commands.py -v -s
pytest tests/hardware/test_05_reconnect.py -v -s      # kräver Shelly
pytest tests/hardware/test_06_coordinator.py -v -s
```

### Steg 4: Reconnect-test (kräver Shelly Plug S)

```bash
pytest tests/hardware/test_05_reconnect.py -v -s
```

Detta power-cyclar enheten via Shelly (192.168.1.50) och verifierar
att den kan återanslutas efteråt.

```
☐ Lampa tänds före power cycle
☐ Lampa tänds igen efter power cycle
```

---

## Logga resultat

Fyll i mallen i `docs/HARDWARE_TEST_LOG.md` efter varje körning.

---

## Felsökning

| Problem | Lösning |
|---------|---------|
| Enhet hittas inte i scan | Fabriksåterställ, kontrollera ström |
| "Failed to connect" | Enheten kanske redan är ansluten — vänta 30s och försök igen |
| "Provisioning failed" | Fabriksåterställ till `out_of_mesh` |
| Shelly ej nåbar | Kontrollera att 192.168.1.50 svarar: `curl http://192.168.1.50/shelly` |
| BLE adapter nere | `sudo hciconfig hci0 up` |

---

## Nya enheter att testa (när de anländer)

| Art.nr | Enhet | Profil |
|--------|-------|--------|
| 9917072 | Smart Plug | `profiles/9917072_smart_plug.yaml` |
| 9917076 | Dosdimmer | `profiles/9917076_dosdimmer.yaml` |

Kör samma hw_validate.py med `--mac <ny MAC>`.
Profilerna är estimerade — uppdatera efter test.

---

*Genererad av Claude Code, Phase 4 implementation*
*495 unit tests passing, 7/7 validation checks*
