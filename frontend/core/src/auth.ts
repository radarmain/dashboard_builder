import {
  AuthenticationDetails,
  CognitoUser,
  CognitoUserPool,
  type CognitoUserSession,
} from "amazon-cognito-identity-js";

// Pool ID and app client ID are public values (VITE_ vars from frontend/builder/.env.local).
// Created lazily so a missing value is reported on screen (see main.tsx) instead of crashing at import.
let _pool: CognitoUserPool | null = null;
function getPool(): CognitoUserPool {
  return (_pool ??= new CognitoUserPool({
    UserPoolId: import.meta.env.VITE_COGNITO_USER_POOL_ID ?? "",
    ClientId: import.meta.env.VITE_COGNITO_APP_CLIENT_ID ?? "",
  }));
}

export interface Identity {
  email: string;
  groups: string[];
}

export type SignInResult =
  | { kind: "ok"; session: CognitoUserSession }
  | { kind: "new_password"; complete: (newPassword: string) => Promise<CognitoUserSession> };

export function identityOf(session: CognitoUserSession): Identity {
  const p = session.getIdToken().payload;
  return { email: String(p.email ?? p["cognito:username"] ?? ""), groups: (p["cognito:groups"] as string[] | undefined) ?? [] };
}

export function isBuilder(id: Identity): boolean {
  return id.groups.includes("admin") || id.groups.includes("analyst");
}

export function signIn(email: string, password: string): Promise<SignInResult> {
  const user = new CognitoUser({ Username: email, Pool: getPool() });
  const details = new AuthenticationDetails({ Username: email, Password: password });
  return new Promise((resolve, reject) => {
    user.authenticateUser(details, {
      onSuccess: (session) => resolve({ kind: "ok", session }),
      onFailure: reject,
      newPasswordRequired: (attrs) => {
        // These are read-only/immutable in the challenge response.
        delete attrs.email;
        delete attrs.email_verified;
        resolve({
          kind: "new_password",
          complete: (newPassword) =>
            new Promise((res, rej) =>
              user.completeNewPasswordChallenge(newPassword, {}, { onSuccess: res, onFailure: rej }),
            ),
        });
      },
    });
  });
}

/** Current session; the SDK refreshes expired ID tokens with the refresh token automatically. */
export function currentSession(): Promise<CognitoUserSession | null> {
  const user = getPool().getCurrentUser();
  if (!user) return Promise.resolve(null);
  return new Promise((resolve) => {
    user.getSession((err: Error | null, session: CognitoUserSession | null) => {
      resolve(!err && session?.isValid() ? session : null);
    });
  });
}

export async function getIdToken(): Promise<string | null> {
  return (await currentSession())?.getIdToken().getJwtToken() ?? null;
}

export function signOut(): void {
  getPool().getCurrentUser()?.signOut();
}
