import assert from "node:assert/strict";
import { test } from "node:test";

import {
  NO_FLASH_SCRIPT,
  nextTheme,
  parseStoredTheme,
  resolveTheme,
  THEME_STORAGE_KEY,
  THEMES,
  themeToggleLabel,
} from "../lib/theme.js";

test("a stored value is a theme only when it is exactly one of the two", () => {
  assert.equal(parseStoredTheme("light"), "light");
  assert.equal(parseStoredTheme("dark"), "dark");
  // Unknown rather than a theme: a stale or hand-edited value must not pin a
  // reader to whatever it happens to spell.
  assert.equal(parseStoredTheme("Light"), null);
  assert.equal(parseStoredTheme("system"), null);
  assert.equal(parseStoredTheme(""), null);
  assert.equal(parseStoredTheme(null), null);
  assert.equal(parseStoredTheme(undefined), null);
});

test("the remembered choice wins, then the system, then dark", () => {
  const { stored, prefersLight } = { stored: "light", prefersLight: false };
  assert.equal(resolveTheme({ stored, prefersLight }), "light");
  assert.equal(resolveTheme({ stored: "dark", prefersLight: true }), "dark");
  assert.equal(resolveTheme({ stored: "light", prefersLight: true }), "light");
  assert.equal(resolveTheme({ stored: "dark", prefersLight: false }), "dark");
  // Nothing stored: the reader's own system preference decides.
  assert.equal(resolveTheme({ stored: null, prefersLight: true }), "light");
  assert.equal(resolveTheme({ stored: null, prefersLight: false }), "dark");
  // A corrupt store is treated as nothing stored, not as a theme.
  assert.equal(resolveTheme({ stored: "chartreuse", prefersLight: true }), "light");
  assert.equal(resolveTheme(), "dark");
});

test("the toggle flips and nothing else", () => {
  assert.equal(nextTheme("dark"), "light");
  assert.equal(nextTheme("light"), "dark");
  assert.equal(nextTheme(undefined), "light");
  for (const theme of THEMES) assert.notEqual(nextTheme(theme), theme);
});

test("the toggle names the theme you get, not the one you are in", () => {
  assert.equal(themeToggleLabel("dark"), "Switch to the light theme");
  assert.equal(themeToggleLabel("light"), "Switch to the dark theme");
});

/**
 * The inline script cannot import the functions above — it has to stand alone in
 * the document — so it re-states the same two rules. This runs the real string
 * against a fake window and asserts it agrees with those functions for every
 * interesting input, which is what keeps the copy from drifting.
 */
function runScript({ stored = null, prefersLight = false, localStorageThrows = false } = {}) {
  const attributes = {};
  const document = {
    documentElement: {
      setAttribute(name, value) {
        attributes[name] = value;
      },
    },
  };
  const window = {
    localStorage: {
      getItem(key) {
        assert.equal(key, THEME_STORAGE_KEY, "the script must read the one key the toggle writes");
        if (localStorageThrows) throw new Error("the store is blocked");
        return stored;
      },
    },
    matchMedia(query) {
      assert.equal(query, "(prefers-color-scheme: light)");
      return { matches: prefersLight };
    },
  };
  // eslint-disable-next-line no-new-func
  new Function("window", "document", NO_FLASH_SCRIPT)(window, document);
  return attributes["data-theme"];
}

test("the inline no-flash script resolves the same theme the component would", () => {
  for (const stored of [null, "light", "dark", "chartreuse", ""]) {
    for (const prefersLight of [true, false]) {
      assert.equal(
        runScript({ stored, prefersLight }),
        resolveTheme({ stored, prefersLight }),
        `script and component disagree for stored=${JSON.stringify(stored)} prefersLight=${prefersLight}`,
      );
    }
  }
});

test("a browser with no usable store falls back to dark, never to a throw", () => {
  assert.equal(runScript({ localStorageThrows: true }), "dark");
  assert.equal(runScript({ localStorageThrows: true, prefersLight: true }), "dark");
});

test("the script sets the attribute the stylesheet keys its light values off", () => {
  assert.match(NO_FLASH_SCRIPT, /documentElement\.setAttribute\("data-theme", theme\)/);
  assert.match(NO_FLASH_SCRIPT, new RegExp(THEME_STORAGE_KEY));
});