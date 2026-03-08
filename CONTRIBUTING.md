# Contributing to Tuya BLE Mesh Integration

Thank you for considering contributing! This document outlines the process.

## How to Contribute

### Reporting Bugs
1. Check existing issues first
2. Use the bug report template
3. Include:
   - HA version
   - Integration version
   - Device model
   - Debug logs
   - Steps to reproduce

### Suggesting Features
1. Check existing feature requests
2. Use the feature request template
3. Explain use case and proposed solution

### Contributing Code

#### Setup Development Environment
```bash
# Clone the repository
git clone https://github.com/4recon/tuya-ble-mesh.git
cd tuya-ble-mesh

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows

# Install dependencies
pip install -r requirements-dev.txt

# Install pre-commit hooks
pre-commit install
```

#### Development Workflow
1. **Fork the repository**
2. **Create a feature branch**
   ```bash
   git checkout -b feature/your-feature-name
   ```
3. **Make your changes**
   - Follow code style (Black, isort, Ruff)
   - Add type hints
   - Write docstrings
   - Update tests
4. **Run tests**
   ```bash
   pytest tests/
   ```
5. **Run linters**
   ```bash
   ruff check .
   mypy .
   ```
6. **Commit your changes**
   ```bash
   git commit -m "Add feature: description"
   ```
7. **Push to your fork**
   ```bash
   git push origin feature/your-feature-name
   ```
8. **Create Pull Request**
   - Use PR template
   - Link related issues
   - Describe changes clearly

#### Code Style
- Follow PEP 8
- Use Black for formatting
- Use isort for imports
- Use type hints
- Max line length: 100 characters
- Docstrings: Google style

#### Testing
- Write unit tests for new functions
- Write integration tests for new features
- Add E2E tests for UI changes (see `tests/e2e/README.md`)
- Run accessibility tests for frontend changes
- Ensure all tests pass
- Aim for >80% coverage

**Run all tests:**
```bash
# Unit and integration tests
pytest tests/unit tests/security

# E2E tests (requires running HA instance)
npx playwright test

# Accessibility tests
npx playwright test accessibility
```

#### Documentation
- Update README.md if needed
- Update CHANGELOG.md
- Add docstrings to new code
- Update type hints

### Device Compatibility
If you're adding support for a new device:
1. Test thoroughly with the device
2. Document device model and features
3. Add to device compatibility list
4. Include test results

### Translation
Help translate to other languages:
1. Copy `translations/en.json`
2. Translate strings
3. Submit PR with new language file

## Code Review Process
1. Maintainer reviews PR
2. Feedback provided if needed
3. Update PR based on feedback
4. Approval and merge

## Community Guidelines
- Be respectful and constructive
- Help newcomers
- Stay on topic
- Follow HA Code of Conduct

## Recognition
Contributors are:
- Listed in CHANGELOG
- Thanked in release notes
- Co-author in commits (when applicable)

## Questions?
- Open a GitHub Discussion
- Ask in Discord `#integrations`
- Post in HA Community Forum

Thank you for contributing! 🎉
