import { redirect } from "next/navigation";

import AuthForm from "../../components/AuthForm.jsx";
import { readSessionToken } from "../../lib/session.js";

export const metadata = { title: "Create a workspace — QualiaBits Voice" };

export default async function SignupPage() {
  if (await readSessionToken()) redirect("/");
  return <AuthForm mode="signup" />;
}