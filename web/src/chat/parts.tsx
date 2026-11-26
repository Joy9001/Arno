import { AskBelow, AskGreeting } from "./AskHome";
import { createElement, useContext, useEffect, useRef, useState } from "react";
import { APICallError } from "ai";
import {
  Calculator,
  FileText,
  Flag,
  LogIn,
  ReceiptText,
  RotateCcw,
  Wallet,
} from "lucide-react";
import {
  SuggestionPrimitive,
  ThreadPrimitive,
  useAssistantDataUI,
  useAui,
  useAuiEvent,
  useAuiState,
  type ToolCallMessagePartComponent,
} from "@assistant-ui/react";
import { useAISDKError } from "@assistant-ui/ai-sdk";
import { ImagePreview, ImageRoot, ImageZoom } from "@/components/image";
import { Button } from "@/components/ui/button";
import { ToolFallback } from "@/components/tool-fallback.aui";
import { CaptureCard, NumberFlags } from "./capture-card";
import { ChatSurfaceContext, chatErrorMessage } from "./surface";
import { ActionConfirmCard, ActionReceipt, AffordabilityLab, ImportReviewWidget } from "./widgets";
import { parseProjectionReceipt } from "../projectionReceipt";

function useMessageId(): string {
  return useAuiState((state) => state.message.id);
}

function usePartIndex(): number | null {
  const part = useAui().part as unknown as {
    query?: { type: string; index?: number };
  };
  const query = part.query;
  return query?.type === "index" && typeof query.index === "number"
    ? query.index
    : null;
}

function CardData({ data }: { data: Record<string, unknown> }) {
  const messageId = useMessageId();
  const surface = useContext(ChatSurfaceContext);
  const card = String(data.card ?? "");
  const projection =
    card === "projection" ? parseProjectionReceipt(data) : null;
  if (projection) return <AffordabilityLab seed={projection} />;
  if (card === "import" && typeof data.import_id === "string")
    return <ImportReviewWidget importId={data.import_id} />;
  if (
    [
      "movements_batch",
      "movement",
      "correction",
      "undo",
      "preference",
      "setup",
    ].includes(card)
  )
    return <ActionReceipt data={data} />;
  if (card === "capture" || card === "capture-retry")
    return (
      <CaptureCard
        data={data}
        onRetry={(kind, attachmentId) =>
          surface.onRetryCapture?.(kind, attachmentId, messageId)
        }
      />
    );
  return null;
}

function ReplyModelData({
  data,
}: {
  data: { model: string; fallback: boolean };
}) {
  // Each run saved into one message (a delegate report, a retry) adds its own part; the
  // last one is the model that answered, so only it shows.
  const parts = useAuiState((state) => state.message.parts);
  const index = usePartIndex();
  const last = parts.findLastIndex(
    (part) => part.type === "data" && part.name === "reply-model",
  );
  const lastPart = parts[last];
  const isLast =
    index === null
      ? lastPart?.type === "data" && lastPart.data === data
      : index === last;
  if (!isLast) return null;
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
  return (
    <p className="aui-part-note">
      Answered by the backup model ({data.model}).
    </p>
  );
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
  data: { filename?: string; attachment_id?: string; import_id?: string };
}) {
  // The upload opened an import; its card polls the status and asks for any password.
  if (data.import_id) {
    return <ImportReviewWidget importId={data.import_id} />;
  }
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

/**
 * A pending approval uses the kit's ApprovalCard. Everything else, including
 * the settled receipt and the raw input/output, stays on the kit's
 * ToolFallback. The server has no "always allow", so neither does this card.
 */
export const ChatToolFallback: ToolCallMessagePartComponent = (props) => {
  const { approval, respondToApproval, args, toolName, status } = props;
  const surface = useContext(ChatSurfaceContext);
  const input =
    args && typeof args === "object" ? (args as Record<string, unknown>) : {};
  const changes =
    input.changes && typeof input.changes === "object"
      ? (input.changes as Record<string, unknown>)
      : {};
  const data =
    input.data && typeof input.data === "object"
      ? (input.data as Record<string, unknown>)
      : {};
  const categoryEditable =
    toolName === "correct_movement" && typeof changes.category === "string";
  const floorEditable =
    toolName === "update_setup" &&
    input.entity === "settings" &&
    typeof data.floor_rupees === "number";
  const [category, setCategory] = useState(
    categoryEditable ? String(changes.category) : "",
  );
  const [floor, setFloor] = useState(
    floorEditable ? String(data.floor_rupees) : "",
  );
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
        if (approved && categoryEditable) {
          if (!category.trim()) {
            setError("Enter a category.");
            answeringRef.current = false;
            setAnswering(false);
            return;
          }
          if (category.trim() !== String(changes.category).trim()) {
            surface.onApprovalEdit?.(props.toolCallId, {
              category: category.trim(),
            });
          }
        }
        if (approved && floorEditable) {
          const value = Number(floor);
          if (
            !floor.trim() ||
            !Number.isFinite(value) ||
            value < 0 ||
            !/^\d+(?:\.\d{1,2})?$/.test(floor)
          ) {
            setError(
              "Enter a valid floor in rupees, with up to two decimal places.",
            );
            answeringRef.current = false;
            setAnswering(false);
            return;
          }
          if (value !== data.floor_rupees) {
            surface.onApprovalEdit?.(props.toolCallId, { floor_rupees: value });
          }
        }
        await respondToApproval({ approved });
      } catch (caught) {
        answeringRef.current = false;
        setAnswering(false);
        setError(
          caught instanceof Error
            ? caught.message
            : "That answer was not accepted.",
        );
      }
    };

    return (
      <div className="flex flex-col gap-1.5">
        {categoryEditable && (
          <label className="text-sm">
            Category
            <input
              className="ml-2 rounded border border-white/20 bg-transparent p-2"
              value={category}
              onChange={(event) => setCategory(event.target.value)}
            />
          </label>
        )}
        {floorEditable && (
          <label className="text-sm">
            Floor (₹)
            <input
              className="ml-2 rounded border border-white/20 bg-transparent p-2"
              inputMode="decimal"
              value={floor}
              onChange={(event) => setFloor(event.target.value)}
            />
          </label>
        )}
        <ActionConfirmCard
          state="request"
          icon={<Flag className="size-4" />}
          title={`Confirm ${prettyToolName(toolName)}`}
          subtitle={approval.prompt ?? "This tool changes your records"}
          description="Review this request before allowing the tool to run."
          details={approvalDetails({
            ...input,
            ...(categoryEditable ? { changes: { ...changes, category } } : {}),
            ...(floorEditable
              ? { data: { ...data, floor_rupees: floor } }
              : {}),
          })}
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
] as const;

