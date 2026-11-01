import "./globals.css";

import SiteHeader from "../components/SiteHeader.jsx";

export const metadata = {
  title: "QualiaBits Voice — dashboard",
  description:
    "The browser dashboard for the hosted QualiaBits Voice service, speaking through the control plane.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>
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