# Home Assistant Brands Submission

Guide for submitting this integration to the [Home Assistant Brands](https://github.com/home-assistant/brands) repository.

## What is Brands?

The HA Brands repository contains:
- Integration logos/icons
- Brand metadata (name, IoT class, etc.)
- Used by HA frontend for displaying integration cards

## Requirements

### 1. Logo Files
Create the following files in the Brands repo:

**Path**: `brands/custom_integrations/tuya_ble_mesh/`

Files needed:
- `icon.png` - 256x256px, transparent background
- `icon@2x.png` - 512x512px, transparent background (optional but recommended)
- `logo.png` - Any size, full logo with background
- `logo@2x.png` - 2x resolution of logo.png

**Logo Guidelines:**
- Use official Tuya branding (if allowed)
- Or create custom icon representing BLE Mesh
- Transparent background for icon files
- PNG format
- Optimized file size (<100KB)

### 2. Metadata File

Create `manifest.json` in the same directory:

```json
{
  "domain": "tuya_ble_mesh",
  "name": "Tuya BLE Mesh",
  "integration_type": "device",
  "iot_class": "local_polling",
  "supported_brands": [
    "tuya"
  ]
}
```

### 3. Submission Process

#### Step 1: Fork the Brands Repository
```bash
git clone https://github.com/YOUR_USERNAME/brands.git
cd brands
```

#### Step 2: Create Brand Directory
```bash
mkdir -p custom_integrations/tuya_ble_mesh
cd custom_integrations/tuya_ble_mesh
```

#### Step 3: Add Logo Files
- Copy icon.png (256x256)
- Copy icon@2x.png (512x512)
- Copy logo.png
- Copy logo@2x.png (optional)

#### Step 4: Create Manifest
Create `manifest.json` with metadata above.

#### Step 5: Validate
```bash
# Run validation script (if available)
python scripts/validate.py custom_integrations/tuya_ble_mesh
```

#### Step 6: Create Pull Request
```bash
git checkout -b add-tuya-ble-mesh-brand
git add custom_integrations/tuya_ble_mesh/
git commit -m "Add Tuya BLE Mesh integration brand"
git push origin add-tuya-ble-mesh-brand
```

Create PR with:
- Title: "Add Tuya BLE Mesh integration"
- Description: Link to integration repo
- Follow PR template

### 4. Metadata Fields

| Field | Value | Description |
|-------|-------|-------------|
| `domain` | `tuya_ble_mesh` | Integration domain |
| `name` | `Tuya BLE Mesh` | Display name |
| `integration_type` | `device` | Type of integration |
| `iot_class` | `local_polling` | IoT classification |
| `supported_brands` | `["tuya"]` | Supported brands |

#### IoT Class Options
- `local_polling` - Local polling (our choice)
- `local_push` - Local push
- `cloud_polling` - Cloud polling
- `cloud_push` - Cloud push

#### Integration Type Options
- `device` - Device integration (our choice)
- `service` - Service integration
- `helper` - Helper integration

## Logo Design

If creating a custom logo:

### Icon Design Ideas
- BLE symbol + Mesh network
- Smart bulb + Bluetooth icon
- Tuya logo + BLE mesh pattern

### Tools
- [GIMP](https://www.gimp.org/) - Free image editor
- [Inkscape](https://inkscape.org/) - Vector graphics
- [Figma](https://www.figma.com/) - Online design tool

### References
- [Tuya Branding](https://www.tuya.com/) - Official Tuya logos
- [Bluetooth Brand Guide](https://www.bluetooth.com/develop-with-bluetooth/marketing-branding/)
- [Material Icons](https://fonts.google.com/icons) - Icon inspiration

## After Submission

Once PR is merged:
1. Logo appears in HA frontend
2. Integration card shows brand icon
3. Users see professional branding

## Notes

- Brands submission is OPTIONAL but recommended
- Integration works without brands entry
- Improves user experience and discoverability
- May take time for PR review and merge

## Resources

- [HA Brands Repo](https://github.com/home-assistant/brands)
- [Brand Guidelines](https://github.com/home-assistant/brands/blob/master/README.md)
- [Integration Documentation](https://developers.home-assistant.io/docs/creating_integration_manifest)
