import {
  useCallback, useEffect, useMemo, useRef, useState,
  type ChangeEvent, type DragEvent, type MouseEvent, type ReactNode,
} from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { apiRequest, getApiBaseUrl } from "@systutor/shell/api/client";
import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { ConfirmDialog } from "@systutor/shell/ui/confirm-dialog";
import { Dialog } from "@systutor/shell/ui/dialog";
import { Input } from "@systutor/shell/ui/input";
import { Progress } from "@systutor/shell/ui/progress";
import { toast } from "@systutor/shell/ui/toast";
import { useT } from "@/i18n/use-i18n";
import { useAuthStore } from "../../../../../apps/web/src/features/auth/store";

import { ConflictDialog, PromptDialog, type ConflictChoice, type PromptRequest } from "./FileDialogs";
import { FileGrid } from "./FileGrid";
import { FileManagerModal } from "./FileManagerModal";
import { FileSidebar } from "./FileSidebar";
import { FileTable } from "./FileTable";
import { sortEntries, type SortKey, type SortState } from "./format";
import { isCoarsePointer, isTextFile, isZipFile, parentPath, within } from "./fileUtils";
import {
  ArchiveIcon,
  BackIcon,
  DownloadIcon,
  FolderIcon,
  FolderPlusIcon,
  ForwardIcon,
  GridViewIcon,
  HomeIcon,
  ListViewIcon,
  RefreshIcon,
  RenameIcon,
  SearchIcon,
  TrashIcon,
  UpIcon,
  UploadIcon,
} from "./FileIcons";
import type { Entry, Listing, TextFile, UploadProgress } from "./types";

const EMPTY: Entry[] = [];
const VIEW_KEY = "filemanager:view";
type ViewMode = "list" | "grid";

function readView(): ViewMode {
  try {
    return window.localStorage.getItem(VIEW_KEY) === "grid" ? "grid" : "list";
  } catch {
    return "list";
  }
}

function targetUrl(provider: string, id: string) {
  return `/api/v1/plugins/filemanager/targets/${encodeURIComponent(provider)}/${encodeURIComponent(id)}`;
}

type Props = {
  provider: string;
  resourceId: string;
  onClose: () => void;
};

