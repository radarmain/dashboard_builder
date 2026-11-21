import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AuthProvider, RequireAuth } from "./AuthContext";
import { Layout } from "./Layout";
import { ClientPage, ClientsPage } from "./pages/Clients";
import { ProjectPage } from "./pages/Project";
import { ViewEditor } from "./pages/ViewEditor";

export default function App() {
  return (
    <AuthProvider>
      <RequireAuth>
        <BrowserRouter>
          <Routes>
            <Route element={<Layout />}>
              <Route path="/" element={<ClientsPage />} />
              <Route path="/c/:client" element={<ClientPage />} />
              <Route path="/c/:client/p/:project" element={<ProjectPage />} />
              <Route path="/c/:client/p/:project/v/:view" element={<ViewEditor />} />
              <Route path="*" element={<div className="p-8 text-slate-500">Not found</div>} />
            </Route>
          </Routes>
        </BrowserRouter>
      </RequireAuth>
    </AuthProvider>
  );
}
