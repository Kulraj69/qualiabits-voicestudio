import Link from "next/link";

import NavLinks from "./NavLinks.jsx";
import SignOutButton from "./SignOutButton.jsx";
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
      <Link href="/" className="brand" aria-label="QualiaBits Voice home">
        <span className="brand__mark" aria-hidden="true">
          ◇
        </span>
        <span>QualiaBits Voice</span>
        <span className="brand__sub">Dashboard</span>
      </Link>
      <NavLinks signedIn={Boolean(workspace)} />
      <span className="topbar__spacer" />
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