import { memo, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Box, CircularProgress, LinearProgress, TextField, Typography, keyframes, useMediaQuery } from "@mui/material";
import { alpha, type SxProps, type Theme } from "@mui/material/styles";
import type { ViewSpec } from "@rdb/shared";
import { ApiError, streamChat, type ChatEvent, type ChatOp } from "../api";
import { Btn, errMsg } from "../ui";
import { MONO } from "@rdb/core";

interface Step { text: string; done: boolean }
interface Msg {
  role: "user" | "assistant" | "error";
  text: string;
  applied?: number; // validated changes applied to the local draft in this response
  steps?: Step[]; // live status lines for this response
}

const slideIn = keyframes`from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; }`;
const dotPulse = keyframes`0%, 80%, 100% { opacity: .25; transform: scale(.7); } 40% { opacity: 1; transform: scale(1); }`;
const sheen = keyframes`0% { background-position: 200% 0; } 100% { background-position: -200% 0; }`;
const outlinePulse = keyframes`0%, 100% { opacity: .35; } 50% { opacity: 1; }`;
const noMotion = { "@media (prefers-reduced-motion: reduce)": { animation: "none !important" } } as const;

function TypingDots({ reduce }: { reduce: boolean }) {
  if (reduce) return <Typography variant="body2" sx={{ color: "text.secondary" }}>Working…</Typography>;
  return (
    <Box role="status" aria-label="Assistant is typing" sx={{ display: "flex", gap: 0.75, py: 0.75, px: 0.25 }}>
      {[0, 1, 2].map((i) => (
        <Box key={i} sx={{ width: 7, height: 7, borderRadius: "50%", bgcolor: "secondary.main", animation: `${dotPulse} 1.2s ${i * 0.16}s infinite ease-in-out`, ...noMotion }} />
      ))}
    </Box>
  );
}

/** Markdown for assistant text. No raw HTML, no images; links open in a new tab. Memoized so only the streaming message re-renders. */
const Markdown = memo(function Markdown({ text }: { text: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      skipHtml
      components={{
        a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
        img: () => null,
        table: ({ children }) => <div className="rdb-md-table"><table>{children}</table></div>,
      }}
    >
      {text}
    </ReactMarkdown>
  );
});

const mdSx: SxProps<Theme> = (t) => {
  const k = t.palette.tokens;
  return {
    ...t.typography.body2, wordBreak: "break-word",
    "& > :first-child": { mt: 0 }, "& > :last-child": { mb: 0 },
    "& p": { m: "0 0 .75em" },
    "& h1, & h2, & h3, & h4, & h5, & h6": { ...t.typography.subtitle1, m: "1em 0 .4em", color: k.onSurface },
    "& h1": { ...t.typography.h6 },
    "& ul, & ol": { m: "0 0 .75em", pl: 3 },
    "& li": { mb: 0.25 },
    "& li > p": { mb: 0.25 },
    "& code": { fontFamily: MONO, fontSize: "0.85em", bgcolor: k.surfaceContainerHigh, px: 0.6, py: 0.1, borderRadius: "4px" },
    "& pre": { m: "0 0 .75em", p: 1.5, bgcolor: k.surfaceContainerHigh, borderRadius: "8px", overflow: "auto" },
    "& pre code": { bgcolor: "transparent", p: 0, fontSize: "0.8em" },
    "& blockquote": { m: "0 0 .75em", pl: 1.5, py: 0.25, borderLeft: `3px solid ${k.secondary}`, color: k.onSurfaceVariant },
    "& a": { color: k.primary, textDecorationColor: k.secondary, textUnderlineOffset: 2 },
    "& hr": { border: 0, borderTop: `1px solid ${k.outline}`, my: 1.5 },
    "& .rdb-md-table": { overflowX: "auto", mb: "0.75em" },
    "& table": { borderCollapse: "collapse", fontSize: "0.9em" },
    "& th, & td": { border: `1px solid ${k.outlineVariant}`, px: 1.25, py: 0.5, textAlign: "left" },
    "& th": { bgcolor: k.surfaceContainerHigh, fontWeight: 500 },
  };
};

