import { redirect } from "next/navigation";

import ProjectsView from "../../components/ProjectsView.jsx";
import { cpJson } from "../../lib/controlplane.js";
import { readSessionToken } from "../../lib/session.js";

export const metadata = { title: "Projects — QualiaBits Voice" };

/**
 * The long-form page.
 *
 * The manuscript limits come from the plane's own `/healthz`, not from a constant
 * here: a ceiling written down twice is a ceiling that drifts, and the composer's
 * refusal has to quote the number the plane would refuse on. When `/healthz`
 * cannot be read, the page forwards the manuscript and lets the plane do the
 * refusing — the same fallback the transcription page uses for its upload ceiling.
 */
export default async function ProjectsPage() {
  const token = await readSessionToken();
  if (!token) redirect("/login");

  const [voices, projects, health] = await Promise.all([
    cpJson("/v1/voices", { token }),
    cpJson("/v1/projects?limit=20", { token }),
    cpJson("/healthz"),
  ]);
  if (voices.status === 401 || projects.status === 401) redirect("/login");

  const limits = health.status === 200 ? health.data : null;

  return (
    <>
      <div className="page-head">
        <p className="page-head__eyebrow">Long-form</p>
        <h1>Projects</h1>
        <p className="page-head__lede">
          A book is longer than one request. Paste the manuscript, and the plane splits it
          into chapters at paragraph, sentence and word boundaries, queues one job per
          chapter, and joins the chapters the worker really spoke into a single WAV you
          can download. Every chapter is metered the same way the queue meters a job.
        </p>
      </div>
      <ProjectsView
        catalogue={voices.status === 200 ? voices.data : null}
        initialProjects={projects.status === 200 ? projects.data?.projects ?? [] : []}
        limits={limits}
      />
    </>
  );
}