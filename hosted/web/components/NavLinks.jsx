"use client";

import { usePathname } from "next/navigation";

/**
 * The navigation, with the current page marked.
 *
 * A client component only because `usePathname` is; there is no state and no
 * data here, so it renders identically on the server and the client.
 */
const LINKS = [
  { href: "/", label: "Home" },
  { href: "/speak", label: "Speak" },
  { href: "/transcribe", label: "Transcribe" },
  { href: "/jobs", label: "Jobs" },
  { href: "/keys", label: "Keys" },
  { href: "/usage", label: "Usage" },
];

export default function NavLinks({ signedIn }) {
  const pathname = usePathname();
  const links = signedIn
    ? LINKS
    : [
        { href: "/", label: "Home" },
        { href: "/login", label: "Sign in" },
        { href: "/signup", label: "Sign up" },
      ];
  return (
    <nav className="nav" aria-label="Primary">
      {links.map((link) => {
        const current = pathname === link.href;
        return (
          <a key={link.href} href={link.href} aria-current={current ? "page" : undefined}>
            {link.label}
          </a>
        );
      })}
    </nav>
  );
}