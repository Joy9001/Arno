import {
  useContext,
  useEffect,
  useRef,
  useState,
  type PropsWithChildren,
} from "react";
import { APICallError } from "ai";
import { Calculator, FileText, Flag, ReceiptText, Wallet } from "lucide-react";
import {
  useAssistantDataUI,
  useAui,
  useAuiEvent,
  useAuiState,
  type ToolCallMessagePartComponent,
} from "@assistant-ui/react";
import { useAISDKError } from "@assistant-ui/ai-sdk";
import { ApprovalCard } from "@/components/approval-card";
import { ImagePreview, ImageRoot, ImageZoom } from "@/components/image";
import { Button } from "@/components/ui/button";
import {
  ToolGroupContent,
  ToolGroupRoot,
  ToolGroupTrigger,
} from "@/components/tool-group.aui";
import { ToolFallback } from "@/components/tool-fallback.aui";
import { Card, NumberFlags } from "./cards";
import { ChatSurfaceContext, chatErrorMessage } from "./surface";
import type { ThreadGroupPart } from "@/components/thread.aui";

function useMessageId(): string {
  return useAuiState((state) => state.message.id);
}

function CardData({ data }: { data: Record<string, unknown> }) {
  const messageId = useMessageId();
  const surface = useContext(ChatSurfaceContext);
  return (
    <Card
      data={data}
      onRetryCapture={(kind, attachmentId) =>
        surface.onRetryCapture?.(kind, attachmentId, messageId)
      }
      onEvidence={surface.onEvidence}
    />
  );
}

function ReplyModelData({ data }: { data: { model: string; fallback: boolean } }) {
  return (
    <p className="aui-part-note">
      Model: {data.model}
      {data.fallback ? " (fallback)" : ""}
    </p>
  );
}

function LegacyModelBadge({ data }: { data: { model: string } }) {
  // New saves carry data-reply-model with the model that really answered; the
  // old badge is only kept for transcripts saved before that part existed.
  const hasReplyModel = useAuiState((state) =>
    state.message.parts.some(
      (part) => part.type === "data" && part.name === "reply-model",
    ),
  );
  if (hasReplyModel) return null;
  return <p className="aui-part-note">Answered by the backup model ({data.model}).</p>;
}

function EndingData({ data }: { data: { note: string } }) {
  return data.note ? <p className="aui-part-note">{data.note}</p> : null;
}

function NumberFlagsData({ data }: { data: { amounts: string[] } }) {
  return <NumberFlags amounts={data.amounts} />;
}

function StatementData({
  data,
}: {
  data: { filename?: string; attachment_id?: string };
}) {
  return (
    <span className="aui-statement-chip">
      <FileText size={14} strokeWidth={1.8} aria-hidden="true" />
      <span>{data.filename || "Statement"}</span>
    </span>
  );
}

function PhotoData({
  data,
}: {
  data: { filename?: string; attachment_id?: string };
}) {
  if (!data.attachment_id) return null;
  const src = `/api/attachments/${encodeURIComponent(data.attachment_id)}`;
  const name = data.filename || "Photo";
  return (
    <div className="aui-photo-attachment">
      <ImageRoot variant="outline" size="sm" className="size-14">
        <ImageZoom src={src} alt={name}>
          <ImagePreview
            src={src}
            alt={name}
            ratio="1:1"
            fit="cover"
            containerClassName="size-14"
          />
        </ImageZoom>
      </ImageRoot>
    </div>
  );
}

/** Registers the app's saved data parts on the runtime's own renderer registry. */
export function ChatDataRenderers() {
  useAssistantDataUI({ name: "card", render: CardData });
  useAssistantDataUI({ name: "number-flags", render: NumberFlagsData });
  useAssistantDataUI({ name: "model-badge", render: LegacyModelBadge });
  useAssistantDataUI({ name: "reply-model", render: ReplyModelData });
  useAssistantDataUI({ name: "ending", render: EndingData });
  useAssistantDataUI({ name: "statement", render: StatementData });
  useAssistantDataUI({ name: "photo", render: PhotoData });
  return null;
}

function prettyToolName(name: string): string {
  return name.replaceAll("_", " ");
}

function approvalDetails(args: unknown): { label: string; value: string }[] {
  if (!args || typeof args !== "object") return [];
  return Object.entries(args as Record<string, unknown>)
    .slice(0, 8)
    .map(([label, value]) => ({
      label: label.replaceAll("_", " "),
      value:
        typeof value === "object" && value !== null
          ? JSON.stringify(value)
          : String(value),
    }));
}

/** A pending approval must stay visible even when its run is folded. */
export function ChatToolGroup({
  group,
  children,
}: PropsWithChildren<{ group: ThreadGroupPart }>) {
  const pendingApproval = useAuiState((state) =>
    group.indices.some((index) => {
      const part = state.message.parts[index];
      return (
        part?.type === "tool-call" &&
        part.approval !== undefined &&
        part.approval.approved === undefined &&
        part.approval.resolution === undefined &&
        part.approval.isAutomatic !== true
      );
    }),
  );
  const [open, setOpen] = useState(false);

  return (
    <ToolGroupRoot
      variant="ghost"
      open={open || pendingApproval}
      onOpenChange={setOpen}
    >
      <ToolGroupTrigger
        count={group.indices.length}
        active={group.status.type === "running"}
      />
      <ToolGroupContent>{children}</ToolGroupContent>
    </ToolGroupRoot>
  );
}

