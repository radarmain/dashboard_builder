import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { setUnauthorizedHandler } from "./api";
import { currentSession, identityOf, signOut as doSignOut, type Identity } from "./auth";
import { Box, LinearProgress, TextField, Typography } from "@mui/material";
import { Btn, Card, ErrorBox, LOGO, Loading, errMsg } from "./ui";
import { signIn } from "./auth";

interface AuthState {
  status: "loading" | "out" | "in";
  identity: Identity | null;
  refresh: () => Promise<void>;
  signOut: () => void;
}
const Ctx = createContext<AuthState>(null as never);
export const useAuth = () => useContext(Ctx);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthState["status"]>("loading");
  const [identity, setIdentity] = useState<Identity | null>(null);

  const refresh = useCallback(async () => {
    const s = await currentSession();
    setIdentity(s ? identityOf(s) : null);
    setStatus(s ? "in" : "out");
  }, []);
  const signOut = useCallback(() => {
    doSignOut();
    setIdentity(null);
    setStatus("out");
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(signOut); // any 401 signs the user out
    void refresh();
  }, [refresh, signOut]);

  const value = useMemo(() => ({ status, identity, refresh, signOut }), [status, identity, refresh, signOut]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

function Centered({ children }: { children: ReactNode }) {
  return (
    <Box sx={{ minHeight: "100vh", display: "grid", placeItems: "center", p: 2, bgcolor: (t) => t.palette.tokens.surfaceContainerLow }}>
      <Box sx={{ width: "100%", maxWidth: 420 }}>{children}</Box>
    </Box>
  );
}

function Logo() {
  return <Box component="img" src={LOGO} alt="RADaR" sx={{ display: "block", height: 56, width: "auto", mx: "auto", mb: 1 }} />;
}

export interface RequireAuthProps {
  children: ReactNode;
  /** Shown on the login card, e.g. "Dashboard Builder". */
  appName: string;
  /** Who may use this app. Others see `deniedTitle` / `deniedBody` and a Sign out button. */
  allow: (id: Identity) => boolean;
  deniedTitle: string;
  /** String, or a function of the identity when the copy depends on the user. */
  deniedBody: string | ((id: Identity) => string);
  /** Extra content under the denied message (e.g. links); get the identity. */
  deniedExtra?: (id: Identity) => ReactNode;
}

/** Gate: login -> (new password) -> allow check -> children. */
export function RequireAuth({ children, appName, allow, deniedTitle, deniedBody, deniedExtra }: RequireAuthProps) {
  const { status, identity, signOut } = useAuth();
  if (status === "loading") return <Centered><Loading /></Centered>;
  if (status === "out" || !identity) return <Login appName={appName} />;
  if (!allow(identity))
    return (
      <Centered>
        <Card sx={{ p: 4, textAlign: "center", display: "grid", gap: 2 }}>
          <Logo />
          <Typography variant="h5">{deniedTitle}</Typography>
          <Typography variant="body2" color="text.secondary">
            Signed in as {identity.email}. {typeof deniedBody === "function" ? deniedBody(identity) : deniedBody}
          </Typography>
          {deniedExtra?.(identity)}
          <Box><Btn variant="secondary" onClick={signOut}>Sign out</Btn></Box>
        </Card>
      </Centered>
    );
  return <>{children}</>;
}

function Login({ appName }: { appName: string }) {
  const { refresh } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [newPw, setNewPw] = useState("");
  const [newPw2, setNewPw2] = useState("");
  const [challenge, setChallenge] = useState<((p: string) => Promise<unknown>) | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (challenge) {
        if (newPw !== newPw2) throw new Error("Passwords don't match");
        await challenge(newPw);
        await refresh();
      } else {
        const r = await signIn(email.trim(), password);
        if (r.kind === "new_password") setChallenge(() => r.complete);
        else await refresh();
      }
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Centered>
      <Card sx={{ p: 4, overflow: "hidden", position: "relative" }}>
        {busy && <LinearProgress sx={{ position: "absolute", top: 0, left: 0, right: 0, borderRadius: 0 }} />}
        <Box component="form" onSubmit={submit} sx={{ display: "grid", gap: 2.5 }}>
          <Box sx={{ textAlign: "center" }}>
            <Logo />
            <Typography variant="h6" component="h1">{appName}</Typography>
            <Typography variant="body2" color="text.secondary">
              {challenge ? "Choose a new password" : "Sign in with your RADaR account"}
            </Typography>
          </Box>
          {challenge ? (
            <>
              <Typography variant="body2" color="text.secondary">
                Your password is temporary. Choose a new one (8+ characters, upper and lower case, a number and a symbol).
              </Typography>
              <TextField label="New password" type="password" autoFocus autoComplete="new-password" value={newPw} onChange={(e) => setNewPw(e.target.value)} />
              <TextField label="Confirm new password" type="password" autoComplete="new-password" value={newPw2} onChange={(e) => setNewPw2(e.target.value)} />
            </>
          ) : (
            <>
              <TextField label="Email" type="email" autoFocus autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} />
              <TextField label="Password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
            </>
          )}
          <ErrorBox error={error} />
          <Btn type="submit" disabled={busy} size="large" fullWidth>
            {busy ? "Working…" : challenge ? "Set password and sign in" : "Sign in"}
          </Btn>
        </Box>
      </Card>
    </Centered>
  );
}
