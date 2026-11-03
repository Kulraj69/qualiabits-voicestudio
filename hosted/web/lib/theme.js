/**
 * The theme's rules, kept pure so they can be tested without a browser.
 *
 * M3's brief asks for a dashboard that is "responsive, fast, dark/light". Dark is
 * the design that shipped first and stays the default; light is the same page with
 * a different set of values for the tokens in `app/globals.css`, so no component
 * branches on the theme and no colour is written twice.
 *
 * The first theme of a page load is decided in the HTML, before the stylesheet
 * paints, by `NO_FLASH_SCRIPT` below. That script cannot import this module — it
 * has to run on its own, inline, as the first thing in the document — so its two
 * rules live here twice: once as functions the component uses, once as the string
 * the document carries. The unit test evaluates the string against a fake window
 * and asserts it agrees with the functions for every input, so the copy cannot
 * drift from the rules it implements.
 */

/** Where a reader's choice is remembered. Only this key, and only this shape. */
export const THEME_STORAGE_KEY = "qb_theme";

/** The two themes. Dark is the design; light is the alternative. */
export const THEMES = ["dark", "light"];

/**
 * What a stored value means. Anything that is not exactly one of the two theme
 * names — a stale value, a hand-edited one, an empty string — is *unknown* rather
 * than a theme, so a corrupt store falls back to the reader's system preference
 * instead of pinning them to something arbitrary.
 */
export function parseStoredTheme(raw) {
  if (raw === "light" || raw === "dark") return raw;
  return null;
}

/**
 * The theme a page load starts in: the reader's remembered choice, else their
 * system preference, else dark. `prefersLight` is the media query's own answer,
 * passed in rather than read, so this stays a pure function.
 */
export function resolveTheme({ stored = null, prefersLight = false } = {}) {
  const remembered = parseStoredTheme(stored);
  if (remembered) return remembered;
  return prefersLight ? "light" : "dark";
}

/** The other theme. The toggle's whole job. */
export function nextTheme(current) {
  return current === "light" ? "dark" : "light";
}

/**
 * What the toggle says it will do. It names the theme you get, not the one you
 * are in, and the same sentence is the button's accessible name and its tooltip —
 * one string, so a screen-reader user and a sighted one are told the same thing.
 */
export function themeToggleLabel(current) {
  return current === "light" ? "Switch to the dark theme" : "Switch to the light theme";
}

/**
 * The blocking script the document carries as its first child. It resolves the
 * theme and writes `data-theme` on `<html>`; `globals.css` keys every light value
 * off that attribute. It must not throw: a browser that refuses `localStorage`
 * (private mode, a blocked store) falls through to the system preference, and a
 * browser that refuses the media query too falls through to dark.
 *
 * Kept deliberately small and dependency-free. It runs before the page has a
 * stylesheet, so there is nothing to import and nothing to wait for.
 */
export const NO_FLASH_SCRIPT = `(function () {
  var theme = "dark";
  try {
    var stored = window.localStorage.getItem(${JSON.stringify(THEME_STORAGE_KEY)});
    if (stored === "light" || stored === "dark") {
      theme = stored;
    } else if (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches) {
      theme = "light";
    }
  } catch (error) {
    theme = "dark";
  }
  document.documentElement.setAttribute("data-theme", theme);
})();`;