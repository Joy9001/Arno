import {
  type AttachmentAdapter,
  type CompleteAttachment,
  type PendingAttachment,
} from "@assistant-ui/react";
import { generateId } from "ai";
import { api, errorMessage } from "../api";

const MAX_BYTES = 25 * 1024 * 1024;
const EXTENSIONS = [".png", ".jpg", ".jpeg", ".webp", ".gif"];
const MIME_TYPES = new Set(["image/png", "image/jpeg", "image/webp", "image/gif"]);

export const PHOTO_ACCEPT = [...EXTENSIONS, ...MIME_TYPES].join(",");

type Uploaded = {
  id: string;
  filename: string;
  kind: string;
};

function validationError(file: File): string | null {
  if (file.size === 0) return "That image is empty. Choose a receipt or payment photo.";
  if (file.size > MAX_BYTES) return "That image is over the 25 MB photo limit.";
  const name = file.name.toLowerCase();
  const supported =
    EXTENSIONS.some((extension) => name.endsWith(extension)) ||
    MIME_TYPES.has(file.type.split(";", 1)[0].trim().toLowerCase());
  if (!supported) return "Choose a PNG, JPEG, WebP, or GIF receipt or payment photo.";
  return null;
}

function pending(file: File): PendingAttachment {
  const problem = validationError(file);
  return {
    id: generateId(),
    type: "image",
    name: file.name,
    contentType: file.type,
    file,
    content: [],
    status: problem
      ? { type: "incomplete", reason: "error", message: problem }
      : { type: "requires-action", reason: "composer-send" },
  };
}

/**
 * Stages the image only when the composer sends. The chat turn then runs the
 * one server capture path and returns the receipt card, exactly once.
 */
export const photoAttachmentAdapter: AttachmentAdapter = {
  accept: PHOTO_ACCEPT,

  async add({ file }) {
    return pending(file);
  },

  async send(attachment, options): Promise<CompleteAttachment> {
    const form = new FormData();
    form.append("file", attachment.file, attachment.file.name);
    form.append("purpose", "photo");
    let uploaded: Uploaded;
    try {
      uploaded = await api<Uploaded>("/api/attachments", {
        method: "POST",
        body: form,
        signal: options?.signal,
      });
    } catch (caught) {
      throw new Error(errorMessage(caught));
    }
    if (uploaded.kind !== "photo") {
      throw new Error("That file is not a readable PNG, JPEG, WebP, or GIF image.");
    }
    return {
      ...attachment,
      status: { type: "complete" },
      content: [
        {
          type: "data",
          name: "photo",
          data: {
            attachment_id: uploaded.id,
            filename: uploaded.filename,
          },
        },
      ],
    };
  },

  async remove() {
    // The upload happens on send; there is nothing on the server to remove yet.
  },
};
