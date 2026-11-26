import { api } from "./api";

export type { User } from "./types";

export type AuthState = {
  authenticated: boolean;
  method: "tailscale" | "passphrase" | null;
  login: string | null;
  passphrase_enabled: boolean;
};

export function getAuthState(): Promise<AuthState> {
  return api<AuthState>("/api/auth/state");
}

export function login(passphrase: string): Promise<void> {
  return api<void>("/api/auth/login", { method: "POST", body: JSON.stringify({ passphrase }) });
}

export function logout(): Promise<void> {
  return api<void>("/api/auth/logout", { method: "POST" });
}

export type Health = {
  status: string;
  database: string;
  schema_version: number;
};
