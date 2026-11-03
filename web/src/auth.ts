import { startAuthentication, startRegistration } from "@simplewebauthn/browser";
import { api } from "./api";
import type { Enrollment, Passkey, User } from "./types";

export type { User } from "./types";

export type AuthState = {
  setup_required: boolean;
  authenticated: boolean;
  user: User | null;
  rp_origin: string;
};

export type EnrollmentKind = "first_run" | "add_device";

type RegistrationOptions = Parameters<typeof startRegistration>[0]["optionsJSON"];
type AuthenticationOptions = Parameters<typeof startAuthentication>[0]["optionsJSON"];
type RegistrationCredential = Awaited<ReturnType<typeof startRegistration>>;
export type EnrollmentBegin = {
  challenge_id: string;
  options: RegistrationOptions;
  kind: EnrollmentKind;
};

export function getAuthState(): Promise<AuthState> {
  return api<AuthState>("/api/auth/state");
}

/** Start a first-run or add-device ceremony. The server decides which from the token. */
export function beginEnrollment(token: string): Promise<EnrollmentBegin> {
  return api<EnrollmentBegin>("/api/auth/setup/begin", {
    method: "POST",
    body: JSON.stringify({ token }),
  });
}

export function verifyEnrollment(
  challengeId: string,
  credential: RegistrationCredential,
): Promise<{ user: User; kind: EnrollmentKind }> {
  return api<{ user: User; kind: EnrollmentKind }>("/api/auth/setup/verify", {
    method: "POST",
    body: JSON.stringify({ challenge_id: challengeId, credential }),
  });
}

export async function loginWithPasskey(): Promise<User> {
  const begin = await api<{ challenge_id: string; options: AuthenticationOptions }>(
    "/api/auth/login/begin",
    { method: "POST" },
  );
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

let reverifiedUntil = 0;

/**
 * Re-authenticate with a passkey before export or a passkey change. The server checks this, not
 * the client; the timestamp only avoids a second prompt inside the same five-minute window.
 */
export async function ensureFreshPasskey(): Promise<void> {
  if (Date.now() < reverifiedUntil) return;
  const begin = await api<{ challenge_id: string; options: AuthenticationOptions }>(
    "/api/auth/reverify/begin",
    { method: "POST" },
  );
  const credential = await startAuthentication({ optionsJSON: begin.options });
  await api<{ status: string }>("/api/auth/reverify/verify", {
    method: "POST",
    body: JSON.stringify({ challenge_id: begin.challenge_id, credential }),
  });
  reverifiedUntil = Date.now() + 4 * 60 * 1000;
}

export async function logoutAll(): Promise<void> {
  await api<void>("/api/auth/logout-all", { method: "POST" });
}

export async function listPasskeys(): Promise<Passkey[]> {
  const result = await api<{ passkeys: Passkey[] }>("/api/auth/passkeys");
  return result.passkeys;
}

export async function createEnrollment(): Promise<Enrollment> {
  return api<Enrollment>("/api/auth/enrollments", { method: "POST" });
}

export async function renamePasskey(id: string, name: string): Promise<Passkey> {
  const result = await api<{ passkey: Passkey }>(`/api/auth/passkeys/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ name }),
  });
  return result.passkey;
}

export async function deletePasskey(id: string): Promise<void> {
  await api<void>(`/api/auth/passkeys/${id}`, { method: "DELETE" });
}

export type Health = {
  status: string;
  database: string;
  schema_version: number;
};
