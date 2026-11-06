import Link from "next/link";

import NavLinks from "./NavLinks.jsx";
import SignOutButton from "./SignOutButton.jsx";
import ThemeToggle from "./ThemeToggle.jsx";
import { readWorkspace } from "../lib/session.js";

/**
 * The top bar. A server component: it reads the workspace cookie itself, so the
 * signed-in name is in the first HTML and no client round-trip is needed.
 */
export default async function SiteHeader() {
  const workspace = await readWorkspace();
  const label = workspace?.display_name || workspace?.email || "";

  return (
    <header className="topbar">
      {/* No aria-label here on purpose: with one, the link's accessible name
          ("QualiaBits Voice home") did not contain the text on screen
          ("QualiaBits Voice Dashboard"), which is what axe's
          `label-content-name-mismatch` rule is about — a voice-control user
          saying what they see would not have matched the control. The brand
          text is already a name; the mark beside it is hidden from the
          accessibility tree, and the strapline is hidden by CSS on a phone. */}
      <Link href="/" className="brand">
        <span className="brand__mark" aria-hidden="true">
          ◇
        </span>
        <span>QualiaBits Voice</span>
        <span className="brand__sub">Dashboard</span>
      </Link>
      <NavLinks signedIn={Boolean(workspace)} />
      <span className="topbar__spacer" />
      <ThemeToggle />
      {workspace ? (
        <span className="who">
          <span className="who__email" title={label}>
            {label}
          </span>
          <SignOutButton />
        </span>
      ) : (
        <span className="who">
          <Link className="btn btn--ghost btn--sm" href="/login">
            Sign in
          </Link>
          <Link className="btn btn--sm" href="/signup">
            Create workspace
          </Link>
        </span>
      )}
    </header>
  );
}