/** The prompts live once here; the runtime config and the welcome both read them. */
export const FIN_SUGGESTIONS = STARTERS.map(({ text }) => ({
  title: text,
  label: "",
  prompt: text,
}));

const STARTER_ICONS = new Map<string, (typeof STARTERS)[number]["Icon"]>(
  STARTERS.map(({ Icon, text }) => [text, Icon]),
);

type StarterSuggestionState = {
  title: string;
  label: string;
  prompt: string;
};

const renderStarter = ({
  suggestion,
}: {
  suggestion: StarterSuggestionState;
}) => <StarterSuggestion suggestion={suggestion} />;

function StarterSuggestion({
  suggestion,
}: {
  suggestion: StarterSuggestionState;
}) {
  const Icon = STARTER_ICONS.get(suggestion.prompt);

  return (
    <SuggestionPrimitive.Trigger asChild>
      <button type="button" className="aui-starter">
        {Icon &&
          createElement(Icon, {
            size: 16,
            strokeWidth: 1.8,
            "aria-hidden": true,
          })}
        <span className="aui-starter-copy">
          <SuggestionPrimitive.Title className="aui-starter-title" />
          <SuggestionPrimitive.Description className="aui-starter-description empty:hidden" />
        </span>
      </button>
    </SuggestionPrimitive.Trigger>
  );
}

/** The thread's new-chat slot: the agent's home. Starter prompts fill the composer for review. */
export function ChatWelcome() {
  return (
    <div className="aui-welcome">
      <AskGreeting />
    </div>
  );
}

/** Under the composer on a new conversation: starters, then what the agent noticed. */
export function ChatWelcomeAfter() {
  return (
    <AskBelow
      starters={
        <div className="aui-starters">
          <ThreadPrimitive.Suggestions>
            {renderStarter}
          </ThreadPrimitive.Suggestions>
        </div>
      }
    />
  );
}

/** Error recovery and capture status, directly beneath the composer. */
export function ChatComposerStatus() {
  const error = useAISDKError();
  const canResume = useAuiState((state) => state.thread.canResume);
  const aui = useAui();
  const surface = useContext(ChatSurfaceContext);
  const [addError, setAddError] = useState<string | null>(null);
  const needsSignIn =
    APICallError.isInstance(error) && error.statusCode === 401;

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
          <span>
            {canResume && !needsSignIn
              ? "The running answer could not be reconnected. Resume to try again."
              : chatErrorMessage(error)}
          </span>
          {(!canResume || needsSignIn) && (
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
              {needsSignIn ? (
                <LogIn aria-hidden="true" />
              ) : (
                <RotateCcw aria-hidden="true" />
              )}
              {needsSignIn ? "Sign in again" : "Retry"}
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
