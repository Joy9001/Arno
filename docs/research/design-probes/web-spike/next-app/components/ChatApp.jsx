import { useEffect, useMemo, useState } from "react";
import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport, lastAssistantMessageIsCompleteWithApprovalResponses } from "ai";
import { Streamdown } from "streamdown";

const THREAD = new URLSearchParams(location.search).get("thread") || "t1";

// Links render as plain text, and external images never load.
const SAFE = {
  a: ({ children }) => <span data-plain-link>{children}</span>,
  img: ({ alt }) => <span data-blocked-image>[image: {alt}]</span>,
};

function Part({ part, respond }) {
  if (part.type === "text") return <Streamdown components={SAFE}>{part.text}</Streamdown>;
  if (part.type === "data-card") return <div data-card>{part.data.summary} <button>Undo</button></div>;
  if (part.type.startsWith("tool-")) {
    const name = part.type.slice(5);
    if (part.state === "approval-requested") {
      return (
        <div data-confirm-card>
          Change: {JSON.stringify(part.input)}
          <button data-confirm onClick={() => respond({ id: part.approval.id, approved: true })}>Confirm</button>
          <button data-cancel onClick={() => respond({ id: part.approval.id, approved: false })}>Cancel</button>
        </div>
      );
    }
    return <div data-tool={name} data-state={part.state}>{name}: {part.state}</div>;
  }
  return null;
}

function Chat({ initial }) {
  const transport = useMemo(
    () =>
      new DefaultChatTransport({
        api: "/api/chat",
        prepareSendMessagesRequest: ({ id, messages, trigger, messageId }) => ({
          body: { id, trigger, messageId, messages: messages.slice(-1) },
        }),
        prepareReconnectToStreamRequest: ({ id }) => ({ api: `/api/chat/${id}/stream` }),
      }),
    [],
  );
  const { messages, sendMessage, status, error, addToolApprovalResponse } = useChat({
    id: THREAD,
    messages: initial,
    resume: true,
    transport,
    sendAutomaticallyWhen: lastAssistantMessageIsCompleteWithApprovalResponses,
  });
  const [text, setText] = useState("");
  window.__chat = { messages, status, error: error?.message };
  return (
    <main>
      <p data-status>{status}</p>
      {error && <p data-error>{error.message}</p>}
      <section>
        {messages.map((m) => (
          <article key={m.id} data-role={m.role}>
            {m.parts.map((p, i) => (
              <Part key={i} part={p} respond={addToolApprovalResponse} />
            ))}
          </article>
        ))}
      </section>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          sendMessage({ text });
          setText("");
        }}
      >
        <input data-input value={text} onChange={(e) => setText(e.target.value)} />
        <button data-send type="submit">Send</button>
        <button data-stop type="button" onClick={() => fetch(`/api/chat/${THREAD}/stop`, { method: "POST" })}>Stop</button>
      </form>
    </main>
  );
}

export function ChatApp() {
  const [initial, setInitial] = useState(null);
  useEffect(() => {
    fetch(`/api/chat/${THREAD}/messages`).then((r) => r.json()).then((d) => setInitial(d.messages));
  }, []);
  return initial ? <Chat initial={initial} /> : <p>Loading</p>;
}
