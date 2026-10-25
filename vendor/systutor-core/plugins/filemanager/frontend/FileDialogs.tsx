import { useEffect, useState } from "react";

import { Button } from "@systutor/shell/ui/button";
import { Dialog } from "@systutor/shell/ui/dialog";
import { Input } from "@systutor/shell/ui/input";

export type PromptRequest = {
  title: string;
  description?: string;
  label: string;
  initial?: string;
  confirmLabel?: string;
  action: (value: string) => void;
};

export function PromptDialog({ request, onClose }: {
  request: PromptRequest | null;
  onClose: () => void;
}) {
  const [value, setValue] = useState(request?.initial ?? "");

  useEffect(() => {
    setValue(request?.initial ?? "");
  }, [request]);

  if (!request) return null;
  const trimmed = value.trim();
  const submit = () => {
    if (!trimmed) return;
    request.action(trimmed);
    onClose();
  };

  return (
    <Dialog
      open
      title={request.title}
      description={request.description}
      onClose={onClose}
      maxWidthClassName="max-w-md"
      actions={
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>Cancelar</Button>
          <Button onClick={submit} disabled={!trimmed}>{request.confirmLabel ?? "Aceptar"}</Button>
        </div>
      }
    >
      <label className="block space-y-1 text-sm">
        <span className="text-muted-foreground">{request.label}</span>
        <Input
          autoFocus
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") submit();
          }}
        />
      </label>
    </Dialog>
  );
}

export type ConflictChoice =
  | { action: "replace" }
  | { action: "rename"; name: string }
  | { action: "skip" };

export function ConflictDialog({ name, onChoose }: {
  name: string | null;
  onChoose: (choice: ConflictChoice) => void;
}) {
  const [renaming, setRenaming] = useState(false);
  const [newName, setNewName] = useState(name ?? "");

  useEffect(() => {
    setRenaming(false);
    setNewName(name ?? "");
  }, [name]);

  if (!name) return null;
  const trimmed = newName.trim();
  const choose = (choice: ConflictChoice) => {
    onChoose(choice);
    setRenaming(false);
  };

  return (
    <Dialog
      open
      title="El archivo ya existe"
      description={`${name} ya está en esta carpeta. ¿Qué querés hacer?`}
      onClose={() => choose({ action: "skip" })}
      maxWidthClassName="max-w-md"
      actions={
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="secondary" onClick={() => choose({ action: "skip" })}>Omitir</Button>
          {renaming ? (
            <>
              <Button variant="secondary" onClick={() => setRenaming(false)}>Volver</Button>
              <Button disabled={!trimmed || trimmed === name} onClick={() => choose({ action: "rename", name: trimmed })}>
                Subir con este nombre
              </Button>
            </>
          ) : (
            <>
              <Button variant="secondary" onClick={() => setRenaming(true)}>Renombrar</Button>
              <Button onClick={() => choose({ action: "replace" })}>Reemplazar</Button>
            </>
          )}
        </div>
      }
    >
      {renaming ? (
        <label className="block space-y-1 text-sm">
          <span className="text-muted-foreground">Nuevo nombre</span>
          <Input autoFocus value={newName} onChange={(event) => setNewName(event.target.value)} />
        </label>
      ) : (
        <p className="text-sm text-muted-foreground">
          Reemplazar sobrescribe el contenido actual. Renombrar sube el archivo con otro nombre.
        </p>
      )}
    </Dialog>
  );
}
