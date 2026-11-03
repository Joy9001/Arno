import { api } from "./api";
import type { PushKey } from "./types";

function urlBase64ToUint8Array(value: string): Uint8Array {
  const padding = "=".repeat((4 - (value.length % 4)) % 4);
  const base64 = (value + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = window.atob(base64);
  const bytes = new Uint8Array(raw.length);
  for (let index = 0; index < raw.length; index += 1) {
    bytes[index] = raw.charCodeAt(index);
  }
  return bytes;
}

export function pushSupported(): boolean {
  return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

export async function pushPermission(): Promise<NotificationPermission | "unsupported"> {
  return pushSupported() ? Notification.permission : "unsupported";
}

async function saveSubscription(subscription: PushSubscription): Promise<void> {
  const json = subscription.toJSON();
  await api("/api/push/subscribe", {
    method: "POST",
    body: JSON.stringify({
      endpoint: json.endpoint,
      keys: { p256dh: json.keys?.p256dh, auth: json.keys?.auth },
    }),
  });
}

export async function enablePush(): Promise<NotificationPermission | "unsupported"> {
  if (!pushSupported()) return "unsupported";
  const permission = await Notification.requestPermission();
  if (permission !== "granted") return permission;
  const registration = await navigator.serviceWorker.ready;
  const key = await api<PushKey>("/api/push/key");
  const existing = await registration.pushManager.getSubscription();
  if (existing) {
    await saveSubscription(existing);
    return permission;
  }
  const subscription = await registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(key.public_key).buffer as ArrayBuffer,
  });
  await saveSubscription(subscription);
  return permission;
}

export async function disablePush(): Promise<boolean> {
  if (!pushSupported()) return false;
  const registration = await navigator.serviceWorker.ready;
  const existing = await registration.pushManager.getSubscription();
  if (!existing) return false;
  await api("/api/push/subscribe", {
    method: "DELETE",
    body: JSON.stringify({ endpoint: existing.endpoint }),
  });
  await existing.unsubscribe();
  return true;
}
