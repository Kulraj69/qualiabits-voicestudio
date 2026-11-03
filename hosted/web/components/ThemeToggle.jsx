"use client";

import { useEffect, useState } from "react";

import { nextTheme, themeToggleLabel, THEME_STORAGE_KEY } from "../lib/theme.js";

/**
 * The dark/light switch in the top bar.
 *
 * The theme is not this component's state: the document carries it as
 * `data-theme` on `<html>`, set before the first paint by the inline script in
 * `app/layout.jsx`. This button reads that attribute on mount, flips it, and
 * remembers the choice. Until the effect runs `theme` is null and the control
 * renders its dark-mode face, which is what the server sent, so hydration matches.
 *
 * If the store refuses the write (private mode), the theme still changes on this
 * page — the reader is not told their click did nothing when it did something.
 */
export default function ThemeToggle() {
  const [theme, setTheme] = useState(null);

  useEffect(() => {
    const current = document.documentElement.getAttribute("data-theme");
    setTheme(current === "light" ? "light" : "dark");
  }, []);

  const label = themeToggleLabel(theme ?? "dark");

  function flip() {
    const target = nextTheme(theme ?? "dark");
    document.documentElement.setAttribute("data-theme", target);
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, target);
    } catch (error) {
      // A store that refuses the write is a browser setting, not a failure of
      // this page: the theme still applies for the rest of this visit.
    }
    setTheme(target);
  }

  return (
    <button
      type="button"
      className="theme-toggle"
      data-testid="theme-toggle"
      data-theme-current={theme ?? "dark"}
      aria-label={label}
      title={label}
      onClick={flip}
    >
      <span className="theme-toggle__glyph" aria-hidden="true">
        {theme === "light" ? "☾" : "☀"}
      </span>
    </button>
  );
}