import { startAuthentication, startRegistration } from "@simplewebauthn/browser";
import { api } from "./api";

export type User = {
  id: string;
  display_name: string;
};

export type AuthState = {
  setup_required: boolean;
  authenticated: boolean;
  user: User | null;
};

type RegistrationOptions = Parameters<typeof startRegistration>[0]["optionsJSON"];
type AuthenticationOptions = Parameters<typeof startAuthentication>[0]["optionsJSON"];

type BeginResponse<T> = {
  challenge_id: string;
  options: T;
};

export function getAuthState(): Promise<AuthState> {
  return api<AuthState>("/api/auth/state");
}

/** First-run setup: the setup token from the API log registers the one passkey. */
export async function registerFirstPasskey(token: string): Promise<User> {
  const begin = await api<BeginResponse<RegistrationOptions>>("/api/auth/setup/begin", {
    method: "POST",
    body: JSON.stringify({ token }),
  });
  const credential = await startRegistration({ optionsJSON: begin.options });
  const result = await api<{ user: User }>("/api/auth/setup/verify", {
    method: "POST",
    body: JSON.stringify({ challenge_id: begin.challenge_id, credential }),
  });
  return result.user;
}

export async function loginWithPasskey(): Promise<User> {
  const begin = await api<BeginResponse<AuthenticationOptions>>("/api/auth/login/begin", {
    method: "POST",
  });
  const credential = await startAuthentication({ optionsJSON: begin.options });
  const result = await api<{ user: User }>("/api/auth/login/verify", {
    method: "POST",
    body: JSON.stringify({ challenge_id: begin.challenge_id, credential }),
  });
  return result.user;
}

export async function logout(): Promise<void> {
  await api<void>("/api/auth/logout", { method: "POST" });
}

export type Health = {
  status: string;
  database: string;
  schema_version: number;
};
