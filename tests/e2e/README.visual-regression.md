# Visual Regression Testing

Visual regression tests capture screenshots of UI components and compare them against baseline images to detect unintended visual changes.

## Running Visual Tests

```bash
# Run all visual regression tests
npm run test:e2e visual-regression

# Update baseline screenshots (run after UI changes)
npm run test:e2e visual-regression -- --update-snapshots

# View test report
npm run test:e2e:report
```

## How It Works

1. **First run**: Captures baseline screenshots in `tests/e2e/visual-regression.spec.ts-snapshots/`
2. **Subsequent runs**: Compares current screenshots against baselines
3. **Failures**: Generates diff images showing pixel differences

## Configuration

Visual tests use configurable tolerance:
- `maxDiffPixels`: Maximum allowed pixel differences (e.g., 100)
- `threshold`: Pixel difference threshold 0-1 (e.g., 0.2 = 20%)

Adjust these in `visual-regression.spec.ts` based on your UI stability needs.

## Best Practices

- Run visual tests on consistent screen sizes (configured in `playwright.config.ts`)
- Update baselines when intentional UI changes are made
- Review diff images carefully before accepting changes
- Use CI to detect unexpected visual regressions

## Covered Components

- Config flow integration page
- Entity list rendering
- Device cards
- Light entity more-info dialog
- Sensor entity display
- Device diagnostics page
- Full page snapshots
