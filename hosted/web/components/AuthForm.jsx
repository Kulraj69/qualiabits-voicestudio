"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

/**
 * The sign-up / sign-in form.
 *
 * Posts to the app's own route, which is what talks to the control plane; the
 * browser never sees the session token, only the cookie the route sets.
 */
export default function AuthForm({ mode }) {
  const router = useRouter();
  const isSignup = mode === "signup";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      const response = await fetch(`/api/auth/${isSignup ? "signup" : "login"}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          email,
          password,
          ...(isSignup ? { display_name: displayName } : {}),
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        setError(data?.detail || "Something went wrong. Try again.");
        return;
      }
      router.push("/");
      router.refresh();
    } catch {
      setError("The dashboard could not reach its own server.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card" style={{ maxWidth: 460 }}>
      <div className="page-head" style={{ marginBottom: "var(--space-5)" }}>
        <p className="page-head__eyebrow">{isSignup ? "New workspace" : "Welcome back"}</p>
        <h1 style={{ fontSize: "1.6rem" }}>{isSignup ? "Create a workspace" : "Sign in"}</h1>
        <p className="page-head__lede">
          {isSignup
            ? "One workspace per customer. The password is hashed by the control plane; the dashboard keeps only a session cookie."
            : "Your email and password go straight to the control plane. The dashboard stores the session in an httpOnly cookie."}
        </p>
      </div>

      <form className="stack" onSubmit={submit} noValidate>
        {isSignup ? (
          <label className="field">
            <span className="field__label">Display name</span>
            <input
              type="text"
              name="display_name"
              autoComplete="name"
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
              placeholder="optional"
            />
          </label>
        ) : null}

        <label className="field">
          <span className="field__label">Email</span>
          <input
            type="email"
            name="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="you@example.com"
          />
        </label>

        <label className="field">
          <span className="field__label">Password</span>
          <input
            type="password"
            name="password"
            autoComplete={isSignup ? "new-password" : "current-password"}
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
          {isSignup ? (
            <span className="field__hint">At least 12 characters — the plane's rule.</span>
          ) : null}
        </label>

        {error ? (
          <p className="notice notice--error" role="alert">
            {error}
          </p>
        ) : null}

        <button className="btn" type="submit" disabled={busy}>
          {busy ? "Working…" : isSignup ? "Create workspace" : "Sign in"}
        </button>
      </form>

      <p className="card__hint" style={{ marginTop: "var(--space-4)" }}>
        {isSignup ? (
          <>
            Already have a workspace? <Link href="/login">Sign in</Link>.
          </>
        ) : (
          <>
            No workspace yet? <Link href="/signup">Create one</Link>.
          </>
        )}
      </p>
    </div>
  );
}