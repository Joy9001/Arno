/* The service worker stores a shared UPI screenshot in the Cache API, then redirects here.
   The chat screen consumes it once and posts it to the photo endpoint. */

const SHARE_CACHE = "fin-agent-share";
const SHARE_KEY = "/share-target/image";

let pending: Promise<File | null> | null = null;

async function readSharedImage(): Promise<File | null> {
  if (typeof caches === "undefined") return null;
  const cache = await caches.open(SHARE_CACHE);
  const hit = await cache.match(SHARE_KEY);
  if (!hit) return null;
  const blob = await hit.blob();
  const rawName = hit.headers.get("x-filename") ?? "shared-image";
  let name = rawName;
  try {
    name = decodeURIComponent(rawName);
  } catch {
    // Keep the raw name if it wasn't encoded.
  }
  await cache.delete(SHARE_KEY);
  return new File([blob], name || "shared-image", {
    type: blob.type || "image/png",
  });
}

/** One read for a page load; React StrictMode can call it twice without consuming twice. */
export function takeSharedImage(): Promise<File | null> {
  if (!pending) {
    pending = readSharedImage()
      .catch(() => null)
      .finally(() => {
        pending = null;
      });
  }
  return pending;
}
