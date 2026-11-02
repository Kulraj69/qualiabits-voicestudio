/**
 * The keyboard and filtering half of a searchable select, kept pure.
 *
 * The repository rule (`AGENTS.md`, "Shared select controls") says every new or
 * redesigned select box must be the searchable one and that native select elements
 * controls must not be introduced. The fork's control lives in the Electron app
 * (`electron/src/shared/components/SearchableSelect.jsx`), and this package
 * imports nothing from outside itself, so the library page uses the control in
 * `../components/SearchableSelect.jsx` instead.
 *
 * This module is the part of that control a browser is not needed to check: which
 * options a query keeps, where the keyboard moves the active row, and what a key
 * means. The component only renders what these functions return.
 */

/** The query, trimmed and folded — the same folding for filtering and matching. */
export function normalizeQuery(query) {
  return String(query ?? "").trim().toLowerCase();
}

/**
 * A row the control can actually offer: an object with a value to commit.
 *
 * A malformed entry is dropped rather than rendered — a row with no value can be
 * pressed and cannot be chosen, which is worse than not being there.
 */
export function usableOptions(options) {
  return (Array.isArray(options) ? options : []).filter(
    (option) => option && typeof option === "object" && option.value !== undefined && option.value !== null,
  );
}

/**
 * Keep the options a query matches.
 *
 * An empty query keeps everything usable. A non-empty query matches the label or
 * the value, case-insensitively, as a substring. Nothing is ever added: the result
 * is always a subset of what arrived, in the order it arrived.
 */
export function filterOptions(options, query) {
  const list = usableOptions(options);
  const needle = normalizeQuery(query);
  if (!needle) return list.slice();
  return list.filter((option) => {
    const label = String(option?.label ?? "").toLowerCase();
    const value = String(option?.value ?? "").toLowerCase();
    return label.includes(needle) || value.includes(needle);
  });
}

/** Where the active row should sit after a move of `delta`, wrapping at both ends. */
export function nextIndex(count, current, delta) {
  if (!Number.isFinite(count) || count <= 0) return -1;
  const from = Number.isInteger(current) && current >= 0 ? current : (delta > 0 ? -1 : 0);
  return (((from + delta) % count) + count) % count;
}

/** The active row, or null when nothing is active. */
export function activeOption(options, index) {
  const list = Array.isArray(options) ? options : [];
  if (!Number.isInteger(index) || index < 0 || index >= list.length) return null;
  return list[index];
}

/**
 * What a key press means.
 *
 * Returns the next state and whether the press commits the active row, so the
 * component holds no keyboard logic of its own. `count` is the number of rows the
 * keyboard can move through (the filtered rows), never the whole catalogue.
 *
 * - Closed: ArrowDown, ArrowUp, Enter or Space opens, on the first or last row.
 * - Open: ArrowDown and ArrowUp move and wrap; Home and End jump to the ends;
 *   Enter commits the active row; Escape and Tab close without committing.
 * - Any other key is not ours: the state is returned unchanged.
 */
export function reduceKey(state, key, count) {
  const open = Boolean(state?.open);
  const activeIndex = Number.isInteger(state?.activeIndex) ? state.activeIndex : -1;
  const rows = Number.isFinite(count) && count > 0 ? count : 0;

  if (!open) {
    if (key === "ArrowDown" || key === "Enter" || key === " " || key === "Spacebar") {
      return { open: true, activeIndex: rows > 0 ? 0 : -1, commit: false };
    }
    if (key === "ArrowUp") {
      return { open: true, activeIndex: rows > 0 ? rows - 1 : -1, commit: false };
    }
    return { open, activeIndex, commit: false };
  }

  switch (key) {
    case "ArrowDown":
      return { open: true, activeIndex: nextIndex(rows, activeIndex, 1), commit: false };
    case "ArrowUp":
      return { open: true, activeIndex: nextIndex(rows, activeIndex, -1), commit: false };
    case "Home":
      return { open: true, activeIndex: rows > 0 ? 0 : -1, commit: false };
    case "End":
      return { open: true, activeIndex: rows > 0 ? rows - 1 : -1, commit: false };
    case "Enter":
      return { open: true, activeIndex, commit: activeIndex >= 0 && activeIndex < rows };
    case "Escape":
    case "Tab":
      return { open: false, activeIndex: -1, commit: false };
    default:
      return { open: true, activeIndex, commit: false };
  }
}

/**
 * Where the active row goes once the query has changed.
 *
 * A filtered list is a new list: the old index pointed at a row that may not be in
 * it any more, so the active row is the first one that survived, or nothing.
 */
export function activeAfterFilter(shown) {
  return Array.isArray(shown) && shown.length > 0 ? 0 : -1;
}

/**
 * Lay the filtered options out as rows, with a header wherever the group changes.
 *
 * The pickers this control replaced used `<optgroup>`, which the browser draws as a
 * header the keyboard cannot reach. Here a header is a row of its own kind: the
 * arrow keys walk the options and step over it, so the keyboard sees exactly the
 * options the pointer does. Grouping is display only — it never reorders or drops an
 * option, and an option with no group gets no header.
 */
export function withGroupHeaders(options) {
  const rows = [];
  let last = null;
  for (const option of filterOptions(options, "")) {
    const group = typeof option?.group === "string" && option.group ? option.group : null;
    if (group && group !== last) rows.push({ header: group });
    last = group;
    rows.push({ option });
  }
  return rows;
}