import { AuthProvider, RequireAuth, isBuilder } from "@rdb/core";
import { PortalProvider, hostClientFor, portalRoutes } from "@rdb/portal";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Home } from "./Home";

// Host mode when served from <client>.radar-analytics.com; path mode on localhost / cloudfront.net.
const HOST_CLIENT = hostClientFor(window.location.hostname);

export default function App() {
  return (
    <AuthProvider>
      <RequireAuth
        appName="RADaR Dashboards"
        allow={(id) => isBuilder(id) || id.groups.includes("client")}
        deniedTitle="No dashboard access"
        deniedBody="Your account isn't set up to view dashboards yet. Contact your RADaR team."
      >
        <BrowserRouter>
          <PortalProvider basePath="" preview={false} hostClient={HOST_CLIENT}>
            <Routes>{portalRoutes("", HOST_CLIENT ? null : <Route path="/" element={<Home />} />, HOST_CLIENT)}</Routes>
          </PortalProvider>
        </BrowserRouter>
      </RequireAuth>
    </AuthProvider>
  );
}
