import {
  type AttachmentAdapter,
  type CompleteAttachment,
  type PendingAttachment,
} from "@assistant-ui/react";
import { generateId } from "ai";
import { api, errorMessage } from "../api";

const MAX_BYTES = 25 * 1024 * 1024;
const EXTENSIONS = [".pdf", ".csv", ".tsv", ".xlsx", ".xls", ".xlsb", ".ods"];
const MIME_TYPES = new Set([
  "application/pdf",
  "text/csv",
  "text/tab-separated-values",
  "application/vnd.ms-excel",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "application/vnd.ms-excel.sheet.binary.macroEnabled.12",
  "application/vnd.oasis.opendocument.spreadsheet",
]);
const KINDS = new Set(["pdf", "text", "xlsx", "xls", "xlsb", "ods", "encrypted_ooxml"]);

export const STATEMENT_ACCEPT = [
  ...EXTENSIONS,
  ...MIME_TYPES,
].join(",");

type Uploaded = {
  id: string;
  filename: string;
  detected_kind: string;
};

function validationError(file: File): string | null {
  if (file.size === 0) return "That file is empty. Choose a statement with data in it.";
  if (file.size > MAX_BYTES) return "That file is over the 25 MB statement limit.";
  const name = file.name.toLowerCase();
  const supported =
    EXTENSIONS.some((extension) => name.endsWith(extension)) ||
    MIME_TYPES.has(file.type.split(";", 1)[0].trim().toLowerCase());
  if (!supported) return "Choose a PDF, CSV, or spreadsheet statement (XLSX, XLSB, ODS, XLS).";
  return null;
}

function pending(file: File): PendingAttachment {
  const problem = validationError(file);
  return {
    id: generateId(),
    type: "document",
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
 * Uploads only when the composer sends. The completed attachment carries an app
 * reference, never the document bytes, so the model and the browser cloud never
 * receive the file body.
 */
export const statementAttachmentAdapter: AttachmentAdapter = {
  accept: STATEMENT_ACCEPT,

  async add({ file }) {
    return pending(file);
  },

  async send(attachment, options): Promise<CompleteAttachment> {
    const form = new FormData();
    form.append("file", attachment.file, attachment.file.name);
    form.append("purpose", "statement");
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
    if (!KINDS.has(uploaded.detected_kind)) {
      throw new Error("That file is not a readable PDF, CSV, or spreadsheet statement.");
    }
    return {
      ...attachment,
      status: { type: "complete" },
      content: [
        {
          type: "data",
          name: "statement",
          data: {
            attachment_id: uploaded.id,
            filename: uploaded.filename,
            detected_kind: uploaded.detected_kind,
          },
        },
      ],
    };
  },

  async remove() {
    // The upload happens on send; there is nothing on the server to remove yet.
  },
};