/**
 * A pending approval uses the kit's ApprovalCard. Everything else, including
 * the settled receipt and the raw input/output, stays on the kit's
 * ToolFallback. The server has no "always allow", so neither does this card.
 */
export const ChatToolFallback: ToolCallMessagePartComponent = (props) => {
  const { approval, respondToApproval, args, toolName, status } = props;
  const [error, setError] = useState<string | null>(null);
  const [answering, setAnswering] = useState(false);
  const answeringRef = useRef(false);

  if (
    approval &&
    approval.approved === undefined &&
    approval.resolution === undefined &&
    !approval.isAutomatic
  ) {
    const answer = async (approved: boolean) => {
      // A double click must not queue a second continuation.
      if (answeringRef.current) return;
      answeringRef.current = true;
      setAnswering(true);
      setError(null);
      try {
        await respondToApproval({ approved });
      } catch (caught) {
        answeringRef.current = false;
        setAnswering(false);
        setError(caught instanceof Error ? caught.message : "That answer was not accepted.");
      }
    };

    return (
      <div className="flex flex-col gap-1.5">
        <ApprovalCard
          state="request"
          icon={<Flag className="size-4" />}
          title={`Confirm ${prettyToolName(toolName)}`}
          subtitle={approval.prompt ?? "This tool changes your records"}
          description="This change is recorded with undo. Confirm it to run the tool."
          details={approvalDetails(args)}
          busy={answering}
          allowOnceLabel={answering ? "Confirming…" : "Confirm"}
          denyLabel="Cancel"
          onAllowOnce={() => void answer(true)}
          onDeny={() => void answer(false)}
        />
        {error && (
          <p role="alert" className="text-destructive text-xs">
            {error}
          </p>
        )}
        {status.type === "incomplete" && status.reason === "error" && (
          <p className="text-destructive text-xs">The tool did not finish.</p>
        )}
      </div>
    );
  }

  return <ToolFallback {...props} />;
};

const STARTERS = [
  { Icon: ReceiptText, text: "How much did I spend on food last month?" },
  { Icon: Calculator, text: "Can I afford a ₹40,000 phone in December?" },
  { Icon: Wallet, text: "What is due in the next 30 days?" },
];

/** The thread's new-chat slot, wired to the runtime's composer. */
export function ChatWelcome() {
  const aui = useAui();

  const start = (text: string) => {
    aui.composer.setText(text);
    requestAnimationFrame(() => {
      document.querySelector<HTMLTextAreaElement>(".aui-composer-input")?.focus();
    });
  };

  return (
    <div className="aui-welcome">
      <h3>Ask anything about your money.</h3>
      <p className="aui-welcome-copy">
        Answers use your ledger, plan, and projection. Every figure comes with its sources.
      </p>
      <div className="aui-starters">
        {STARTERS.map(({ Icon, text }) => (
          <button
            key={text}
            type="button"
            className="aui-starter"
            onClick={() => start(text)}
          >
            <Icon size={16} strokeWidth={1.8} aria-hidden="true" />
            <span>{text}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

/** Error recovery and capture status, directly beneath the composer. */
export function ChatComposerStatus() {
  const error = useAISDKError();
  const aui = useAui();
  const surface = useContext(ChatSurfaceContext);
  const [addError, setAddError] = useState<string | null>(null);
  const needsSignIn = APICallError.isInstance(error) && error.statusCode === 401;

  useAuiEvent("composer.attachmentAddError", ({ reason, message }) => {
    setAddError(
      reason === "not-accepted"
        ? "Attach a PDF, CSV, spreadsheet, or PNG/JPEG/WebP/GIF photo."
        : message || "That file could not be attached.",
    );
  });

  useEffect(() => {
    if (!addError) return;
    const handle = window.setTimeout(() => setAddError(null), 8000);
    return () => window.clearTimeout(handle);
  }, [addError]);

  if (!error && !surface.capture && !addError) return null;

  const retry = () => {
    aui.thread.append({
      role: "user",
      content: [{ type: "text", text: "Try again" }],
      startRun: true,
    });
  };

  return (
    <div className="aui-composer-status" aria-live="polite">
      {surface.capture && (
        <p
          role={surface.capture.kind === "error" ? "alert" : "status"}
          className={
            surface.capture.kind === "error"
              ? "aui-status-line aui-status-error"
              : "aui-status-line"
          }
        >
          {surface.capture.message}
        </p>
      )}
      {addError && (
        <p role="alert" className="aui-status-line aui-status-error">
          {addError}
        </p>
      )}
      {error && (
        <div className="aui-status-line aui-status-error" role="alert">
          <span>{chatErrorMessage(error)}</span>
          <Button
            type="button"
            variant="link"
            size="xs"
            className="aui-status-action h-auto"
            onClick={() => {
              if (needsSignIn) window.location.reload();
              else retry();
            }}
          >
            {needsSignIn ? "Sign in again" : "Retry"}
          </Button>
        </div>
      )}
    </div>
  );
}
