"use client";

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";

import {
  activeAfterFilter,
  activeOption,
  filterOptions,
  reduceKey,
  withGroupHeaders,
} from "../lib/searchable-select.js";

/**
 * A searchable select box, for this package.
 *
 * The repository rule is that new or redesigned select boxes are searchable and
 * that native select controls are not introduced (`AGENTS.md`, "Shared select
 * controls"). The fork's own control is an Electron component, and this package
 * imports nothing from outside itself, so this is the package's own: the same
 * behaviour — a labeled trigger, a search field, arrow-key movement that wraps,
 * Enter to commit, Escape to leave without committing, a disabled state — with the
 * pure half in `../lib/searchable-select.js` where it is unit-tested.
 *
 * `ariaLabel` is required: the rule asks for a localized label on the control, and
 * a control whose only name is a visible neighbour's text is unnamed to a reader.
 */
export default function SearchableSelect({
  options,
  value,
  onChange,
  ariaLabel,
  testId,
  placeholder = "Select…",
  searchPlaceholder = "Type to narrow",
  disabled = false,
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(-1);
  const rootRef = useRef(null);
  const triggerRef = useRef(null);
  const searchRef = useRef(null);
  const baseId = useId();
  const listId = `${baseId}-list`;

  const listed = Array.isArray(options) ? options : [];
  const shown = useMemo(() => filterOptions(listed, query), [listed, query]);
  const rows = useMemo(() => withGroupHeaders(shown), [shown]);
  const current = listed.find((option) => option.value === value) || null;
  const active = activeOption(shown, activeIndex);

  const close = useCallback((refocus) => {
    setOpen(false);
    setActiveIndex(-1);
    setQuery("");
    if (refocus && triggerRef.current) triggerRef.current.focus();
  }, []);

  function openWith(index) {
    setOpen(true);
    setActiveIndex(index);
  }

  function commit(index) {
    const option = activeOption(shown, index);
    if (!option) return;
    onChange(option.value);
    close(true);
  }

  function key(event) {
    const next = reduceKey({ open, activeIndex }, event.key, shown.length);
    if (next.commit) {
      event.preventDefault();
      commit(next.activeIndex);
      return;
    }
    if (next.open === open && next.activeIndex === activeIndex) return;
    // Arrow keys scroll the page, Enter submits the form, Escape and Tab are
    // handled here — none of that should reach past the control while it is open.
    if (open && ["ArrowDown", "ArrowUp", "Home", "End", "Enter", "Escape", "Tab"].includes(event.key)) {
      event.preventDefault();
    }
    setActiveIndex(next.activeIndex);
    if (next.open !== open) {
      if (next.open) openWith(next.activeIndex);
      else close(false);
    }
  }

  // The search field is where typing goes, so it takes focus when the list opens;
  // the trigger takes it back when the list closes.
  useEffect(() => {
    if (open && searchRef.current) searchRef.current.focus();
  }, [open]);

  // A click anywhere outside the control closes it, without stealing focus.
  useEffect(() => {
    if (!open) return undefined;
    function onPointerDown(event) {
      if (rootRef.current && !rootRef.current.contains(event.target)) close(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open, close]);

  return (
    <div className="ss" ref={rootRef}>
      <button
        type="button"
        ref={triggerRef}
        role="combobox"
        className="ss__trigger"
        data-testid={testId}
        data-value={value}
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-activedescendant={open && active ? `${baseId}-opt-${activeIndex}` : undefined}
        disabled={disabled}
        onClick={() => (open ? close(true) : openWith(shown.length > 0 ? 0 : -1))}
        onKeyDown={key}
      >
        <span className="ss__value">{current ? current.label : placeholder}</span>
        <span className="ss__chevron" aria-hidden="true">
          ▾
        </span>
      </button>

      {open ? (
        <div className="ss__pop" data-testid={testId ? `${testId}-pop` : undefined}>
          <input
            type="search"
            ref={searchRef}
            className="ss__search"
            data-testid={testId ? `${testId}-search` : undefined}
            aria-label={`Search ${ariaLabel}`}
            placeholder={searchPlaceholder}
            value={query}
            onChange={(event) => {
              const next = event.target.value;
              setQuery(next);
              setActiveIndex(activeAfterFilter(filterOptions(listed, next)));
            }}
            onKeyDown={key}
          />
          {shown.length === 0 ? (
            <p className="ss__empty" data-testid={testId ? `${testId}-empty` : undefined}>
              Nothing matches that.
            </p>
          ) : (
            <ul
              className="ss__list"
              id={listId}
              role="listbox"
              aria-label={ariaLabel}
              data-testid={testId ? `${testId}-list` : undefined}
            >
              {rows.map((row) => {
                if (row.header !== undefined) {
                  // A header, not an option: `role="presentation"` keeps it out of
                  // the listbox's options, so a reader and the arrow keys both see
                  // exactly the options the pointer does.
                  return (
                    <li key={`head-${row.header}`} role="presentation" className="ss__group">
                      {row.header}
                    </li>
                  );
                }
                const index = shown.indexOf(row.option);
                const option = row.option;
                return (
                  <li
                    key={option.value}
                    id={`${baseId}-opt-${index}`}
                    role="option"
                    aria-selected={option.value === value}
                    data-value={option.value}
                    className={index === activeIndex ? "ss__option ss__option--active" : "ss__option"}
                    // mousedown, not click: the field would blur out of the document
                    // before a click lands.
                    onMouseDown={(event) => {
                      event.preventDefault();
                      commit(index);
                    }}
                    onMouseEnter={() => setActiveIndex(index)}
                  >
                    {option.label}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      ) : null}
    </div>
  );
}