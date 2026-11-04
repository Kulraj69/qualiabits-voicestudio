import { redirect } from "next/navigation";

import DubView from "../../components/DubView.jsx";
import { cpJson } from "../../lib/controlplane.js";
import { readSessionToken } from "../../lib/session.js";

export const metadata = { title: "Dub — QualiaBits Voice" };

/**
 * The dubbing page.
 *
 * It loads what it needs to render before the customer presses anything: the
 * voices the plane offers (so the picker cannot invent one) and the dubs the
 * workspace has already made. Both come from the plane under the session, and a
 * signed-out visitor is sent to sign in rather than to a form that can only 401.
 */
export default async function DubPage() {
  const token = await readSessionToken();
  if (!token) redirect("/login");

  const [voices, dubs] = await Promise.all([
    cpJson("/v1/voices", { token }),
    cpJson("/v1/dubs?limit=20", { token }),
  ]);
  if (voices.status === 401 || dubs.status === 401) redirect("/login");

  return (
    <>
      <div className="page-head">
        <p className="page-head__eyebrow">Speech to speech</p>
        <h1>Dub</h1>
        <p className="page-head__lede">
          Send a recording and the plane says the same words back in a voice you pick,
          through the engine you can hear. It is not a translation: there is no
          translation model here, so the dub is in the language it heard. The engine&apos;s
          own transcript is shown beside the player, unedited — a dub says what was heard,
          mistakes and all, and you can read exactly what the file will say before you
          keep it.
        </p>
      </div>
      <DubView
        catalogue={voices.status === 200 ? voices.data : null}
        initialDubs={dubs.status === 200 ? dubs.data?.dubs ?? [] : []}
      />
    </>
  );
}