import { useEffect, useState } from "react";

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export default function App() {
  const [health, setHealth] = useState("checking...");
  useEffect(() => {
    fetch(`${API}/health`)
      .then((r) => r.json())
      .then((j) => setHealth(j.status))
      .catch(() => setHealth("unreachable"));
  }, []);
  return (
    <main style={{ fontFamily: "sans-serif", padding: 24 }}>
      <h1>RDB Viewer</h1>
      <p>API health: {health}</p>
    </main>
  );
}
