import { Component, StrictMode, type CSSProperties, type ComponentType, type ErrorInfo, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { CssBaseline, ThemeProvider } from "@mui/material";
import "@fontsource/roboto/300.css";
import "@fontsource/roboto/400.css";
import "@fontsource/roboto/500.css";
import "@fontsource/roboto/700.css";
import { applyCssVars, lightTokens, theme } from "./theme";

const REQUIRED_ENV = ["VITE_COGNITO_USER_POOL_ID", "VITE_COGNITO_APP_CLIENT_ID"] as const;

const boxStyle: CSSProperties = {
  margin: "48px auto", maxWidth: 720, padding: "16px 20px", border: "1px solid var(--rdb-error, currentColor)",
  background: "var(--rdb-error-container, Canvas)", color: "var(--rdb-on-error-container, CanvasText)",
  font: "14px/1.5 var(--rdb-font, system-ui, sans-serif)", borderRadius: 12, whiteSpace: "pre-wrap", wordBreak: "break-word",
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

class ErrorBoundary extends Component<{ children: ReactNode; appName: string }, { error: Error | null }> {
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
    return <div style={boxStyle}>{`Something went wrong rendering the ${this.props.appName}.\n\n${error.stack ?? error.message}`}</div>;
  }
}

/**
 * Shared entrypoint for the Builder and Viewer: theme + CSS vars, env check, fatal-error screens,
 * and a dynamic import of the app so a module-load crash is shown instead of a blank page.
 */
export function startApp(appName: string, load: () => Promise<{ default: ComponentType }>): void {
  applyCssVars(lightTokens); // --rdb-* variables on :root, used by @rdb/shared (no MUI there)
  // Errors outside React's render (module load, async, event handlers) would otherwise be silent.
  window.addEventListener("error", (e) => showFatal("Unexpected error", String(e.error?.stack ?? e.message)));
  window.addEventListener("unhandledrejection", (e) => showFatal("Unhandled promise rejection", String((e.reason as Error)?.stack ?? e.reason)));

  void (async () => {
    const missing = REQUIRED_ENV.filter((k) => !import.meta.env[k]);
    if (missing.length) {
      showFatal(
        `${appName} is not configured`,
        `Missing: ${missing.join(", ")}\n\nRun from the repo root:\n  AWS_PROFILE=concrete AWS_REGION=us-east-2 .venv/bin/python scripts/write_env.py\nthen restart the Vite dev server (env files are read at startup).`,
      );
      return;
    }
    const { default: App } = await load();
    createRoot(document.getElementById("root")!).render(
      <StrictMode>
        <ThemeProvider theme={theme}>
          <CssBaseline />
          <ErrorBoundary appName={appName}>
            <App />
          </ErrorBoundary>
        </ThemeProvider>
      </StrictMode>,
    );
  })().catch((e: Error) => showFatal(`Failed to start the ${appName}`, String(e?.stack ?? e)));
}
