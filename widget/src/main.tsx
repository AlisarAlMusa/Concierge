/**
 * Concierge embeddable chat widget.
 *
 * Loaded from a single <script src=".../widget.js" data-widget-id="…" async>
 * tag the website owner pastes into their HTML. Zero host-page dependencies:
 * the bundle is a self-contained IIFE that mounts its own DOM under a
 * fixed-position root and uses inline-scoped CSS.
 *
 * Runtime flow:
 *   1. On script load, locate our own <script> element and read data-widget-id.
 *   2. Derive the API origin from the script's own src (no env coupling).
 *   3. On first open, exchange (public_widget_id, location.origin) for a
 *      session JWT at POST /public/widgets/session, then fetch the tenant
 *      greeting via GET /public/widgets/config.
 *   4. Each user message POSTs to /public/chat with the session bearer token;
 *      conversation_id is threaded across turns so the orchestrator can
 *      correlate the conversation server-side.
 */

type Role = "visitor" | "assistant";
interface Msg {
  role: Role;
  text: string;
}

(function init() {
  const scripts = Array.from(document.scripts);
  const self =
    document.currentScript instanceof HTMLScriptElement
      ? document.currentScript
      : scripts.find(
          (s) => /widget\.js(\?|$)/.test(s.src) && s.getAttribute("data-widget-id")
        );

  if (!self || !self.getAttribute("data-widget-id")) {
    // eslint-disable-next-line no-console
    console.error(
      "[concierge-widget] could not find <script src='.../widget.js' data-widget-id='…'>"
    );
    return;
  }

  const widgetId = self.getAttribute("data-widget-id")!;
  const apiBase = new URL(self.src, window.location.href).origin;
  const pageOrigin = window.location.origin;

  let token: string | null = null;
  let conversationId: string | null = null;
  let messages: Msg[] = [];
  let isOpen = false;
  let sending = false;
  let error: string | null = null;
  let initializing = false;
  let greetingShown = false;

  // ── CSS ──────────────────────────────────────────────────────────────────
  const css = `
.cwidget-root { all: initial; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; color: #111; }
.cwidget-root *, .cwidget-root *::before, .cwidget-root *::after { box-sizing: border-box; }
.cwidget-bubble {
  position: fixed; right: 24px; bottom: 24px; z-index: 2147483647;
  width: 60px; height: 60px; border-radius: 50%;
  background: #0e76fd; color: #fff; border: none;
  box-shadow: 0 4px 14px rgba(0,0,0,.18); cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: transform .15s ease, background .15s ease;
}
.cwidget-bubble:hover { background: #0560d6; transform: scale(1.05); }
.cwidget-bubble svg { width: 28px; height: 28px; }
.cwidget-panel {
  position: fixed; right: 24px; bottom: 96px; z-index: 2147483647;
  width: 380px; max-width: calc(100vw - 32px);
  height: 560px; max-height: calc(100vh - 120px);
  background: #fff; border-radius: 12px;
  box-shadow: 0 12px 32px rgba(0,0,0,.22);
  display: flex; flex-direction: column; overflow: hidden;
  font-size: 14px;
}
.cwidget-header {
  background: #0e76fd; color: #fff; padding: 14px 16px;
  font-weight: 600; display: flex; justify-content: space-between;
  align-items: center; font-size: 15px;
}
.cwidget-close {
  background: transparent; border: none; color: #fff;
  font-size: 18px; cursor: pointer; line-height: 1; padding: 0 4px;
}
.cwidget-close:hover { opacity: .8; }
.cwidget-msgs {
  flex: 1; overflow-y: auto; padding: 16px;
  background: #f7f8fa; display: flex; flex-direction: column; gap: 8px;
}
.cwidget-msg {
  padding: 10px 12px; border-radius: 10px; max-width: 80%;
  font-size: 14px; line-height: 1.4;
  white-space: pre-wrap; word-wrap: break-word;
}
.cwidget-msg.user {
  align-self: flex-end; background: #0e76fd; color: #fff;
  border-bottom-right-radius: 2px;
}
.cwidget-msg.bot {
  align-self: flex-start; background: #fff; border: 1px solid #e1e4e8;
  border-bottom-left-radius: 2px;
}
.cwidget-typing {
  align-self: flex-start; font-size: 13px; color: #666;
  font-style: italic; padding: 2px 4px;
}
.cwidget-error {
  background: #fbeaea; color: #a3262c; padding: 8px 12px;
  font-size: 13px; border-top: 1px solid #f3c2c2;
}
.cwidget-input {
  display: flex; gap: 8px; border-top: 1px solid #e1e4e8;
  padding: 10px; background: #fff;
}
.cwidget-input input {
  flex: 1; padding: 9px 12px; border: 1px solid #d0d5db;
  border-radius: 8px; font-size: 14px; font-family: inherit;
  background: #fff; color: #111;
}
.cwidget-input input:focus { outline: none; border-color: #0e76fd; }
.cwidget-input button {
  padding: 9px 14px; background: #0e76fd; color: #fff;
  border: none; border-radius: 8px; cursor: pointer;
  font-size: 14px; font-weight: 600;
}
.cwidget-input button:disabled { background: #9ec3f9; cursor: not-allowed; }
`;
  const styleEl = document.createElement("style");
  styleEl.setAttribute("data-concierge-widget", "");
  styleEl.textContent = css;
  document.head.appendChild(styleEl);

  const root = document.createElement("div");
  root.className = "cwidget-root";
  root.setAttribute("data-concierge-widget", "");
  document.body.appendChild(root);

  function esc(s: string): string {
    const div = document.createElement("div");
    div.textContent = s;
    return div.innerHTML;
  }

  function render(): void {
    const errorBar = error
      ? `<div class="cwidget-error">${esc(error)}</div>`
      : "";

    const msgsHtml = messages
      .map(
        (m) =>
          `<div class="cwidget-msg ${m.role === "visitor" ? "user" : "bot"}">${esc(
            m.text
          )}</div>`
      )
      .join("");

    const typing = sending
      ? `<div class="cwidget-typing">Assistant is typing…</div>`
      : "";

    const panel = isOpen
      ? `
        <div class="cwidget-panel" role="dialog" aria-label="Chat">
          <div class="cwidget-header">
            <span>Chat with us</span>
            <button class="cwidget-close" aria-label="Close" data-close="1">✕</button>
          </div>
          <div class="cwidget-msgs" id="cwidget-msgs">${msgsHtml}${typing}</div>
          ${errorBar}
          <form class="cwidget-input" data-form="1">
            <input
              type="text"
              name="msg"
              autocomplete="off"
              placeholder="${initializing ? "Connecting…" : "Type a message…"}"
              ${sending || initializing ? "disabled" : ""}
            />
            <button type="submit" ${sending || initializing ? "disabled" : ""}>Send</button>
          </form>
        </div>`
      : "";

    root.innerHTML = `
      ${panel}
      <button class="cwidget-bubble" aria-label="${
        isOpen ? "Close chat" : "Open chat"
      }" data-toggle="1">
        <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
          <path d="M20 2H4a2 2 0 0 0-2 2v18l4-4h14a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2zM6 9h12v2H6zm0 4h8v2H6z"/>
        </svg>
      </button>
    `;

    root.querySelector<HTMLButtonElement>("[data-toggle]")?.addEventListener(
      "click",
      onToggle
    );
    root
      .querySelector<HTMLButtonElement>("[data-close]")
      ?.addEventListener("click", () => {
        isOpen = false;
        render();
      });
    root
      .querySelector<HTMLFormElement>("[data-form]")
      ?.addEventListener("submit", onSubmit);

    const list = root.querySelector<HTMLDivElement>("#cwidget-msgs");
    if (list) list.scrollTop = list.scrollHeight;

    if (isOpen && !sending && !initializing) {
      root.querySelector<HTMLInputElement>("input[name=msg]")?.focus();
    }
  }

  async function ensureSession(): Promise<boolean> {
    if (token) return true;
    initializing = true;
    error = null;
    render();
    try {
      const r = await fetch(`${apiBase}/public/widgets/session`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ public_widget_id: widgetId, origin: pageOrigin }),
      });
      if (!r.ok) {
        const body = await safeText(r);
        throw new Error(`session ${r.status}: ${body || r.statusText}`);
      }
      const data = (await r.json()) as { token: string };
      token = data.token;

      if (!greetingShown) {
        try {
          const c = await fetch(`${apiBase}/public/widgets/config`, {
            headers: { Authorization: `Bearer ${token}` },
          });
          if (c.ok) {
            const cfg = (await c.json()) as { greeting?: string };
            if (cfg.greeting) {
              messages.push({ role: "assistant", text: cfg.greeting });
            }
          }
        } catch {
          /* greeting is optional — silent fallback */
        }
        greetingShown = true;
      }
      return true;
    } catch (e: unknown) {
      error = `Couldn't start chat: ${describe(e)}`;
      return false;
    } finally {
      initializing = false;
      render();
    }
  }

  async function onToggle(): Promise<void> {
    isOpen = !isOpen;
    render();
    if (isOpen && !token) {
      await ensureSession();
    }
  }

  async function onSubmit(ev: SubmitEvent): Promise<void> {
    ev.preventDefault();
    const form = ev.currentTarget as HTMLFormElement;
    const input = form.querySelector<HTMLInputElement>("input[name=msg]")!;
    const text = input.value.trim();
    if (!text || sending) return;

    if (!token) {
      const ok = await ensureSession();
      if (!ok) return;
    }

    messages.push({ role: "visitor", text });
    input.value = "";
    sending = true;
    error = null;
    render();

    try {
      const r = await fetch(`${apiBase}/public/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token!}`,
        },
        body: JSON.stringify({
          message: text,
          conversation_id: conversationId,
        }),
      });
      if (!r.ok) {
        const body = await safeText(r);
        throw new Error(`${r.status}${body ? `: ${body.slice(0, 240)}` : ""}`);
      }
      const data = (await r.json()) as {
        message: string;
        conversation_id?: string;
      };
      if (data.conversation_id) conversationId = data.conversation_id;
      messages.push({
        role: "assistant",
        text: data.message || "(no reply)",
      });
    } catch (e: unknown) {
      error = `Send failed: ${describe(e)}`;
    } finally {
      sending = false;
      render();
    }
  }

  async function safeText(r: Response): Promise<string> {
    try {
      return await r.text();
    } catch {
      return "";
    }
  }
  function describe(e: unknown): string {
    if (e instanceof Error) return e.message;
    return typeof e === "string" ? e : "network error";
  }

  render();
})();
