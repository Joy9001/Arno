import {
  createResumableSessionStorage,
  type ResumableClientStorage,
} from "@assistant-ui/ai-sdk";

const STREAM_KEY = "arno-stream:";

type ThreadIdentity = { remoteId?: string | null; id?: string | null };

/**
 * One storage instance per thread runtime, with the key read lazily so a new
 * thread's local id becomes the server id without writing into another
 * conversation's checkpoint.
 */
export function createThreadStreamStorage(
  getThread: () => ThreadIdentity | undefined,
): ResumableClientStorage {
  return createResumableSessionStorage({
    key: () => {
      const item = getThread();
      const id = item?.remoteId ?? item?.id;
      return id ? `${STREAM_KEY}${id}` : undefined;
    },
  });
}

/**
 * An explicit Stop must clear the checkpoint held by the thread's own storage
 * instance. Keeping the instance (not just its key) is what lets the library's
 * in-memory owner cache see the clear.
 */
export class StreamCheckpoints {
  private readonly byThread = new Map<string, ResumableClientStorage>();

  set(threadId: string, storage: ResumableClientStorage) {
    this.byThread.set(threadId, storage);
  }

  clear(threadId: string) {
    this.byThread.get(threadId)?.clear();
  }

  release(threadId: string, storage: ResumableClientStorage) {
    if (this.byThread.get(threadId) === storage) this.byThread.delete(threadId);
  }
}
