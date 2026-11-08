import { redirect } from "next/navigation";

import KeysView from "../../components/KeysView.jsx";
import PlaneFailed from "../../components/PlaneFailed.jsx";
import { readPlane } from "../../lib/planeRead.js";
import { readSessionToken } from "../../lib/session.js";

export const metadata = { title: "API keys — QualiaBits Voice" };

export default async function KeysPage() {
  const token = await readSessionToken();
  if (!token) redirect("/login");

  const read = await readPlane("/v1/api-keys", { token });
  if (read.status === 401) redirect("/login");

  return (
    <>
      <div className="page-head">
        <p className="page-head__eyebrow">The product API</p>
        <h1>API keys</h1>
        <p className="page-head__lede">
          A key is what speaks through <code className="mono">POST /v1/audio/speech</code>.
          It is returned once and stored only as a digest; afterwards this page can show
          its public id and its label, and revoke it — nothing more.
        </p>
      </div>
      {read.failed ? (
        <PlaneFailed error={read.error} />
      ) : (
        <KeysView initialKeys={read.data?.api_keys ?? []} />
      )}
    </>
  );
}