import "./globals.css";

import SiteHeader from "../components/SiteHeader.jsx";
import { NO_FLASH_SCRIPT } from "../lib/theme.js";

export const metadata = {
  title: "QualiaBits Voice — dashboard",
  description:
    "The browser dashboard for the hosted QualiaBits Voice service, speaking through the control plane.",
};

export default function RootLayout({ children }) {
  return (
    // `suppressHydrationWarning` is on <html> because the inline script below
    // writes `data-theme` onto it before React hydrates. The attribute is the
    // point, not drift: it is how the reader's theme reaches the stylesheet
    // before the first paint.
    <html lang="en" suppressHydrationWarning>
      <body>
        {/* Runs before anything else is parsed, so the theme is on <html> before
            the stylesheet has anything to paint. Dark is the fallback. */}
        <script dangerouslySetInnerHTML={{ __html: NO_FLASH_SCRIPT }} />
        <div className="shell">
          <SiteHeader />
          <main className="main">{children}</main>
          <footer className="footer">
            <p>
              Local only. This dashboard, the control plane and the engine all run on
              this machine on loopback. Nothing here is deployed, there is no public
              URL, no real S3 bucket and no GPU.
            </p>
            <p className="boundary">
              Speech you hear on the Speak page is metered by the control plane under
              your workspace; the audio is returned by the engine through the plane,
              never straight to the browser.
            </p>
          </footer>
        </div>
      </body>
    </html>
  );
}