function StepLine({ step, reduce }: { step: Step; reduce: boolean }) {
  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: 1, color: "text.secondary", animation: reduce ? "none" : `${slideIn} 220ms ease-out both`, ...noMotion }}>
      <Box sx={{ width: 16, height: 16, display: "grid", placeItems: "center", flexShrink: 0 }}>
        {step.done ? <Typography component="span" sx={{ color: "success.main", fontSize: 14, lineHeight: 1 }}>✓</Typography>
          : reduce ? <Typography component="span" sx={{ fontSize: 14, lineHeight: 1 }}>•</Typography> : <CircularProgress size={12} thickness={6} />}
      </Box>
      <Typography variant="caption">{step.text}</Typography>
    </Box>
  );
}

/** Chat history lives in component state only (not persisted). */
export function ChatPanel({ chatPath, getSpec, onChanges, onUndo, undoCount }: {
  chatPath: string;
  getSpec: () => ViewSpec;
  onChanges: (ops: ChatOp[], turnId: number) => void;
  onUndo: () => void;
  undoCount: number;
}) {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [open, setOpen] = useState(true);
  const abort = useRef<AbortController | null>(null);
  const turn = useRef(0);
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true); // auto-scroll unless the user scrolled up
  const reduce = useMediaQuery("(prefers-reduced-motion: reduce)");

  useEffect(() => {
    const el = scroller.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [msgs]);
  useEffect(() => () => abort.current?.abort(), []);

  const onScroll = () => {
    const el = scroller.current;
    if (el) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
  };
  const patchLast = (fn: (m: Msg) => Msg) =>
    setMsgs((all) => (all.length ? [...all.slice(0, -1), fn(all[all.length - 1])] : all));
  const addStep = (text: string) =>
    patchLast((m) => ({ ...m, steps: [...(m.steps ?? []).map((s) => ({ ...s, done: true })), { text, done: false }] }));

  async function send() {
    const message = input.trim();
    if (!message || streaming) return;
    const history = msgs.filter((m) => m.role !== "error" && m.text.trim()).slice(-20).map((m) => ({ role: m.role, content: m.text }));
    // Elements without SQL yet can't be validated server-side; leave them out of what the AI sees.
    const spec = getSpec();
    const ids = new Set(spec.elements.filter((e) => e.type === "text" || e.sql.trim()).map((e) => e.id));
    const current = { ...spec, elements: spec.elements.filter((e) => ids.has(e.id)), layout: spec.layout.filter((l) => ids.has(l.element_id)) };

    const turnId = ++turn.current;
    setInput("");
    setOpen(true);
    stick.current = true;
    setMsgs((m) => [...m, { role: "user", text: message }, { role: "assistant", text: "", applied: 0, steps: [{ text: "Thinking…", done: false }] }]);
    setStreaming(true);
    const ctl = new AbortController();
    abort.current = ctl;
    const onEvent = (e: ChatEvent) => {
      if (e.type === "status") addStep(e.text);
      else if (e.type === "text") patchLast((m) => ({ ...m, text: m.text + e.delta }));
      else if (e.type === "changes") {
        onChanges(e.operations, turnId);
        patchLast((m) => ({ ...m, applied: (m.applied ?? 0) + e.operations.length }));
      } else if (e.type === "error") setMsgs((all) => [...all, { role: "error", text: e.message }]);
    };
    try {
      await streamChat(chatPath, { message, history, current_spec: current }, ctl.signal, onEvent);
    } catch (err) {
      if (ctl.signal.aborted) setMsgs((all) => [...all, { role: "error", text: "Stopped." }]);
      else setMsgs((all) => [...all, { role: "error", text: err instanceof ApiError ? err.message : errMsg(err) }]);
    } finally {
      setStreaming(false);
      // the response is over: finish any spinner, then drop the live status lines
      setMsgs((all) => all.map((m) => (m.steps ? { ...m, steps: undefined } : m)));
      abort.current = null;
    }
  }

  const fieldSx: SxProps<Theme> = streaming ? (t) => ({
    "& .MuiOutlinedInput-root.Mui-disabled": {
      backgroundImage: `linear-gradient(100deg, ${t.palette.tokens.surfaceContainerLow} 30%, ${alpha(t.palette.secondary.main, 0.2)} 50%, ${t.palette.tokens.surfaceContainerLow} 70%)`,
      backgroundSize: "200% 100%", animation: `${sheen} 2.2s linear infinite`, ...noMotion,
    },
    "& .MuiOutlinedInput-root.Mui-disabled .MuiOutlinedInput-notchedOutline": {
      borderColor: t.palette.secondary.main, borderWidth: 2, animation: `${outlinePulse} 1.6s ease-in-out infinite`, ...noMotion,
    },
  }) : {};

  return (
    <Box sx={{ position: "relative", borderTop: 1, borderColor: "divider", bgcolor: (t) => t.palette.tokens.surfaceContainerLow }}>
      {streaming && (reduce
        ? <Typography variant="caption" sx={{ display: "block", px: 2, pt: 0.75, color: "text.secondary" }}>Working…</Typography>
        : <LinearProgress color="secondary" aria-label="Assistant is working" sx={{ position: "absolute", top: 0, left: 0, right: 0, height: 3, borderRadius: 0 }} />)}

      <Box sx={{ display: "flex", alignItems: "center", gap: 1.5, px: 2, py: 0.5, color: "text.secondary" }}>
        <Btn variant="ghost" size="small" color="inherit" onClick={() => setOpen(!open)} sx={{ px: 1 }}>
          {open ? "▾" : "▸"} AI chat{msgs.length ? ` (${msgs.filter((m) => m.role === "user").length})` : ""}
        </Btn>
        <Typography variant="caption" noWrap sx={{ flex: 1 }}>History is kept in this tab only. Nothing is saved or published automatically.</Typography>
        <Btn variant="ghost" size="small" disabled={undoCount === 0 || streaming} onClick={onUndo}>
          Undo last AI change{undoCount ? ` (${undoCount})` : ""}
        </Btn>
      </Box>

      {open && msgs.length > 0 && (
        <Box ref={scroller} onScroll={onScroll} sx={{ maxHeight: 288, overflow: "auto", px: 2, pb: 1, display: "grid", gap: 1, alignContent: "start" }}>
          {msgs.map((m, i) => {
            const last = i === msgs.length - 1;
            const waiting = m.role === "assistant" && streaming && last && !m.text;
            return (
              <Box key={i} sx={{ display: "grid", gap: 0.75, justifyItems: m.role === "user" ? "end" : "start", animation: reduce ? "none" : `${slideIn} 200ms ease-out both`, ...noMotion }}>
                {m.role === "assistant" && m.steps?.map((s, j) => <StepLine key={j} step={s} reduce={reduce} />)}
                {(m.role !== "assistant" || m.text || waiting || !streaming || !last) && (
                  <Box
                    sx={(t) => {
                      const k = t.palette.tokens;
                      return {
                        maxWidth: "85%", px: 1.75, py: 1, borderRadius: "16px",
                        ...(m.role === "user" ? { bgcolor: k.primaryContainer, color: k.onPrimaryContainer, borderBottomRightRadius: "4px", whiteSpace: "pre-wrap", ...t.typography.body2 }
                          : m.role === "error" ? { bgcolor: k.errorContainer, color: k.onErrorContainer, whiteSpace: "pre-wrap", ...t.typography.body2 }
                            : { bgcolor: k.surfaceContainerHigh, color: k.onSurface, borderBottomLeftRadius: "4px" }),
                      };
                    }}
                  >
                    {m.role === "assistant" ? (
                      <>
                        {waiting ? <TypingDots reduce={reduce} />
                          : m.text ? <Box sx={mdSx}><Markdown text={m.text} /></Box>
                            : <Typography variant="body2" sx={{ color: "text.secondary" }}>(no text reply)</Typography>}
                        {!!m.applied && (
                          <Typography key={m.applied} variant="caption"
                            sx={{ display: "block", mt: 0.75, fontWeight: 500, color: "success.main", animation: reduce ? "none" : `${slideIn} 300ms ease-out both`, ...noMotion }}>
                            Applied {m.applied} change{m.applied === 1 ? "" : "s"} to your draft (unsaved).
                          </Typography>
                        )}
                      </>
                    ) : m.text}
                  </Box>
                )}
              </Box>
            );
          })}
        </Box>
      )}

      <Box component="form" sx={{ display: "flex", gap: 1, px: 2, pb: 1.5, pt: 0.5 }} onSubmit={(e: React.FormEvent) => { e.preventDefault(); void send(); }}>
        <TextField
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={streaming}
          placeholder={streaming ? "Working…" : "Ask for a chart, graph, or table, or ask to change an existing one"}
          sx={[{ "& .MuiOutlinedInput-root": { borderRadius: "999px", px: 1 } }, fieldSx as object]}
        />
        {streaming ? (
          <Btn type="button" variant="secondary" onClick={() => abort.current?.abort()}>Stop</Btn>
        ) : (
          <Btn type="submit" disabled={!input.trim()}>Send</Btn>
        )}
      </Box>
    </Box>
  );
}
