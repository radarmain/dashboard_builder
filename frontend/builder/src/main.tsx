import { Component, StrictMode, type CSSProperties, type ErrorInfo, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";

const REQUIRED_ENV = ["VITE_COGNITO_USER_POOL_ID", "VITE_COGNITO_APP_CLIENT_ID"] as const;

const boxStyle: CSSProperties = {
  margin: "48px auto", maxWidth: 720, padding: "16px 20px", border: "1px solid #fecaca", background: "#fef2f2",
  color: "#991b1b", font: "14px/1.5 system-ui, sans-serif", borderRadius: 8, whiteSpace: "pre-wrap", wordBreak: "break-word",
};

/** Plain-DOM error screen: works even when React itself failed to load or render. */
function showFatal(title: string, detail: string) {
  const root = document.getElementById("root") ?? document.body;
  root.innerHTML = "";
  const el = document.createElement("div");
  Object.assign(el.style, boxStyle);
  el.textContent = `${title}\n\n${detail}`;
  root.appendChild(el);
}

// Errors outside React's render (module load, async, event handlers) would otherwise be silent.
window.addEventListener("error", (e) => showFatal("Unexpected error", String(e.error?.stack ?? e.message)));
window.addEventListener("unhandledrejection", (e) => showFatal("Unhandled promise rejection", String((e.reason as Error)?.stack ?? e.reason)));

class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(error, info.componentStack);
  }
  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div style={boxStyle}>
        {`Something went wrong rendering the Builder.\n\n${error.stack ?? error.message}`}
      </div>
    );
  }
}

async function start() {
  const missing = REQUIRED_ENV.filter((k) => !import.meta.env[k]);
  if (missing.length) {
    showFatal(
      "Builder is not configured",
      `Missing: ${missing.join(", ")}\n\nRun from the repo root:\n  AWS_PROFILE=concrete AWS_REGION=us-east-2 .venv/bin/python scripts/write_env.py\nthen restart the Vite dev server (env files are read at startup).`,
    );
    return;
  }
  const { default: App } = await import("./App"); // dynamic so a module-load crash is caught below
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <ErrorBoundary>
        <App />
      </ErrorBoundary>
    </StrictMode>,
  );
}

start().catch((e: Error) => showFatal("Failed to start the Builder", String(e?.stack ?? e)));
