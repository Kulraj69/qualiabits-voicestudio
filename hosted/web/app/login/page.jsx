import { redirect } from "next/navigation";

import AuthForm from "../../components/AuthForm.jsx";
import { readSessionToken } from "../../lib/session.js";

export const metadata = { title: "Sign in — QualiaBits Voice" };

export default async function LoginPage() {
  if (await readSessionToken()) redirect("/");
  return <AuthForm mode="login" />;
}