import { redirect } from "next/navigation";

import SpeakView from "../../components/SpeakView.jsx";
import { cpJson } from "../../lib/controlplane.js";
import { describeCatalogue } from "../../lib/voices.js";
import { readSessionToken } from "../../lib/session.js";

export const metadata = { title: "Speak — QualiaBits Voice" };

/**
 * `?voice=<id>` preselects a voice, which is how the Voice Library hands one over.
 *
 * The id is only honoured when the catalogue the plane just answered with really
 * contains it. That is the whole point of reading the query this way: a link with
 * a stale or hand-typed id lands on the default voice instead of on a form that
 * fails when it is submitted.
 */
export default async function SpeakPage({ searchParams }) {
  const token = await readSessionToken();
  if (!token) redirect("/login");

  const { status, data } = await cpJson("/v1/voices", { token });
  if (status === 401) redirect("/login");
  const catalogue = status === 200 ? data : null;

  const params = await searchParams;
  const wanted = typeof params?.voice === "string" ? params.voice : "";
  const known = (catalogue?.voices ?? []).some((voice) => voice.id === wanted);

  return (
    <>
      <div className="page-head">
        <p className="page-head__eyebrow">Text to speech</p>
        <h1>Speak</h1>
        <p className="page-head__lede">{describeCatalogue(catalogue)}</p>
      </div>
      <SpeakView catalogue={catalogue} initialVoice={known ? wanted : ""} />
    </>
  );
}