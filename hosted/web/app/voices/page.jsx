import { redirect } from "next/navigation";

import VoiceLibraryView from "../../components/VoiceLibraryView.jsx";
import { cpJson } from "../../lib/controlplane.js";
import { describeCatalogue } from "../../lib/voices.js";
import { readSessionToken } from "../../lib/session.js";

export const metadata = { title: "Voice library — QualiaBits Voice" };

export default async function VoiceLibraryPage() {
  const token = await readSessionToken();
  if (!token) redirect("/login");

  const { status, data } = await cpJson("/v1/voices", { token });
  if (status === 401) redirect("/login");
  const catalogue = status === 200 ? data : null;

  return (
    <>
      <div className="page-head">
        <p className="page-head__eyebrow">The engine's catalogue</p>
        <h1>Voice library</h1>
        <p className="page-head__lede">{describeCatalogue(catalogue)}</p>
      </div>
      <VoiceLibraryView catalogue={catalogue} />
    </>
  );
}