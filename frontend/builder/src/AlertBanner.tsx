import { useEffect, useState } from "react";
import { Alert } from "@mui/material";
import { get } from "./api";

interface SystemAlert { id: string; severity: "warning" | "critical"; message: string; expires_on: string | null; days_left: number | null }

/** Admin-only, persistent, NON-dismissible. Any failure (or non-admin 403) renders nothing. */
export function AlertBanner() {
  const [alerts, setAlerts] = useState<SystemAlert[]>([]);
  useEffect(() => {
    let live = true;
    get<SystemAlert[]>("/alerts").then((a) => live && Array.isArray(a) && setAlerts(a)).catch(() => {});
    return () => { live = false; };
  }, []);
  if (!alerts.length) return null;
  return (
    <>
      {alerts.map((a) => (
        <Alert key={a.id} role="alert" severity={a.severity === "critical" ? "error" : "warning"} variant="filled" square sx={{ borderRadius: 0 }}>
          {a.message}
        </Alert>
      ))}
    </>
  );
}
