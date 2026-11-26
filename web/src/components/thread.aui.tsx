import {
  ComposerAttachments,
  UserMessageAttachments,
} from "@/components/attachment.aui";
import { ThreadFollowupSuggestions } from "@/components/follow-up-suggestions.aui";
import { Image } from "@/components/image";
import { MarkdownText } from "@/components/markdown-text";
import { Reasoning } from "@/components/reasoning.aui";
import { ToolFallback } from "@/components/tool-fallback.aui";
import { Sources } from "@/components/ai-elements/sources";
import {
  ActionBarPrimitive,
  AuiIf,
  ComposerPrimitive,
  ErrorPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useAuiState,
  type AssistantState,
  type FileMessagePartComponent,
  type ToolCallMessagePartComponent,
} from "@assistant-ui/react";
import { ArrowUp, Copy, Mic, Play, Square } from "lucide-react";
import { createContext, useContext, type ComponentType } from "react";

export type ThreadComponents = {
  AssistantMessage?: ComponentType;
  Welcome?: ComponentType;
  WelcomeAfter?: ComponentType;
  ToolFallback?: ToolCallMessagePartComponent;
  ComposerExtra?: ComponentType;
  ComposerCancel?: ComponentType;
  ComposerStatus?: ComponentType;
  AssistantActions?: ComponentType;
};
export type ThreadProps = {
  components?: ThreadComponents;
  autoFocus?: boolean;
};
const Components = createContext<ThreadComponents>({});
const isEmpty = (s: AssistantState) =>
  s.thread.messages.length === 0 &&
  (!s.thread.isLoading || s.threads.isLoading);
const canResume = (s: AssistantState) =>
  !!s.thread.canResume && s.composer.type === "thread" && s.composer.isEmpty;

export function Thread({ components = {}, autoFocus = true }: ThreadProps) {
  const empty = useAuiState(isEmpty);
  const Welcome = components.Welcome;
  const WelcomeAfter = components.WelcomeAfter;
  return (
    <Components.Provider value={components}>
      <ThreadPrimitive.Root
        className="aui-root aui-thread-root bg-background flex h-full flex-col"
        style={{
          ["--thread-max-width" as string]: "44rem",
          ["--composer-bg" as string]: "var(--surface-2)",
          ["--composer-radius" as string]: "22px",
          ["--composer-padding" as string]: "8px",
        }}
      >
        <ThreadPrimitive.Viewport
          turnAnchor="top"
          data-slot="aui_thread-viewport"
          className="aui-thread-viewport relative min-h-0 flex-1 overflow-y-auto"
        >
          <div
            className={`mx-auto flex min-h-full w-full max-w-(--thread-max-width) flex-col px-4 pt-4 ${empty ? "justify-center" : ""}`}
          >
            {Welcome && (
              <AuiIf condition={isEmpty}>
                <Welcome />
              </AuiIf>
            )}
            <AuiIf
              condition={(s) =>
                s.thread.messages.length === 0 &&
                s.thread.isLoading &&
                !s.threads.isLoading
              }
            >
              <p role="status">Loading conversation…</p>
            </AuiIf>
            <AuiIf
              condition={(s) =>
                s.thread.hasEarlier || s.thread.isLoadingEarlier
              }
            >
              <ThreadPrimitive.LoadEarlier asChild>
                <button type="button">Load earlier messages</button>
              </ThreadPrimitive.LoadEarlier>
            </AuiIf>
            <div
              data-slot="aui_message-group"
              className="mb-14 flex flex-col gap-y-6 empty:hidden"
            >
              <ThreadPrimitive.Messages>
                {() => <ThreadMessage />}
              </ThreadPrimitive.Messages>
            </div>
            <ThreadPrimitive.ViewportFooter className={`aui-thread-viewport-footer flex flex-col gap-4 bg-background pb-4 ${empty ? "" : "sticky bottom-0 mt-auto"}`}>
              <ThreadFollowupSuggestions />
              <Composer autoFocus={autoFocus} />
              {WelcomeAfter && (
                <AuiIf condition={isEmpty}>
                  <WelcomeAfter />
                </AuiIf>
              )}
            </ThreadPrimitive.ViewportFooter>
          </div>
        </ThreadPrimitive.Viewport>
      </ThreadPrimitive.Root>
    </Components.Provider>
  );
}

function ThreadMessage() {
  const role = useAuiState((s) => s.message.role);
  const { AssistantMessage: Override } = useContext(Components);
  return role === "user" ? (
    <UserMessage />
  ) : Override ? (
    <Override />
  ) : (
    <AssistantMessage />
  );
}

