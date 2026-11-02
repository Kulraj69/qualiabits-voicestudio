import { redirect } from "next/navigation";

import TranscribeView from "../../components/TranscribeView.jsx";
import { readSessionToken } from "../../lib/session.js";

export const metadata = { title: "Transcribe — QualiaBits Voice" };

/**
 * The page itself holds no data to fetch: the transcription is a request the
 * customer makes, not something already on the server. It still checks the
 * session, so a signed-out visitor is sent to the login page rather than to a
 * form that can only answer 401.
 */
export default async function TranscribePage() {
  const token = await readSessionToken();
  if (!token) redirect("/login");

  return (
    <>
      <div className="page-head">
        <p className="page-head__eyebrow">Speech to text</p>
        <h1>Transcribe</h1>
        <p className="page-head__lede">
          Send a recording and get the engine's transcript back, with the plane's own
          numbers beside it. This is the reverse of the Speak page, and the meter counts
          it separately: text the engine returns is not text you asked it to speak.
        </p>
      </div>
      <TranscribeView />
    </>
  );
}