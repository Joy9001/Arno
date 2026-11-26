/* The service worker precaches the app and receives shared images. */

// TODO(L7): the share target is built and the worker handler is tested only by the build. Verify
// a real GPay, PhonePe, or Paytm share into the installed PWA on Android.

import { cleanupOutdatedCaches, precacheAndRoute } from "workbox-precaching";

type WorkerScope = {
  skipWaiting(): void;
  addEventListener(type: string, handler: (event: Event) => void): void;
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