const UserFile: FileMessagePartComponent = ({
  filename,
  mimeType,
  data,
  sourceType,
  status,
}) => {
  if (mimeType.toLowerCase().startsWith("image/") && sourceType !== "id") {
    const unsafe =
      /^[a-z][a-z\d+.-]*:/i.test(data) &&
      !/^(data:|https?:\/\/|blob:)/i.test(data);
    const image = /^(data:|https?:\/\/|blob:)/i.test(data)
      ? data
      : sourceType === "url"
        ? ""
        : `data:${mimeType};base64,${data}`;
    if (!unsafe && image)
      return (
        <Image type="image" image={image} filename={filename} status={status} />
      );
  }
  return <span>{filename || "Attachment"}</span>;
};
function UserMessage() {
  const id = useAuiState(
    (s) => s.message.metadata.custom?.server_id as string | undefined,
  );
  return (
    <MessagePrimitive.Root
      id={id ? `message-${id}` : undefined}
      tabIndex={id ? -1 : undefined}
      data-role="user"
      className="scroll-mt-24 px-2"
    >
      <UserMessageAttachments />
      <div className="aui-user-message-content ml-auto w-fit rounded-2xl bg-muted px-4 py-2">
        <MessagePrimitive.Parts components={{ Image, File: UserFile }} />
      </div>
    </MessagePrimitive.Root>
  );
}

function AssistantMessage() {
  const { ToolFallback: Tool = ToolFallback, AssistantActions } =
    useContext(Components);
  const id = useAuiState(
    (s) => s.message.metadata.custom?.server_id as string | undefined,
  );
  return (
    <MessagePrimitive.Root
      id={id ? `message-${id}` : undefined}
      tabIndex={id ? -1 : undefined}
      data-role="assistant"
      className="scroll-mt-24 px-2"
    >
      <div className="aui-assistant-message-content">
        <MessagePrimitive.Parts
          components={{
            Text: MarkdownText,
            Reasoning,
            tools: { Fallback: Tool },
          }}
        />
        <Sources />
        <MessagePrimitive.Error>
          <ErrorPrimitive.Root role="alert">
            <ErrorPrimitive.Message />
          </ErrorPrimitive.Root>
        </MessagePrimitive.Error>
      </div>
      <div className="flex items-center gap-2">
        <ActionBarPrimitive.Root hideWhenRunning>
          <ActionBarPrimitive.Copy aria-label="Copy answer">
            <Copy size={14} />
          </ActionBarPrimitive.Copy>
        </ActionBarPrimitive.Root>
        {AssistantActions && <AssistantActions />}
      </div>
    </MessagePrimitive.Root>
  );
}

function Composer({ autoFocus }: { autoFocus: boolean }) {
  const { ComposerExtra, ComposerCancel, ComposerStatus } =
    useContext(Components);
  return (
    <ComposerPrimitive.Root className="aui-composer-root relative flex w-full flex-col">
      <ComposerPrimitive.AttachmentDropzone asChild>
        <div
          data-slot="aui_composer-shell"
          className="aui_composer-shell flex flex-col gap-2 rounded-(--composer-radius) border border-white/10 bg-(--composer-bg) p-2 focus-within:border-white/25"
        >
          <ComposerAttachments />
          <ComposerPrimitive.Input
            placeholder="Message Arno…"
            aria-label="Message Arno"
            rows={1}
            autoFocus={autoFocus}
            enterKeyHint="send"
            className="aui-composer-input max-h-48 min-h-10 w-full resize-none bg-transparent px-2.5 py-1 text-base outline-none"
          />
          <div className="flex items-center justify-between">
            <div>{ComposerExtra && <ComposerExtra />}</div>
            <div className="flex items-center gap-2">
              <AuiIf
                condition={(s) =>
                  s.thread.capabilities.dictation &&
                  s.composer.dictation == null
                }
              >
                <ComposerPrimitive.Dictate aria-label="Start voice input" className="aui-composer-dictate size-7 rounded-full text-muted-foreground hover:text-foreground">
                  <Mic size={16} />
                </ComposerPrimitive.Dictate>
              </AuiIf>
              <AuiIf
                condition={(s) =>
                  s.thread.capabilities.dictation &&
                  s.composer.dictation != null
                }
              >
                <ComposerPrimitive.StopDictation aria-label="Stop voice input" className="aui-composer-stop-dictation size-7 rounded-full text-destructive">
                  <Square size={14} />
                </ComposerPrimitive.StopDictation>
              </AuiIf>
              <AuiIf condition={(s) => !s.composer.canCancel && !canResume(s)}>
                <ComposerPrimitive.Send aria-label="Send message" className="aui-composer-send inline-flex size-7 items-center justify-center rounded-full bg-[var(--lime)] text-[var(--lime-ink)] disabled:opacity-50">
                  <ArrowUp size={16} />
                </ComposerPrimitive.Send>
              </AuiIf>
              <AuiIf condition={canResume}>
                <ComposerPrimitive.Resume aria-label="Resume generating">
                  <Play size={16} />
                </ComposerPrimitive.Resume>
              </AuiIf>
              <AuiIf condition={(s) => s.composer.canCancel}>
                {ComposerCancel ? (
                  <ComposerCancel />
                ) : (
                  <ComposerPrimitive.Cancel aria-label="Stop generating">
                    <Square size={14} />
                  </ComposerPrimitive.Cancel>
                )}
              </AuiIf>
            </div>
          </div>
          {ComposerStatus && <ComposerStatus />}
        </div>
      </ComposerPrimitive.AttachmentDropzone>
    </ComposerPrimitive.Root>
  );
}
