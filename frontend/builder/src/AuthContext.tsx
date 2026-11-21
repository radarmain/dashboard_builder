import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { setUnauthorizedHandler } from "./api";
import { currentSession, identityOf, signOut as doSignOut, type Identity } from "./auth";
import { Btn, Card, ErrorBox, Field, errMsg, inputCls } from "./ui";
import { isBuilder, signIn } from "./auth";

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

/** Gate: login -> (new password) -> analyst check -> children. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { status, identity, signOut } = useAuth();
  if (status === "loading") return <div className="p-8 text-sm text-slate-500">Loading…</div>;
  if (status === "out" || !identity) return <Login />;
  if (!isBuilder(identity))
    return (
      <div className="mx-auto mt-24 max-w-md">
        <Card className="space-y-3 text-center">
          <h1 className="text-lg font-semibold">This app is for analysts</h1>
          <p className="text-sm text-slate-600">
            Signed in as {identity.email}. Your account doesn't have builder access. Client dashboards are available in the Viewer.
          </p>
          <Btn variant="secondary" onClick={signOut}>Sign out</Btn>
        </Card>
      </div>
    );
  return <>{children}</>;
}

function Login() {
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
    <div className="mx-auto mt-24 max-w-sm">
      <Card>
        <form onSubmit={submit} className="space-y-4">
          <h1 className="text-xl font-semibold">RDB Builder</h1>
          {challenge ? (
            <>
              <p className="text-sm text-slate-600">
                Your password is temporary. Choose a new one (8+ characters, upper and lower case, a number and a symbol).
              </p>
              <Field label="New password"><input className={inputCls} type="password" autoFocus value={newPw} onChange={(e) => setNewPw(e.target.value)} /></Field>
              <Field label="Confirm new password"><input className={inputCls} type="password" value={newPw2} onChange={(e) => setNewPw2(e.target.value)} /></Field>
            </>
          ) : (
            <>
              <Field label="Email"><input className={inputCls} type="email" autoFocus value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
              <Field label="Password"><input className={inputCls} type="password" value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
            </>
          )}
          <ErrorBox error={error} />
          <Btn type="submit" disabled={busy} className="w-full">
            {busy ? "Working…" : challenge ? "Set password and sign in" : "Sign in"}
          </Btn>
        </form>
      </Card>
    </div>
  );
}
