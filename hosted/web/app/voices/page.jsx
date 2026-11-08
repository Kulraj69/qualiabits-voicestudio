import { redirect } from "next/navigation";

import VoiceLibraryView from "../../components/VoiceLibraryView.jsx";
import PlaneFailed from "../../components/PlaneFailed.jsx";
import { readPlane } from "../../lib/planeRead.js";
import { describeCatalogue } from "../../lib/voices.js";
import { readSessionToken } from "../../lib/session.js";

export const metadata = { title: "Voice library — QualiaBits Voice" };

export default async function VoiceLibraryPage() {
  const token = await readSessionToken();
  if (!token) redirect("/login");

  const read = await readPlane("/v1/voices", { token });
  if (read.status === 401) redirect("/login");
  const catalogue = read.ok ? read.data : null;

  return (
    <>
      <div className="page-head">
        <p className="page-head__eyebrow">The engine's catalogue</p>
        <h1>Voice library</h1>
        <p className="page-head__lede">{read.failed ? "" : describeCatalogue(catalogue)}</p>
      </div>
      {read.failed ? <PlaneFailed error={read.error} /> : <VoiceLibraryView catalogue={catalogue} />}
    </>
  );
}