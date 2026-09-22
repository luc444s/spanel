import { type TouchEvent, useEffect, useRef, useState } from "react";
import { LogOut } from "./icons";
import { Outlet, useLocation } from "react-router-dom";

import { useAuthBootstrap } from "../../features/auth/useAuthBootstrap";
import { useLogoutAction } from "../../features/auth/useLogoutAction";
import { useAuthStore } from "../../features/auth/store";
import { Sidebar } from "./Sidebar";
import { ShellHeader } from "./ShellHeader";
import { Button } from "@systutor/shell/ui/button";
import { ThemeToggle } from "./theme-toggle";

function MenuIcon() {
  return (
    <svg aria-hidden="true" className="h-5 w-5" fill="none" viewBox="0 0 24 24">
      <path
        d="M4 6h16M4 12h16M4 18h16"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.8"
      />
    </svg>
  );
}

export function AppLayout() {
  const token = useAuthStore((state) => state.token);
  const user = useAuthStore((state) => state.user);
  const currentTenant = useAuthStore((state) => state.currentTenant);
  const currentBranch = useAuthStore((state) => state.currentBranch);
  const logout = useLogoutAction();
  const bootstrap = useAuthBootstrap();
  const location = useLocation();
  const [isNavOpen, setIsNavOpen] = useState(false);
  const swipeStartRef = useRef<{ x: number; y: number } | null>(null);

  useEffect(() => {
    setIsNavOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    function openSidebar() {
      setIsNavOpen(true);
    }
    window.addEventListener("systutor:open-sidebar", openSidebar);
    return () => window.removeEventListener("systutor:open-sidebar", openSidebar);
  }, []);

  function handleSidebarTouchStart(event: TouchEvent<HTMLDivElement>) {
    if (window.innerWidth >= 1024) return;
    const touch = event.touches[0];
    swipeStartRef.current = touch.clientX <= 24 ? { x: touch.clientX, y: touch.clientY } : null;
  }

  function handleSidebarTouchMove(event: TouchEvent<HTMLDivElement>) {
    const start = swipeStartRef.current;
    if (!start || isNavOpen) return;
    const touch = event.touches[0];
    if (touch.clientX - start.x > 55 && Math.abs(touch.clientY - start.y) < 80) {
      swipeStartRef.current = null;
      setIsNavOpen(true);
    }
  }

  if (token && bootstrap.isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-muted-foreground">
        Cargando contexto del tenant...
      </div>
    );
  }

  return (
    <div className="flex h-screen overflow-hidden" onTouchStart={handleSidebarTouchStart} onTouchMove={handleSidebarTouchMove}>
      <div className="hidden h-screen w-60 shrink-0 lg:block">
        <Sidebar />
      </div>

      <MobileSidebar open={isNavOpen} onClose={() => setIsNavOpen(false)} />

      <div className="flex h-screen min-w-0 flex-1 flex-col overflow-y-auto">
        <header className="hidden items-center justify-between border-b border-border bg-surface/70 px-16 py-4 backdrop-blur lg:flex lg:px-6">
          <ShellHeader
            tenantName={currentTenant?.name ?? null}
            branchName={currentBranch?.name ?? null}
            userName={user?.full_name ?? null}
            userEmail={user?.email ?? null}
          />

          <div className="flex items-center gap-3">
            <div className="hidden text-right sm:block">
              <p className="text-sm font-medium text-foreground">{user?.full_name ?? "Cargando usuario..."}</p>
              <p className="text-xs text-muted-foreground">{user?.email ?? "Sesion activa"}</p>
            </div>
            <Button type="button" variant="secondary" onClick={logout}>
              <LogOut />
              Cerrar sesión
            </Button>
          </div>
        </header>

        <main className="flex-1 px-4 py-6 lg:px-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

function MenuButton({ onClick, className = "" }: { onClick: () => void; className?: string }) {
  return (
    <button
      type="button"
      aria-label="Abrir menú"
      title="Menú"
      onClick={onClick}
      className={`fixed left-3 top-3 z-50 grid h-10 w-10 place-items-center rounded-full border border-border bg-card/95 text-foreground shadow-md backdrop-blur ${className}`}
    >
      <MenuIcon />
    </button>
  );
}

function MobileSidebar({ open, onClose }: { open: boolean; onClose: () => void }) {
  const swipeStartRef = useRef<{ x: number; y: number } | null>(null);

  if (!open) return null;

  function handleTouchStart(event: TouchEvent<HTMLDivElement>) {
    const touch = event.touches[0];
    swipeStartRef.current = { x: touch.clientX, y: touch.clientY };
  }

  function handleTouchMove(event: TouchEvent<HTMLDivElement>) {
    const start = swipeStartRef.current;
    if (!start) return;
    const touch = event.touches[0];
    if (start.x - touch.clientX > 55 && Math.abs(touch.clientY - start.y) < 80) {
      swipeStartRef.current = null;
      onClose();
    }
  }

  return (
    <div className="fixed inset-0 z-40 lg:hidden" onTouchStart={handleTouchStart} onTouchMove={handleTouchMove}>
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <div className="relative z-50 h-full w-60 max-w-[80%]">
        <Sidebar />
      </div>
    </div>
  );
}
