import { Typography } from "@mui/material";
import { AuthProvider, RequireAuth, isBuilder } from "@rdb/core";
import { portalRoutes } from "@rdb/portal";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { ClientDashboards } from "./ClientDashboards";
import { Layout } from "./Layout";
import { AdminSettingsPage } from "./pages/AdminSettings";
import { ClientPage, ClientsPage } from "./pages/Clients";
import { ProjectPage } from "./pages/Project";
import { ViewEditor } from "./pages/ViewEditor";
import { PREVIEW_BASE, PreviewShell } from "./Preview";

export default function App() {
  return (
    <AuthProvider>
      <RequireAuth
        appName="Dashboard Builder"
        allow={isBuilder}
        deniedTitle="This app is for analysts"
        deniedBody={(id) =>
          id.groups.includes("client")
            ? "This is the RADaR analyst tool."
            : "Your account doesn't have builder access."
        }
        deniedExtra={(id) => (id.groups.includes("client") ? <ClientDashboards /> : null)}
      >
        <BrowserRouter>
          <Routes>
            <Route element={<PreviewShell />}>{portalRoutes(PREVIEW_BASE)}</Route>
            <Route element={<Layout />}>
              <Route path="/" element={<ClientsPage />} />
              <Route path="/settings" element={<AdminSettingsPage />} />
              <Route path="/c/:client" element={<ClientPage />} />
              <Route path="/c/:client/p/:project" element={<ProjectPage />} />
              <Route path="/c/:client/p/:project/v/:view" element={<ViewEditor />} />
              <Route path="*" element={<Typography sx={{ p: 4, color: "text.secondary" }}>Not found</Typography>} />
            </Route>
          </Routes>
        </BrowserRouter>
      </RequireAuth>
    </AuthProvider>
  );
}
