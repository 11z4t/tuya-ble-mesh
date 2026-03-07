# Accessibility Testing (WCAG 2.1 AA)

Automated accessibility testing using axe-core to ensure WCAG 2.1 Level AA compliance.

## Requirements

Per CLAUDE-shared.md, all web solutions must meet WCAG 2.1 AA standards and comply with:
- **EAA (EU 2019/882)**: Digital accessibility directive
- **WCAG 2.1 AA**: Web Content Accessibility Guidelines

## Running Accessibility Tests

```bash
# Run all accessibility tests
npm run test:e2e accessibility

# Run in headed mode to see visual checks
npm run test:e2e:headed accessibility

# View detailed report
npm run test:e2e:report
```

## What We Test

### Automated Checks (axe-core)
- Color contrast (4.5:1 for text, 3:1 for large elements)
- Form label associations
- ARIA attributes on interactive elements
- Image alt text
- Heading hierarchy (h1→h2→h3, no skipping)
- HTML lang attribute
- Duplicate IDs
- Keyboard accessibility

### Manual Checks (via Playwright)
- Visible focus indicators on all interactive elements
- Keyboard navigation (Tab/Shift+Tab)
- Heading structure validation

## WCAG 2.1 AA Coverage

| Requirement | Test Coverage |
|-------------|---------------|
| Skip-navigation | Manual verification needed |
| Form labels | ✅ Automated (axe-core) |
| ARIA attributes | ✅ Automated (axe-core) |
| Focus indicators | ✅ Manual + automated |
| Color contrast | ✅ Automated (axe-core) |
| Alt text | ✅ Automated (axe-core) |
| Heading hierarchy | ✅ Manual validation |
| Lang attribute | ✅ Manual check |

## Fixing Violations

When tests fail:

1. **Review the violation details** in the test output
2. **Check axe DevTools** browser extension for interactive debugging
3. **Common fixes**:
   - Add `aria-label` to buttons without text
   - Associate `<label>` with `<input>` via `htmlFor`/`id`
   - Increase color contrast ratios
   - Add alt text to images
   - Fix heading hierarchy gaps

## EU Directive Compliance

This test suite helps verify:
- **EAA (EU 2019/882)**: Digital accessibility = WCAG 2.1 AA
- **GDPR**: Not covered by these tests (separate compliance needed)
- **ePrivacy**: Not covered (cookie consent testing separate)

## Resources

- [WCAG 2.1 Quick Reference](https://www.w3.org/WAI/WCAG21/quickref/)
- [axe-core Rules](https://github.com/dequelabs/axe-core/blob/develop/doc/rule-descriptions.md)
- [HA Accessibility Guide](https://www.home-assistant.io/accessibility/)
