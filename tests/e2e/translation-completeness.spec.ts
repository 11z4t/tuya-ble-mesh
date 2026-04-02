import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

/**
 * Translation completeness tests.
 *
 * For every language file other than en.json, verify that:
 *   1. All dot-path keys present in en.json also exist in the language file.
 *   2. No translated value is an empty string.
 *
 * No browser is launched — tests read translation JSON files from disk only.
 * They run once per Playwright project, but each run completes in milliseconds.
 *
 * Translations directory:
 *   custom_components/tuya_ble_mesh/translations/
 *
 * Supported languages (auto-discovered at test-collection time):
 *   da, de, fi, fr, kl, nb, nl, sv, uk  (plus any added later)
 */

const TRANSLATIONS_DIR = path.resolve(
  __dirname,
  '../../custom_components/tuya_ble_mesh/translations',
);

// ── Helpers ───────────────────────────────────────────────────────────────────

type JsonObject = Record<string, unknown>;

/**
 * Returns every dot-separated path that leads to a scalar leaf value inside
 * a JSON object.  Arrays are treated as scalars (their index paths are not
 * expanded) because HA translation files never use arrays at the leaf level.
 */
function leafPaths(obj: JsonObject, prefix = ''): string[] {
  const paths: string[] = [];
  for (const [key, val] of Object.entries(obj)) {
    const p = prefix ? `${prefix}.${key}` : key;
    if (val !== null && typeof val === 'object' && !Array.isArray(val)) {
      paths.push(...leafPaths(val as JsonObject, p));
    } else {
      paths.push(p);
    }
  }
  return paths;
}

/**
 * Resolves a dot-separated key path against a JSON object.
 * Returns `undefined` if any segment of the path is absent.
 */
function valueAt(obj: JsonObject, dotPath: string): unknown {
  return dotPath
    .split('.')
    .reduce<unknown>(
      (cur, k) =>
        cur !== null && typeof cur === 'object' && !Array.isArray(cur)
          ? (cur as JsonObject)[k]
          : undefined,
      obj,
    );
}

function readJson(filePath: string): JsonObject {
  return JSON.parse(fs.readFileSync(filePath, 'utf-8')) as JsonObject;
}

// ── Load reference (en.json) at test-collection time ─────────────────────────

const EN: JsonObject = readJson(path.join(TRANSLATIONS_DIR, 'en.json'));
const EN_PATHS: string[] = leafPaths(EN);

// Discover every non-English translation file in the directory.
const LANGUAGES: string[] = fs
  .readdirSync(TRANSLATIONS_DIR)
  .filter((f) => f.endsWith('.json') && f !== 'en.json')
  .map((f) => path.basename(f, '.json'))
  .sort();

// ── Tests ─────────────────────────────────────────────────────────────────────

test.describe('Translation completeness', () => {
  // Sanity-check: the reference file itself is non-empty.
  test('en.json has translation keys', () => {
    expect(EN_PATHS.length).toBeGreaterThan(0);
  });

  // At least one non-English language must exist.
  test('at least one non-English language file exists', () => {
    expect(LANGUAGES.length).toBeGreaterThan(0);
  });

  for (const lang of LANGUAGES) {
    test.describe(lang, () => {
      test('all en.json keys are present and non-empty', () => {
        const langData = readJson(path.join(TRANSLATIONS_DIR, `${lang}.json`));

        const missing: string[] = [];
        const empty: string[] = [];

        for (const p of EN_PATHS) {
          const val = valueAt(langData, p);
          if (val === undefined) {
            missing.push(p);
          } else if (val === '') {
            empty.push(p);
          }
        }

        expect(
          missing,
          `[${lang}] Keys present in en.json but missing from ${lang}.json:\n  ${missing.join('\n  ')}`,
        ).toHaveLength(0);

        expect(
          empty,
          `[${lang}] Keys with empty-string values in ${lang}.json:\n  ${empty.join('\n  ')}`,
        ).toHaveLength(0);
      });

      test('no stale keys absent from en.json', () => {
        // Stale keys are those present in the language file but not in en.json.
        // They represent translations that were removed from the English source
        // and should be cleaned up from language files too.
        const langData = readJson(path.join(TRANSLATIONS_DIR, `${lang}.json`));
        const langPaths = leafPaths(langData);

        const stale = langPaths.filter((p) => valueAt(EN, p) === undefined);

        expect(
          stale,
          `[${lang}] Stale keys in ${lang}.json not present in en.json:\n  ${stale.join('\n  ')}`,
        ).toHaveLength(0);
      });
    });
  }
});
