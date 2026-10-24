import { useCallback, useRef, useState, type ChangeEvent, type DragEvent } from "react";
import { useParams, Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { apiRequest, getApiBaseUrl } from "@systutor/shell/api/client";
import { Alert } from "@systutor/shell/ui/alert";
import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { toast } from "@systutor/shell/ui/toast";
import { useAuthStore } from "../../../../../apps/web/src/features/auth/store";

type Entry = { name: string; directory: boolean; size: number | null; modified: number };
type Listing = { entries: Entry[] };
type TextFile = { content: string; version: string };
type Progress = { name: string; percent: number; status: string };

function targetUrl(provider: string, id: string) {
  return `/api/v1/plugins/filemanager/targets/${encodeURIComponent(provider)}/${encodeURIComponent(id)}`;
}

function within(folder: string, name: string) {
  return folder ? `${folder}/${name}` : name;
}

export default function FileManagerPage() {
  const { provider = "", resourceId = "" } = useParams();
  const permissions = useAuthStore((state) => state.permissions);
  const canWrite = permissions.includes("filemanager.files.manage");
  const token = useAuthStore((state) => state.token);
  const base = targetUrl(provider, resourceId);
  const queryClient = useQueryClient();
  const [folder, setFolder] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [editing, setEditing] = useState<{ path: string; version: string; content: string } | null>(null);
  const [progress, setProgress] = useState<Progress[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const key = ["filemanager", provider, resourceId, folder];
  const listing = useQuery({
    queryKey: key,
    queryFn: () => apiRequest<Listing>(`${base}/entries?path=${encodeURIComponent(folder)}`),
  });
  const refresh = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["filemanager", provider, resourceId] });
  }, [queryClient, provider, resourceId]);

  function go(path: string) {
    setSelected([]);
    setFolder(path);
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
      setEditing({ path, ...file });
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

  async function download(name: string) {
    try {
      const path = within(folder, name);
      const { url } = await apiRequest<{ url: string }>(
        `${base}/download-tickets?path=${encodeURIComponent(path)}`, { method: "POST" },
      );
      const a = document.createElement("a");
      a.href = `${getApiBaseUrl()}${url}`;
      a.click();
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
          setProgress((items) => items.map((item) =>
            item.name === name ? { ...item, percent: Math.round(event.loaded / event.total * 100) } : item));
        }
      };
      xhr.onload = () => xhr.status >= 200 && xhr.status < 300
        ? resolve() : reject(new Error(xhr.status === 409 ? "Ya existe" : `Error HTTP ${xhr.status}`));
      xhr.onerror = () => reject(new Error("Falló la conexión"));
      const data = new FormData();
      data.append("file", file, name);
      xhr.send(data);
    });
  }

  async function uploadFiles(files: FileList | File[]) {
    const batch = Array.from(files);
    setProgress(batch.map((file) => ({ name: file.name, percent: 0, status: "Subiendo" })));
    for (const file of batch) {
      let name = file.name;
      try {
        try {
          await sendFile(file);
        } catch (error) {
          if ((error as Error).message !== "Ya existe") throw error;
          const choice = window.prompt(
            `${name} ya existe. Escribí reemplazar, renombrar u omitir:`, "omitir",
          )?.toLowerCase();
          if (choice === "reemplazar") await sendFile(file, name, true);
          else if (choice === "renombrar") {
            const alternative = window.prompt("Nuevo nombre del archivo:", name);
            if (!alternative) throw new Error("Omitido");
            name = alternative;
            await sendFile(file, name);
          } else throw new Error("Omitido");
        }
        setProgress((items) => items.map((item) => item.name === file.name
          ? { ...item, percent: 100, status: "Listo" } : item));
      } catch (error) {
        setProgress((items) => items.map((item) => item.name === file.name
          ? { ...item, status: (error as Error).message } : item));
      }
    }
    refresh();
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    if (canWrite) void uploadFiles(event.dataTransfer.files);
  }

  function onFileChange(event: ChangeEvent<HTMLInputElement>) {
    if (event.target.files) void uploadFiles(event.target.files);
    event.target.value = "";
  }

  function rename(entry: Entry) {
    const name = window.prompt("Nuevo nombre:", entry.name);
    if (name && name !== entry.name) void change("/entries", "PATCH", { path: within(folder, entry.name), name });
  }

  function remove(entry: Entry) {
    if (window.confirm(`¿Borrar ${entry.name}${entry.directory ? " y todo su contenido" : ""}?`)) {
      void change(`/entries?path=${encodeURIComponent(within(folder, entry.name))}`, "DELETE");
    }
  }

  function compress() {
    const name = window.prompt("Nombre del ZIP:", "archivos.zip");
    if (!name || !selected.length) return;
    void change("/archives", "POST", { path: folder, name, entries: selected });
    setSelected([]);
  }

  function extract(name: string) {
    if (window.confirm(`¿Descomprimir ${name} en esta carpeta? Los archivos existentes se omitirán.`)) {
      void change("/archives/extract", "POST", { path: within(folder, name), destination: folder });
    }
  }

  const crumbs = folder.split("/").filter(Boolean);
  return (
    <section className="space-y-4" onDragOver={(event) => event.preventDefault()} onDrop={onDrop}>
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <Link to="/app/hosting" className="text-sm text-primary underline">← Hosting</Link>
          <h1 className="text-lg font-semibold">Gestor de archivos</h1>
          <p className="text-xs text-muted-foreground">Sitio {resourceId}</p>
        </div>
        {canWrite ? <div className="flex flex-wrap gap-2">
          <input ref={inputRef} className="hidden" type="file" multiple onChange={onFileChange} />
          <Button onClick={() => inputRef.current?.click()}>Subir archivos</Button>
          <Button variant="secondary" onClick={() => {
            const name = window.prompt("Nombre de la carpeta:");
            if (name) void change("/directories", "POST", { path: within(folder, name) });
          }}>Nueva carpeta</Button>
          <Button variant="secondary" disabled={!selected.length} onClick={compress}>Comprimir ZIP</Button>
        </div> : null}
      </header>
      <nav aria-label="Ruta" className="flex flex-wrap gap-1 text-sm">
        <button type="button" className="text-primary underline" onClick={() => go("")}>Inicio</button>
        {crumbs.map((part, index) => <span key={index}>
          <span className="mx-1">/</span>
          <button type="button" className="text-primary underline"
            onClick={() => go(crumbs.slice(0, index + 1).join("/"))}>{part}</button>
        </span>)}
      </nav>
      <p className="rounded border border-dashed border-border p-3 text-sm text-muted-foreground">
        {canWrite ? "Arrastrá archivos aquí para subirlos a esta carpeta." : "Explorando archivos en modo lectura."}
      </p>
      {progress.length ? <ul className="text-xs" aria-label="Subidas">{progress.map((item, index) =>
        <li key={`${item.name}-${index}`}>{item.name}: {item.percent}% — {item.status}</li>)}</ul> : null}
      {listing.isLoading ? <p>Cargando archivos...</p> : null}
      {listing.error ? <Alert title="No se pudo abrir la carpeta">{(listing.error as Error).message}</Alert> : null}
      <div className="overflow-x-auto rounded border border-border">
        <table className="w-full text-left text-sm">
          <thead className="bg-muted"><tr><th className="p-2">Seleccionar</th><th className="p-2">Nombre</th>
            <th className="p-2">Tamaño</th><th className="p-2">Acciones</th></tr></thead>
          <tbody>{listing.data?.entries.map((entry) => <tr key={entry.name} className="border-t border-border">
            <td className="p-2"><input type="checkbox" aria-label={`Seleccionar ${entry.name}`}
              checked={selected.includes(entry.name)} onChange={(event) =>
                setSelected((current) => event.target.checked
                  ? [...current, entry.name] : current.filter((name) => name !== entry.name))} /></td>
            <td className="p-2 break-all">{entry.directory ? "📁 " : "📄 "}
              {entry.directory ? <button className="text-primary underline" onClick={() => go(within(folder, entry.name))}>
                {entry.name}</button> : entry.name}</td>
            <td className="p-2">{entry.size ?? "—"}</td>
            <td className="p-2"><div className="flex flex-wrap gap-1">
              {!entry.directory ? <Button size="sm" variant="secondary" onClick={() => void download(entry.name)}>Descargar</Button> : null}
              {canWrite && !entry.directory ? <Button size="sm" variant="secondary"
                onClick={() => void openText(entry.name)}>Editar</Button> : null}
              {canWrite && entry.name.toLowerCase().endsWith(".zip") && !entry.directory
                ? <Button size="sm" variant="secondary" onClick={() => extract(entry.name)}>Descomprimir</Button> : null}
              {canWrite ? <><Button size="sm" variant="secondary" onClick={() => rename(entry)}>Renombrar</Button>
                <Button size="sm" variant="secondary" onClick={() => remove(entry)}>Borrar</Button></> : null}
            </div></td>
          </tr>)}</tbody>
        </table>
      </div>
      <Dialog open={Boolean(editing)} title={editing?.path ?? "Editar"} onClose={() => {
        if (!editing || window.confirm("¿Descartar los cambios sin guardar?")) setEditing(null);
      }}>
        <textarea className="h-80 w-full rounded border border-border bg-background p-2 font-mono text-sm"
          aria-label="Contenido del archivo" value={editing?.content ?? ""} onChange={(event) =>
            setEditing((current) => current && { ...current, content: event.target.value })} />
        <div className="mt-2 flex justify-end gap-2"><Button variant="secondary" onClick={() => setEditing(null)}>Cancelar</Button>
          <Button onClick={() => void saveText()}>Guardar</Button></div>
      </Dialog>
    </section>
  );
}
