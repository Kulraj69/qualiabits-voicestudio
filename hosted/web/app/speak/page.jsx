import { redirect } from "next/navigation";

import SpeakView from "../../components/SpeakView.jsx";
import { cpJson } from "../../lib/controlplane.js";
import { describeCatalogue } from "../../lib/voices.js";
import { readSessionToken } from "../../lib/session.js";

export const metadata = { title: "Speak — QualiaBits Voice" };

export default async function SpeakPage() {
  const token = await readSessionToken();
  if (!token) redirect("/login");

  const { status, data } = await cpJson("/v1/voices", { token });
  if (status === 401) redirect("/login");
  const catalogue = status === 200 ? data : null;

  return (
    <>
      <div className="page-head">
        <p className="page-head__eyebrow">Text to speech</p>
        <h1>Speak</h1>
        <p className="page-head__lede">{describeCatalogue(catalogue)}</p>
      </div>
      <SpeakView catalogue={catalogue} />
    </>
  );
}