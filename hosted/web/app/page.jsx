import Link from "next/link";
import { redirect } from "next/navigation";

import { cpJson } from "../lib/controlplane.js";
import { formatCount, formatSeconds } from "../lib/format.js";
import { readSessionToken, readWorkspace } from "../lib/session.js";

const TILES = [
  {
    href: "/speak",
    kicker: "Text to speech",
    title: "Speak",
    body: "Pick a voice from the engine's own catalogue and hear the audio come back through the control plane.",
  },
  {
    href: "/voices",
    kicker: "The engine's catalogue",
    title: "Voice library",
    body: "Every voice the plane reports, searchable, with a metered preview of any of them and a one-click hand-off to Speak.",
  },
  {
    href: "/transcribe",
    kicker: "Speech to text",
    title: "Transcribe",
    body: "Send a recording and read the engine's transcript, with the plane's own numbers for the request.",
  },
  {
    href: "/jobs",
    kicker: "The queue",
    title: "Jobs",
    body: "Queue a paragraph, watch it move to done, then play and download the stored audio.",
  },
  {
    href: "/projects",
    kicker: "Long-form",
    title: "Projects",
    body: "Paste a manuscript, let the plane split it into chapters and queue one job each, then download the whole book as one file when every chapter is done.",
  },
  {
    href: "/keys",
    kicker: "The product API",
    title: "API keys",
    body: "Mint a key (shown once), see only its public id afterwards, and revoke it on the next request.",
  },
  {
    href: "/usage",
    kicker: "The meter",
    title: "Usage",
    body: "The workspace totals and the per-key breakdown the plane already records.",
  },
];

async function loadUsage(token) {
  const { status, data } = await cpJson("/v1/usage", { token });
  if (status === 401) return { status, usage: null };
  return { status, usage: data?.usage ?? null };
}

export default async function HomePage() {
  const token = await readSessionToken();

  if (!token) {
    return (
      <>
        <section className="hero">
          <p className="page-head__eyebrow">Hosted QualiaBits Voice</p>
          <h1 className="hero__title">
            A dashboard for the <em>voice</em> plane.
          </h1>
          <p className="hero__lede">
            Sign up for a workspace, then speak text with a voice taken from the
            engine's own catalogue, queue longer pieces of audio or a whole manuscript
            as chapters, transcribe a recording, mint API keys and read the meter the
            plane keeps. Every number on these pages comes from
            the control plane's records, not from this page's own state.
          </p>
          <div className="hero__actions">
            <Link className="btn" href="/signup">
              Create a workspace
            </Link>
            <Link className="btn btn--ghost" href="/login">
              Sign in
            </Link>
          </div>
        </section>

        <div className="grid grid--cards">
          {TILES.map((tile) => (
            <div className="tile" key={tile.href} aria-disabled="true">
              <p className="tile__kicker">{tile.kicker}</p>
              <p className="tile__title">{tile.title}</p>
              <p className="tile__body">{tile.body}</p>
              <p className="subtle" style={{ marginTop: "var(--space-3)", fontSize: "0.82rem" }}>
                Sign in to open this.
              </p>
            </div>
          ))}
        </div>
      </>
    );
  }

  const workspace = await readWorkspace();
  const { status, usage } = await loadUsage(token);
  if (status === 401) redirect("/login");

  return (
    <>
      <div className="page-head">
        <p className="page-head__eyebrow">Workspace</p>
        <h1>{workspace?.display_name || workspace?.email || "Your workspace"}</h1>
        <p className="page-head__lede">
          The things a customer does first. Every page below is one click away.
        </p>
      </div>

      <section className="grid grid--stats" aria-label="Usage at a glance" style={{ marginBottom: "var(--space-6)" }}>
        <div className="stat">
          <p className="stat__label">Requests</p>
          <p className="stat__value">{formatCount(usage?.requests ?? 0)}</p>
          <p className="stat__note">metered by the plane</p>
        </div>
        <div className="stat">
          <p className="stat__label">Characters</p>
          <p className="stat__value">{formatCount(usage?.characters ?? 0)}</p>
          <p className="stat__note">{usage?.characters_note || "text sent to the engine"}</p>
        </div>
        <div className="stat">
          <p className="stat__label">Audio returned</p>
          <p className="stat__value">{formatSeconds(usage?.audio_seconds ?? 0)}</p>
          <p className="stat__note">{usage?.audio_seconds_note || "measured from the returned WAV"}</p>
        </div>
        <div className="stat">
          <p className="stat__label">Failed</p>
          <p className="stat__value">{formatCount(usage?.failed_requests ?? 0)}</p>
          <p className="stat__note">engine refusals and our own errors</p>
        </div>
      </section>

      <div className="grid grid--cards">
        {TILES.map((tile) => (
          <Link className="tile" href={tile.href} key={tile.href}>
            <p className="tile__kicker">{tile.kicker}</p>
            <p className="tile__title">{tile.title}</p>
            <p className="tile__body">{tile.body}</p>
          </Link>
        ))}
      </div>
    </>
  );
}