export function FileExplorer({ provider, resourceId, onClose }: Props) {
  const t = useT();
  const permissions = useAuthStore((state) => state.permissions);
  const canWrite = permissions.includes("filemanager.files.manage");
  const token = useAuthStore((state) => state.token);
  const base = targetUrl(provider, resourceId);
  const queryClient = useQueryClient();

  const [nav, setNav] = useState({ stack: [""], index: 0 });
  const folder = nav.stack[nav.index];
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [lastIndex, setLastIndex] = useState<number | null>(null);
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<SortState>({ key: "name", direction: "asc" });
  const [view, setView] = useState<ViewMode>(readView);
  const [menu, setMenu] = useState<{ entry: Entry | null; x: number; y: number } | null>(null);
  const [progress, setProgress] = useState<UploadProgress[]>([]);
  const [editing, setEditing] = useState<{ path: string; version: string; content: string; original: string } | null>(null);
  const [discarding, setDiscarding] = useState(false);
  const [prompt, setPrompt] = useState<PromptRequest | null>(null);
  const [deleting, setDeleting] = useState<Entry[] | null>(null);
  const [extracting, setExtracting] = useState<string | null>(null);
  const [conflict, setConflict] = useState<{ name: string; resolve: (choice: ConflictChoice) => void } | null>(null);
  const [dragging, setDragging] = useState(false);
  const [marqueeBox, setMarqueeBox] = useState<{ x: number; y: number; width: number; height: number } | null>(null);
  const [marqueeActive, setMarqueeActive] = useState(false);
  const marqueeDrag = useRef<{ startX: number; startY: number; base: Set<string>; additive: boolean; moved: boolean } | null>(null);
  const dragDepth = useRef(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const loadEntries = useCallback(
    (path: string) => apiRequest<Listing>(`${base}/entries?path=${encodeURIComponent(path)}`),
    [base],
  );
  const listing = useQuery({
    queryKey: ["filemanager", provider, resourceId, { path: folder }],
    queryFn: () => loadEntries(folder),
  });
  const rootListing = useQuery({
    queryKey: ["filemanager", provider, resourceId, { root: true }],
    queryFn: () => loadEntries(""),
    enabled: folder !== "",
  });
  const refresh = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["filemanager", provider, resourceId] });
  }, [queryClient, provider, resourceId]);

  const entries = listing.data?.entries ?? EMPTY;
  const rootEntries = folder === "" ? entries : rootListing.data?.entries ?? EMPTY;
  const rootFolders = useMemo(
    () => rootEntries.filter((entry) => entry.directory).map((entry) => entry.name),
    [rootEntries],
  );
  const visible = useMemo(() => {
    const term = search.trim().toLowerCase();
    const filtered = term ? entries.filter((entry) => entry.name.toLowerCase().includes(term)) : entries;
    return sortEntries(filtered, sort);
  }, [entries, search, sort]);
  const selectedEntries = useMemo(
    () => entries.filter((entry) => selected.has(entry.name)),
    [entries, selected],
  );

  function resetView() {
    setSelected(new Set());
    setLastIndex(null);
    setSearch("");
    setMenu(null);
  }

  function go(path: string) {
    setNav((current) => ({
      stack: [...current.stack.slice(0, current.index + 1), path],
      index: current.index + 1,
    }));
    resetView();
  }

  function back() {
    setNav((current) => current.index > 0 ? { ...current, index: current.index - 1 } : current);
    resetView();
  }

  function forward() {
    setNav((current) => current.index < current.stack.length - 1 ? { ...current, index: current.index + 1 } : current);
    resetView();
  }

  function up() {
    const parent = parentPath(folder);
    if (parent !== folder) go(parent);
  }

  async function change(url: string, method: string, data?: object) {
    try {
      await apiRequest(`${base}${url}`, {
        method, ...(data ? { body: JSON.stringify(data) } : {}),
      });
      refresh();
    } catch (error) {
      toast.error((error as Error).message);
    }
  }

  async function openText(name: string) {
    const path = within(folder, name);
    try {
      const file = await apiRequest<TextFile>(`${base}/text?path=${encodeURIComponent(path)}`);
      setEditing({ path, ...file, original: file.content });
    } catch (error) {
      toast.error((error as Error).message);
    }
  }

  async function saveText() {
    if (!editing) return;
    try {
      await apiRequest(`${base}/text?path=${encodeURIComponent(editing.path)}`, {
        method: "PUT", body: JSON.stringify({ content: editing.content, version: editing.version }),
      });
      setEditing(null);
      refresh();
    } catch (error) {
      toast.error((error as Error).message);
    }
  }

  function requestCloseEditor() {
    if (!editing) return;
    if (editing.content === editing.original) setEditing(null);
    else setDiscarding(true);
  }

  async function download(name: string) {
    try {
      const path = within(folder, name);
      const { url } = await apiRequest<{ url: string }>(
        `${base}/download-tickets?path=${encodeURIComponent(path)}`, { method: "POST" },
      );
      const anchor = document.createElement("a");
      anchor.href = `${getApiBaseUrl()}${url}`;
      anchor.click();
    } catch (error) {
      toast.error((error as Error).message);
    }
  }

  function sendFile(file: File, name = file.name, replace = false): Promise<void> {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${getApiBaseUrl()}${base}/uploads?path=${encodeURIComponent(folder)}&replace=${replace}`);
      xhr.setRequestHeader("Authorization", `Bearer ${token}`);
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) {
          setProgress((items) => items.map((item) => item.name === file.name
            ? { ...item, percent: Math.round(event.loaded / event.total * 100) } : item));
        }
      };
      xhr.onload = () => xhr.status >= 200 && xhr.status < 300
        ? resolve() : reject(new Error(xhr.status === 409 ? "Already exists" : t("HTTP error {status}", { status: xhr.status })));
      xhr.onerror = () => reject(new Error(t("Connection failed")));
      const data = new FormData();
      data.append("file", file, name);
      xhr.send(data);
    });
  }

  function askConflict(name: string): Promise<ConflictChoice> {
    return new Promise((resolve) => setConflict({ name, resolve }));
  }

  function settleConflict(choice: ConflictChoice) {
    if (!conflict) return;
    const { resolve } = conflict;
    setConflict(null);
    resolve(choice);
  }

  async function uploadFiles(files: FileList | File[]) {
    const batch = Array.from(files);
    setProgress(batch.map((file) => ({ name: file.name, percent: 0, status: "Uploading" })));
    for (const file of batch) {
      let name = file.name;
      try {
        try {
          await sendFile(file);
        } catch (error) {
          if ((error as Error).message !== "Already exists") throw error;
          const choice = await askConflict(name);
          if (choice.action === "replace") await sendFile(file, name, true);
          else if (choice.action === "rename") {
            name = choice.name;
            await sendFile(file, name);
          } else throw new Error("Skipped");
        }
        setProgress((items) => items.map((item) => item.name === file.name
          ? { ...item, percent: 100, status: "Done" } : item));
      } catch (error) {
        setProgress((items) => items.map((item) => item.name === file.name
          ? { ...item, status: (error as Error).message } : item));
      }
    }
    refresh();
  }

  function onFileChange(event: ChangeEvent<HTMLInputElement>) {
    if (event.target.files) void uploadFiles(event.target.files);
    event.target.value = "";
  }

  function activate(entry: Entry) {
    setMenu(null);
    if (entry.directory) {
      go(within(folder, entry.name));
      return;
    }
    if (isTextFile(entry.name)) void openText(entry.name);
    else void download(entry.name);
  }

  function select(entry: Entry, index: number, event: MouseEvent) {
    if (isCoarsePointer()) {
      activate(entry);
      return;
    }
    setSelected((current) => {
      const next = new Set(current);
      if (event.shiftKey && lastIndex !== null) {
        const from = Math.min(lastIndex, index);
        const to = Math.max(lastIndex, index);
        visible.slice(from, to + 1).forEach((item) => next.add(item.name));
      } else if (event.ctrlKey || event.metaKey) {
        if (next.has(entry.name)) next.delete(entry.name);
        else next.add(entry.name);
      } else {
        next.clear();
        next.add(entry.name);
      }
      return next;
    });
    setLastIndex(index);
  }

  function toggle(entry: Entry, checked: boolean) {
    setSelected((current) => {
      const next = new Set(current);
      if (checked) next.add(entry.name);
      else next.delete(entry.name);
      return next;
    });
  }

  function askRename(entry: Entry) {
    setPrompt({
      title: t("Rename {name}", { name: entry.name }),
      label: t("New name"),
      initial: entry.name,
      confirmLabel: t("Rename"),
      action: (name) => {
        if (name !== entry.name) {
          void change("/entries", "PATCH", { path: within(folder, entry.name), name });
        }
      },
    });
  }

  function askNewFolder() {
    setPrompt({
      title: t("New folder"),
      label: t("Folder name"),
      confirmLabel: t("Create"),
      action: (name) => void change("/directories", "POST", { path: within(folder, name) }),
    });
  }

  function askCompress(names: string[]) {
    if (!names.length) return;
    setPrompt({
      title: t("Compress ZIP"),
      description: t("{count} item(s) selected.", { count: names.length }),
      label: t("ZIP name"),
      initial: "files.zip",
      confirmLabel: t("Compress"),
      action: (name) => {
        void change("/archives", "POST", { path: folder, name, entries: names });
        setSelected(new Set());
      },
    });
  }

  async function confirmDelete() {
    if (!deleting) return;
    const targets = deleting;
    setDeleting(null);
    try {
      if (targets.length === 1) {
        const [entry] = targets;
        await apiRequest(`${base}/entries?path=${encodeURIComponent(within(folder, entry.name))}`, { method: "DELETE" });
      } else {
        await apiRequest(`${base}/entries/delete-batch`, {
          method: "POST",
          body: JSON.stringify({ paths: targets.map((entry) => within(folder, entry.name)) }),
        });
      }
    } catch (error) {
      toast.error((error as Error).message);
    }
    setSelected(new Set());
    refresh();
  }

  function confirmExtract() {
    if (!extracting) return;
    const name = extracting;
    setExtracting(null);
    void change("/archives/extract", "POST", { path: within(folder, name), destination: folder });
  }

  function toggleSort(key: SortKey) {
    setSort((current) => current.key === key
      ? { key, direction: current.direction === "asc" ? "desc" : "asc" }
      : { key, direction: "asc" });
  }

  const dialogOpen = Boolean(prompt || deleting || extracting || conflict || editing || discarding);
  const uploading = progress.some((item) => item.status === "Uploading");

  useEffect(() => {
    if (!dialogOpen) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape" || conflict) return;
      event.preventDefault();
      if (discarding) setDiscarding(false);
      else if (prompt) setPrompt(null);
      else if (deleting) setDeleting(null);
      else if (extracting) setExtracting(null);
      else if (editing) requestCloseEditor();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

  useEffect(() => {
    try {
      window.localStorage.setItem(VIEW_KEY, view);
    } catch {
      // Private mode or storage disabled: the view still works for this session.
    }
  }, [view]);

  useEffect(() => {
    if (!marqueeActive) return;
    function onMove(event: globalThis.MouseEvent) {
      const drag = marqueeDrag.current;
      if (!drag) return;
      const x = Math.min(drag.startX, event.clientX);
      const y = Math.min(drag.startY, event.clientY);
      const width = Math.abs(event.clientX - drag.startX);
      const height = Math.abs(event.clientY - drag.startY);
      if (!drag.moved && width < 4 && height < 4) return;
      drag.moved = true;
      setMarqueeBox({ x, y, width, height });
      const next = new Set(drag.additive ? drag.base : []);
      document.querySelectorAll<HTMLElement>("[data-file-item='true']").forEach((node) => {
        const box = node.getBoundingClientRect();
        const overlaps =
          box.left < x + width && box.right > x && box.top < y + height && box.bottom > y;
        const name = node.dataset.fileName;
        if (overlaps && name) next.add(name);
      });
      setSelected(next);
    }
    function onUp() {
      const drag = marqueeDrag.current;
      if (drag && !drag.moved && !drag.additive) setSelected(new Set());
      marqueeDrag.current = null;
      setMarqueeActive(false);
      setMarqueeBox(null);
    }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [marqueeActive]);

  useEffect(() => {
    if (!menu) return;
    function close(event: PointerEvent) {
      if (!menuRef.current?.contains(event.target as Node)) setMenu(null);
    }
    function onScroll() {
      setMenu(null);
    }
    window.addEventListener("pointerdown", close, true);
    window.addEventListener("scroll", onScroll, true);
    return () => {
      window.removeEventListener("pointerdown", close, true);
      window.removeEventListener("scroll", onScroll, true);
    };
  }, [menu]);

  useEffect(() => {
    if (dialogOpen) return;
    function onKeyDown(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (target?.closest("input, textarea, select, [contenteditable='true']")) return;
      if (event.key === "F2" && canWrite && selectedEntries.length === 1) {
        event.preventDefault();
        askRename(selectedEntries[0]);
      } else if (event.key === "Delete" && canWrite && selectedEntries.length) {
        event.preventDefault();
        setDeleting(selectedEntries);
      } else if (event.key === "Backspace") {
        event.preventDefault();
        up();
      } else if (event.key === "Enter" && selectedEntries.length === 1) {
        event.preventDefault();
        activate(selectedEntries[0]);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

  const crumbs = folder.split("/").filter(Boolean);
  const single = selectedEntries.length === 1 ? selectedEntries[0] : null;

  return (
    <FileManagerModal
      title={t("File manager")}
      subtitle={`${provider} · ${resourceId}${folder ? ` · /${folder}` : ""}`}
      onClose={onClose}
      escapeEnabled={!dialogOpen && !uploading}
      nav={
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex gap-1">
              <Button size="sm" variant="secondary" aria-label={t("Back")} disabled={nav.index === 0} onClick={back} className="px-2">
                <BackIcon size={16} className="h-4 w-4" />
              </Button>
              <Button size="sm" variant="secondary" aria-label={t("Forward")} disabled={nav.index >= nav.stack.length - 1} onClick={forward} className="px-2">
                <ForwardIcon size={16} className="h-4 w-4" />
              </Button>
              <Button size="sm" variant="secondary" aria-label={t("Up")} disabled={folder === ""} onClick={up} className="px-2">
                <UpIcon size={16} className="h-4 w-4" />
              </Button>
              <Button size="sm" variant="secondary" aria-label={t("Refresh")} onClick={refresh} className="px-2">
                <RefreshIcon size={16} className="h-4 w-4" />
              </Button>
            </div>
            <nav aria-label={t("Breadcrumb")} className="flex min-w-0 flex-1 flex-wrap items-center gap-1 text-sm">
              <button type="button" className="text-primary underline" onClick={() => go("")}>{t("Home")}</button>
              {crumbs.map((part, index) => (
                <span key={index}>
                  <span className="mx-1 text-muted-foreground">/</span>
                  <button type="button" className="text-primary underline"
                    onClick={() => go(crumbs.slice(0, index + 1).join("/"))}>{part}</button>
                </span>
              ))}
            </nav>
            <div className="relative w-40 shrink-0 sm:w-56">
              <Input
                className="h-8 py-1 pl-8 text-xs"
                placeholder={t("Search in this folder")}
                aria-label={t("Search in this folder")}
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
              <span className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground" aria-hidden>
                <SearchIcon size={14} className="h-3.5 w-3.5" />
              </span>
            </div>
            <div className="flex shrink-0 overflow-hidden rounded-md border border-border" role="group" aria-label={t("View mode")}>
              <button
                type="button"
                aria-label={t("List view")}
                title={t("List view")}
                aria-pressed={view === "list"}
                onClick={() => setView("list")}
                className={`inline-flex items-center justify-center px-2 py-1 ${view === "list" ? "bg-primary text-primary-foreground" : "bg-secondary text-secondary-foreground hover:bg-accent"}`}
              >
                <ListViewIcon size={16} className="h-4 w-4" />
              </button>
              <button
                type="button"
                aria-label={t("Grid view")}
                title={t("Grid view")}
                aria-pressed={view === "grid"}
                onClick={() => setView("grid")}
                className={`inline-flex items-center justify-center px-2 py-1 ${view === "grid" ? "bg-primary text-primary-foreground" : "bg-secondary text-secondary-foreground hover:bg-accent"}`}
              >
                <GridViewIcon size={16} className="h-4 w-4" />
              </button>
            </div>
          </div>
          <div className="flex gap-1 overflow-x-auto pb-0.5 md:hidden">
            {rootFolders.map((name) => (
              <Button
                key={name}
                size="sm"
                variant={folder === name ? "primary" : "secondary"}
                className="inline-flex items-center gap-1.5"
                onClick={() => go(name)}
              >
                <FolderIcon size={14} className="h-3.5 w-3.5" />
                {name}
              </Button>
            ))}
          </div>
        </div>
      }
      actions={canWrite ? (
        <>
          <input ref={inputRef} className="hidden" type="file" multiple onChange={onFileChange} />
          <Button size="sm" className="inline-flex items-center gap-1.5" onClick={() => inputRef.current?.click()}>
            <UploadIcon size={15} className="h-4 w-4" />
            {t("Upload files")}
          </Button>
          <Button size="sm" variant="secondary" className="inline-flex items-center gap-1.5" onClick={askNewFolder}>
            <FolderPlusIcon size={15} className="h-4 w-4" />
            {t("New folder")}
          </Button>
          <Button size="sm" variant="secondary" className="inline-flex items-center gap-1.5" disabled={!single} onClick={() => single && void download(single.name)}>
            <DownloadIcon size={15} className="h-4 w-4" />
            {t("Download")}
          </Button>
          <Button size="sm" variant="secondary" className="inline-flex items-center gap-1.5" disabled={!selectedEntries.length} onClick={() => askCompress(selectedEntries.map((entry) => entry.name))}>
            <ArchiveIcon size={15} className="h-4 w-4" />
            {t("Compress")}
          </Button>
          <Button size="sm" variant="secondary" className="inline-flex items-center gap-1.5" disabled={!single || !isZipFile(single.name)} onClick={() => single && setExtracting(single.name)}>
            <ArchiveIcon size={15} className="h-4 w-4" />
            {t("Extract")}
          </Button>
          <Button size="sm" variant="secondary" className="inline-flex items-center gap-1.5" disabled={!single} onClick={() => single && askRename(single)}>
            <RenameIcon size={15} className="h-4 w-4" />
            {t("Rename")}
          </Button>
          <Button size="sm" variant="secondary" className="inline-flex items-center gap-1.5" disabled={!selectedEntries.length} onClick={() => setDeleting(selectedEntries)}>
            <TrashIcon size={15} className="h-4 w-4" />
            {t("Delete")}
          </Button>
        </>
      ) : (
        <>
          <Button size="sm" variant="secondary" className="inline-flex items-center gap-1.5" disabled={!single} onClick={() => single && void download(single.name)}>
            <DownloadIcon size={15} className="h-4 w-4" />
            {t("Download")}
          </Button>
          <p className="self-center text-xs text-muted-foreground">{t("Read-only mode")}</p>
        </>
      )}
      sidebar={<FileSidebar folders={rootFolders} current={folder} onGo={go} />}
      footer={
        <>
          <span>{t("{count} item(s)", { count: visible.length })} · {t("{count} selected", { count: selected.size })}{search ? ` · ${t("filter “{term}”", { term: search })}` : ""}</span>
          <span>{progress.length ? t("{done}/{total} uploaded", { done: progress.filter((item) => item.status === "Done").length, total: progress.length }) : ""}</span>
        </>
      }
      overlay={dragging ? (
        <div className="pointer-events-none absolute inset-0 z-[950] flex items-center justify-center rounded-xl border-2 border-dashed border-primary bg-background/85 p-6 text-center text-sm font-medium">
          {t("Drop files to upload them to this folder")}
        </div>
      ) : null}
    >
      <div
        className={`min-h-full ${marqueeActive ? "select-none" : ""}`}
        onMouseDown={(event) => {
          if (event.button !== 0 || isCoarsePointer()) return;
          const target = event.target as HTMLElement;
          if (target.closest("input, button, a, select, textarea, [role='menuitem'], [data-file-item='true']")) return;
          event.preventDefault();
          marqueeDrag.current = {
            startX: event.clientX,
            startY: event.clientY,
            base: new Set(selected),
            additive: event.ctrlKey || event.metaKey || event.shiftKey,
            moved: false,
          };
          setMarqueeBox({ x: event.clientX, y: event.clientY, width: 0, height: 0 });
          setMarqueeActive(true);
        }}
        onDragEnter={(event) => {
          if (!canWrite) return;
          event.preventDefault();
          dragDepth.current += 1;
          setDragging(true);
        }}
        onDragOver={(event) => {
          if (canWrite) event.preventDefault();
        }}
        onDragLeave={() => {
          dragDepth.current = Math.max(0, dragDepth.current - 1);
          if (dragDepth.current === 0) setDragging(false);
        }}
        onDrop={(event: DragEvent) => {
          event.preventDefault();
          dragDepth.current = 0;
          setDragging(false);
          if (canWrite) void uploadFiles(event.dataTransfer.files);
        }}
        onContextMenu={(event) => {
          event.preventDefault();
          setMenu({ entry: null, x: event.clientX, y: event.clientY });
        }}
      >
        {progress.length ? (
          <div className="space-y-2 border-b border-border bg-muted/30 p-3" aria-label={t("Uploads")}>
            {progress.map((item, index) => (
              <div key={`${item.name}-${index}`} className="space-y-1 text-xs">
                <div className="flex justify-between gap-2">
                  <span className="truncate">{item.name}</span>
                  <span className="shrink-0">{item.percent}% — {uploadStatusLabel(item.status, t)}</span>
                </div>
                <Progress value={item.percent} />
              </div>
            ))}
            <Button size="sm" variant="secondary" onClick={() => setProgress([])}>{t("Clear")}</Button>
          </div>
        ) : null}
        {listing.isLoading ? <p className="p-4 text-sm text-muted-foreground">{t("Loading files...")}</p> : null}
        {listing.error ? (
          <div className="space-y-2 p-3">
            <Alert title={t("Could not open the folder")}>{(listing.error as Error).message}</Alert>
            <Button size="sm" variant="secondary" onClick={refresh}>{t("Retry")}</Button>
          </div>
        ) : null}
        {!listing.isLoading && !listing.error ? (
          view === "list" ? (
            <FileTable
              entries={visible}
              selected={selected}
              sort={sort}
              onSort={toggleSort}
              onSelect={select}
              onToggle={toggle}
              onOpen={activate}
              onContext={(entry, point) => setMenu({ entry, ...point })}
            />
          ) : (
            <FileGrid
              entries={visible}
              selected={selected}
              onSelect={select}
              onToggle={toggle}
              onOpen={activate}
              onContext={(entry, point) => setMenu({ entry, ...point })}
            />
          )
        ) : null}
      </div>

      {menu ? (
        <div
          ref={menuRef}
          role="menu"
          className="fixed z-[960] min-w-44 overflow-hidden rounded-md border border-border bg-card py-1 text-sm shadow-xl"
          style={{
            left: Math.max(8, Math.min(menu.x, window.innerWidth - 200)),
            top: Math.max(8, Math.min(menu.y, window.innerHeight - 230)),
          }}
          onMouseDown={(event) => event.stopPropagation()}
        >
          {menu.entry ? menuItems(menu.entry, {
            canWrite,
            onActivate: () => activate(menu.entry as Entry),
            onDownload: () => void download((menu.entry as Entry).name),
            onRename: () => askRename(menu.entry as Entry),
            onCompress: () => askCompress([(menu.entry as Entry).name]),
            onExtract: () => setExtracting((menu.entry as Entry).name),
            onDelete: () => setDeleting([menu.entry as Entry]),
            close: () => setMenu(null),
          }, t) : backgroundItems(canWrite, {
            onUpload: () => inputRef.current?.click(),
            onNewFolder: askNewFolder,
            onRefresh: refresh,
            onHome: () => go(""),
            close: () => setMenu(null),
          }, t)}
        </div>
      ) : null}

      <Dialog
        open={Boolean(editing)}
        title={editing?.path ?? t("Edit")}
        description={t("Text editor")}
        onClose={requestCloseEditor}
        maxWidthClassName="max-w-[94vw]"
        maxHeightClassName="max-h-[92vh]"
        actions={
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={requestCloseEditor}>{t("Cancel")}</Button>
            <Button onClick={() => void saveText()}>{t("Save")}</Button>
          </div>
        }
      >
        <textarea
          className="h-[65vh] min-h-[45vh] w-full resize-none rounded border border-border bg-background p-3 font-mono text-sm leading-relaxed"
          aria-label={t("File content")}
          spellCheck={false}
          value={editing?.content ?? ""}
          onChange={(event) => setEditing((current) => current && { ...current, content: event.target.value })}
        />
      </Dialog>

      <PromptDialog request={prompt} onClose={() => setPrompt(null)} />
      <ConflictDialog name={conflict?.name ?? null} onChoose={settleConflict} />
      <ConfirmDialog
        open={Boolean(deleting)}
        title={deleting?.length === 1 ? t("Delete {name}?", { name: deleting[0].name }) : t("Delete {count} items?", { count: deleting?.length ?? 0 })}
        description={t("Folders will be deleted with all their contents. This action cannot be undone.")}
        confirmLabel={t("Delete")}
        cancelLabel={t("Cancel")}
        destructive
        onClose={() => setDeleting(null)}
        onConfirm={() => void confirmDelete()}
      />
      <ConfirmDialog
        open={Boolean(extracting)}
        title={t("Extract {name}?", { name: extracting ?? "" })}
        description={t("It will be extracted to {destination}. Existing files will be skipped.", { destination: folder ? `/${folder}` : t("the root") })}
        confirmLabel={t("Extract")}
        cancelLabel={t("Cancel")}
        onClose={() => setExtracting(null)}
        onConfirm={confirmExtract}
      />
      <ConfirmDialog
        open={discarding}
        title={t("Discard changes?")}
        description={t("Edited content will be lost.")}
        confirmLabel={t("Discard")}
        cancelLabel={t("Cancel")}
        destructive
        onClose={() => setDiscarding(false)}
        onConfirm={() => {
          setDiscarding(false);
          setEditing(null);
        }}
      />
      {marqueeBox ? (
        <div
          className="pointer-events-none fixed z-[955] border border-primary bg-primary/10"
          style={{
            left: marqueeBox.x,
            top: marqueeBox.y,
            width: marqueeBox.width,
            height: marqueeBox.height,
          }}
        />
      ) : null}
    </FileManagerModal>
  );
}

type MenuActions = {
  canWrite: boolean;
  onActivate: () => void;
  onDownload: () => void;
  onRename: () => void;
  onCompress: () => void;
  onExtract: () => void;
  onDelete: () => void;
  close: () => void;
};

type Translator = (key: string) => string;

function uploadStatusLabel(status: string, t: Translator) {
  if (status === "Uploading") return t("Uploading");
  if (status === "Done") return t("Done");
  if (status === "Skipped") return t("Skipped");
  return status;
}

function menuItems(entry: Entry, actions: MenuActions, t: Translator) {
  const item = (label: string, run: () => void, disabled = false) => (
    <button
      key={label}
      type="button"
      role="menuitem"
      disabled={disabled}
      className="block w-full px-3 py-1.5 text-left hover:bg-accent disabled:cursor-not-allowed disabled:opacity-40"
      onClick={() => {
        actions.close();
        run();
      }}
    >
      {label}
    </button>
  );

  return (
    <>
      {item(entry.directory ? t("Open folder") : t("Open"), actions.onActivate)}
      {!entry.directory ? item(t("Download"), actions.onDownload) : null}
      {actions.canWrite ? item(t("Rename"), actions.onRename) : null}
      {actions.canWrite ? item(t("Compress to ZIP"), actions.onCompress) : null}
      {actions.canWrite && isZipFile(entry.name) && !entry.directory
        ? item(t("Extract here"), actions.onExtract)
        : null}
      {actions.canWrite ? item(t("Delete"), actions.onDelete) : null}
    </>
  );
}

type BackgroundActions = {
  onUpload: () => void;
  onNewFolder: () => void;
  onRefresh: () => void;
  onHome: () => void;
  close: () => void;
};

function backgroundItems(canWrite: boolean, actions: BackgroundActions, t: Translator) {
  const item = (key: string, label: ReactNode, run: () => void) => (
    <button
      key={key}
      type="button"
      role="menuitem"
      className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-accent"
      onClick={() => {
        actions.close();
        run();
      }}
    >
      {label}
    </button>
  );

  return (
    <>
      {canWrite ? item("upload", <><UploadIcon size={14} className="h-3.5 w-3.5" />{t("Upload files")}</>, actions.onUpload) : null}
      {canWrite ? item("new-folder", <><FolderPlusIcon size={14} className="h-3.5 w-3.5" />{t("New folder")}</>, actions.onNewFolder) : null}
      {item("refresh", <><RefreshIcon size={14} className="h-3.5 w-3.5" />{t("Refresh")}</>, actions.onRefresh)}
      {item("home", <><HomeIcon size={14} className="h-3.5 w-3.5" />{t("Go home")}</>, actions.onHome)}
    </>
  );
}
