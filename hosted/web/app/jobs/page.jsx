import { redirect } from "next/navigation";

import JobsView from "../../components/JobsView.jsx";
import PlaneFailed from "../../components/PlaneFailed.jsx";
import { firstFailure, readPlane } from "../../lib/planeRead.js";
import { readSessionToken } from "../../lib/session.js";

export const metadata = { title: "Jobs — QualiaBits Voice" };

export default async function JobsPage() {
  const token = await readSessionToken();
  if (!token) redirect("/login");

  const [voices, jobs] = await Promise.all([
    readPlane("/v1/voices", { token }),
    readPlane("/v1/jobs?limit=50", { token }),
  ]);
  if (voices.status === 401 || jobs.status === 401) redirect("/login");
  const failure = firstFailure(voices, jobs);

  return (
    <>
      <div className="page-head">
        <p className="page-head__eyebrow">The queue</p>
        <h1>Jobs</h1>
        <p className="page-head__lede">
          A paragraph is too long for one request. Queue it here, a worker process speaks
          it and stores the audio, and this page plays back exactly what the plane kept.
        </p>
      </div>
      {failure ? (
        <PlaneFailed error={failure.error} />
      ) : (
        <JobsView
          catalogue={voices.data}
          initialJobs={jobs.data?.jobs ?? []}
        />
      )}
    </>
  );
}