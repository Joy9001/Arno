/* The service worker adds the push handler. Push payloads are contentless pings: a generic
   sentence and a URL, never a merchant or an amount. */

// TODO(L6): verify this on an installed Android PWA over Tailscale HTTPS. The build, manifest,
// and push handler are tested; the live vendor delivery path is not.
// TODO(L7): the share target is built and the worker handler is tested only by the build. Verify
// a real GPay, PhonePe, or Paytm share into the installed PWA on Android.

import { cleanupOutdatedCaches, precacheAndRoute } from "workbox-precaching";

type NotificationData = { title?: string; body?: string; url?: string };
type PushLike = Event & {
  data: { json(): unknown } | null;
  waitUntil(promise: Promise<unknown>): void;
};
type NotificationLike = Event & {
  notification: { close(): void; data?: { url?: string } };
  waitUntil(promise: Promise<unknown>): void;
};
type ClientLike = { url: string; focus(): Promise<unknown>; navigate(url: string): Promise<unknown> };
type WorkerScope = {
  skipWaiting(): void;
  addEventListener(type: string, handler: (event: Event) => void): void;
  registration: {
    showNotification(title: string, options: Record<string, unknown>): Promise<void>;
  };
  clients: {
    matchAll(options: { type: string; includeUncontrolled: boolean }): Promise<ClientLike[]>;
    openWindow(url: string): Promise<unknown>;
  };
};

declare global {
  interface Window {
    __WB_MANIFEST: Array<{ url: string } | string>;
  }
}

const worker = self as unknown as WorkerScope;

const SHARE_CACHE = "arno-share";
const SHARE_KEY = "/share-target/image";

type FetchLike = Event & {
  request: Request;
  respondWith(response: Response | Promise<Response>): void;
};

worker.skipWaiting();
cleanupOutdatedCaches();
precacheAndRoute(self.__WB_MANIFEST);

worker.addEventListener("fetch", (event: Event) => {
  const request = event as FetchLike;
  const url = new URL(request.request.url);
  if (
    request.request.method !== "POST" ||
    url.origin !== self.location.origin ||
    url.pathname !== "/share-target"
  ) {
    return;
  }
  request.respondWith(
    (async () => {
      try {
        const form = await request.request.formData();
        const files = form
          .getAll("image")
          .filter((value): value is File => value instanceof File);
        const image =
          files.find((file) => file.type.startsWith("image/")) ?? files[0];
        if (!image) return Response.redirect("/?share=empty", 303);
        const cache = await caches.open(SHARE_CACHE);
        await cache.put(
          SHARE_KEY,
          new Response(image, {
            headers: {
              "content-type": image.type || "image/png",
              "x-filename": encodeURIComponent(image.name || "shared-image"),
            },
          }),
        );
        return Response.redirect("/?share=1", 303);
      } catch {
        return Response.redirect("/?share=error", 303);
      }
    })(),
  );
});

worker.addEventListener("push", (event: Event) => {
  const push = event as PushLike;
  let payload: NotificationData = {
    title: "Arno",
    body: "You have a new notification",
    url: "/",
  };
  try {
    if (push.data) payload = { ...payload, ...(push.data.json() as NotificationData) };
  } catch {
    // A malformed payload still shows the generic ping.
  }
  push.waitUntil(
    worker.registration.showNotification(payload.title ?? "Arno", {
      body: payload.body ?? "You have a new notification",
      icon: "/icons/icon-192.png",
      badge: "/icons/icon-192.png",
      data: { url: payload.url ?? "/" },
      tag: "arno-inbox",
      renotify: false,
    }),
  );
});

worker.addEventListener("notificationclick", (event: Event) => {
  const click = event as NotificationLike;
  click.notification.close();
  const target = click.notification.data?.url ?? "/";
  click.waitUntil(
    worker.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      const existing = clients.find((client) => new URL(client.url).origin === self.location.origin);
      if (existing) {
        return existing.focus().then(() => existing.navigate(target));
      }
      return worker.clients.openWindow(target);
    }),
  );
});
