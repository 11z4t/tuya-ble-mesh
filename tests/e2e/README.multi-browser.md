# Multi-Browser Testing

Tests run across multiple browsers and devices to ensure compatibility.

## Supported Browsers

### Desktop
- **Chromium** (Chrome, Edge, etc.)
- **Firefox**
- **WebKit** (Safari)

### Mobile
- **Mobile Chrome** (Pixel 5 emulation)
- **Mobile Safari** (iPhone 13 emulation)

## Running Multi-Browser Tests

```bash
# Run all tests on all browsers
npm run test:e2e

# Run on specific browser
npm run test:e2e -- --project=chromium
npm run test:e2e -- --project=firefox
npm run test:e2e -- --project=webkit

# Run browser compatibility tests only
npm run test:e2e browser-compatibility

# Run mobile tests
npm run test:e2e -- --project=mobile-chrome
npm run test:e2e -- --project=mobile-safari
```

## Installing Browser Binaries

Before first run, install browser binaries:

```bash
# Install all browsers
npx playwright install

# Install specific browsers
npx playwright install chromium
npx playwright install firefox
npx playwright install webkit
```

## What We Test

- Page loading and rendering
- Navigation between pages
- Search and filtering
- Entity list rendering
- Device dashboard
- CSS loading and styling
- JavaScript execution
- Responsive layout (mobile)
- Touch interactions (mobile)

## Browser Support Matrix

| Feature | Chromium | Firefox | WebKit | Mobile Chrome | Mobile Safari |
|---------|----------|---------|--------|---------------|---------------|
| Page Load | ✅ | ✅ | ✅ | ✅ | ✅ |
| Navigation | ✅ | ✅ | ✅ | ✅ | ✅ |
| Search | ✅ | ✅ | ✅ | ✅ | ✅ |
| Entity List | ✅ | ✅ | ✅ | ✅ | ✅ |
| CSS Rendering | ✅ | ✅ | ✅ | ✅ | ✅ |
| JavaScript | ✅ | ✅ | ✅ | ✅ | ✅ |
| Touch Input | N/A | N/A | N/A | ✅ | ✅ |
| Responsive | ✅ | ✅ | ✅ | ✅ | ✅ |

## CI/CD Integration

In CI, all browsers are tested automatically:

```yaml
- name: Install Playwright Browsers
  run: npx playwright install --with-deps

- name: Run E2E Tests
  run: npm run test:e2e
```

## Troubleshooting

### Browser installation fails
```bash
# Install system dependencies first
npx playwright install-deps
npx playwright install
```

### Tests fail on specific browser
```bash
# Run in headed mode to debug
npm run test:e2e:headed -- --project=firefox

# Enable trace for debugging
npm run test:e2e -- --project=webkit --trace on
```

### Mobile tests don't work
Mobile tests use device emulation. They work on desktop by emulating:
- Screen size and resolution
- Touch events
- User agent string
- Viewport settings

No real mobile device needed for testing.
