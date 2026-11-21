import { Link, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "./AuthContext";

export function Layout() {
  const { identity, signOut } = useAuth();
  const { pathname } = useLocation();
  const [, c, client, p, project, v, view] = pathname.split("/"); // /c/:client/p/:project/v/:view
  const crumbs: { to: string; label: string }[] = [];
  if (c === "c" && client) crumbs.push({ to: `/c/${client}`, label: client });
  if (p === "p" && project) crumbs.push({ to: `/c/${client}/p/${project}`, label: project });
  if (v === "v" && view) crumbs.push({ to: pathname, label: view });

  return (
    <div className="flex h-screen">
      <aside className="flex w-56 shrink-0 flex-col border-r border-slate-200 bg-white">
        <div className="px-4 py-4 text-lg font-semibold tracking-tight">RDB Builder</div>
        <nav className="flex-1 space-y-1 px-2 text-sm">
          <Link to="/" className="block rounded-md px-3 py-1.5 font-medium text-slate-700 hover:bg-slate-100">Clients</Link>
          {crumbs.map((cr, i) => (
            <Link key={cr.to} to={cr.to} style={{ marginLeft: (i + 1) * 10 }}
              className="block truncate rounded-md px-3 py-1.5 text-slate-600 hover:bg-slate-100">
              {["Client", "Project", "View"][i]}: <span className="font-medium">{cr.label}</span>
            </Link>
          ))}
        </nav>
        <div className="border-t border-slate-200 p-3 text-xs text-slate-500">
          <div className="truncate" title={identity?.email}>{identity?.email}</div>
          <div className="mb-2">{identity?.groups.join(", ")}</div>
          <button onClick={signOut} className="font-medium text-blue-600 hover:underline">Sign out</button>
        </div>
      </aside>
      <main className="min-w-0 flex-1 overflow-auto"><Outlet /></main>
    </div>
  );
}
