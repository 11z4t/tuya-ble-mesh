# Home Assistant Brands Submission Guide

This document outlines the process for submitting the Tuya BLE Mesh integration to the official [Home Assistant Brands](https://github.com/home-assistant/brands) repository.

## Prerequisites

Before submitting, ensure:

- ✅ Integration is stable and well-tested
- ✅ Quality Scale tier achieved (currently: **PLATINUM**)
- ✅ Documentation is complete and accurate
- ✅ Code follows HA development standards
- ✅ Integration is published and accessible (GitHub/HACS)

## What to Submit

### 1. Brand Assets

Create the following assets in the `brands` repository under `custom_integrations/tuya_ble_mesh/`:

#### Logo Files
- **`logo.png`** - 256x256px, transparent background, PNG format
- **`logo@2x.png`** - 512x512px, transparent background, PNG format (optional)
- **`icon.png`** - 256x256px, transparent background, PNG format
- **`icon@2x.png`** - 512x512px, transparent background, PNG format (optional)

**Design Guidelines:**
- Simple, recognizable design
- Works well on light and dark backgrounds
- Represents BLE mesh or Tuya branding
- Follow HA visual style

#### Brand Metadata (`manifest.json`)

```json
{
  "domain": "tuya_ble_mesh",
  "name": "Tuya BLE Mesh",
  "codeowners": ["@11z4t"],
  "documentation": "https://github.com/4recon/tuya-ble-mesh",
  "integration_type": "device",
  "iot_class": "local_polling",
  "quality_scale": "platinum"
}
```

### 2. Integration Core Submission

For official HA inclusion (optional, separate process):

1. **Fork [home-assistant/core](https://github.com/home-assistant/core)**
2. **Add integration** under `homeassistant/components/tuya_ble_mesh/`
3. **Submit PR** following HA contribution guidelines
4. **Pass CI/CD** checks and code review

## Submission Process

### Step 1: Prepare Assets

```bash
# Create directory structure
mkdir -p brands_submission/custom_integrations/tuya_ble_mesh

# Add logo and icon files
cp assets/logo.png brands_submission/custom_integrations/tuya_ble_mesh/
cp assets/icon.png brands_submission/custom_integrations/tuya_ble_mesh/

# Create manifest.json
cat > brands_submission/custom_integrations/tuya_ble_mesh/manifest.json << 'EOF'
{
  "domain": "tuya_ble_mesh",
  "name": "Tuya BLE Mesh",
  "codeowners": ["@11z4t"],
  "documentation": "https://github.com/4recon/tuya-ble-mesh",
  "integration_type": "device",
  "iot_class": "local_polling",
  "quality_scale": "platinum"
}
EOF
```

### Step 2: Fork and Clone Brands Repo

```bash
# Fork https://github.com/home-assistant/brands on GitHub

# Clone your fork
git clone https://github.com/YOUR_USERNAME/brands.git
cd brands

# Create feature branch
git checkout -b add-tuya-ble-mesh
```

### Step 3: Add Assets

```bash
# Copy files to correct location
mkdir -p custom_integrations/tuya_ble_mesh
cp ../brands_submission/custom_integrations/tuya_ble_mesh/* custom_integrations/tuya_ble_mesh/

# Verify file structure
tree custom_integrations/tuya_ble_mesh
# Should show:
# custom_integrations/tuya_ble_mesh/
# ├── icon.png
# ├── logo.png
# └── manifest.json
```

### Step 4: Validate Submission

```bash
# Run brands validation script (if available)
python -m script.validate

# Check image dimensions
file custom_integrations/tuya_ble_mesh/*.png
# Should show 256x256 or 512x512

# Validate manifest.json
jq . custom_integrations/tuya_ble_mesh/manifest.json
```

### Step 5: Create Pull Request

```bash
# Commit changes
git add custom_integrations/tuya_ble_mesh
git commit -m "Add Tuya BLE Mesh integration branding"

# Push to your fork
git push origin add-tuya-ble-mesh

# Create PR on GitHub
# Title: "Add Tuya BLE Mesh integration"
# Description: Include quality scale tier, documentation link, brief description
```

### Step 6: PR Review

The HA Brands team will review:
- Asset quality and format
- Manifest accuracy
- Integration maturity
- Documentation completeness

**Response Time:** Typically 1-2 weeks

## After Approval

Once the PR is merged:
1. Assets will be available in HA
2. Integration logo will appear in UI
3. Update README with "Official HA Branding" badge

## Troubleshooting

**PR rejected due to image quality**
- Ensure PNG format, not JPEG
- Check transparency (no white background)
- Verify dimensions (256x256 or 512x512)
- Use vector source for scaling

**Manifest validation fails**
- Check JSON syntax
- Verify all required fields
- Match `domain` with integration name
- Ensure `codeowners` exist

**Integration not mature enough**
- Achieve at least Silver quality tier
- Add comprehensive documentation
- Ensure stability over multiple releases
- Build community adoption

## Resources

- [HA Brands Repository](https://github.com/home-assistant/brands)
- [Brands Contribution Guide](https://github.com/home-assistant/brands/blob/master/CONTRIBUTING.md)
- [HA Developer Docs](https://developers.home-assistant.io/)
- [Quality Scale Index](https://developers.home-assistant.io/docs/integration_quality_scale_index)

## Current Status

- **Quality Scale**: PLATINUM ⭐⭐⭐⭐
- **Documentation**: Complete
- **Testing**: Comprehensive (unit, integration, E2E, a11y)
- **Community**: Guidelines and templates ready
- **Ready for Submission**: ✅ YES

## Next Steps

1. Design logo/icon assets (or use Tuya branding if licensed)
2. Fork and clone brands repository
3. Add assets and manifest
4. Submit PR
5. Respond to reviewer feedback
6. Celebrate official inclusion! 🎉
