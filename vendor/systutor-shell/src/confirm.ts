import { createContext, useContext } from "react";

export interface ConfirmRequest {
  message: string;
  renderPreview?: unknown;
  onConfirm: () => Promise<unknown>;
  onCancel?: () => string;
}

export interface ConfirmContextType {
  requestConfirm: (req: ConfirmRequest) => void;
  clearConfirm: () => void;
}

export const ConfirmContext = createContext<ConfirmContextType | null>(null);

export function useConfirm(): ConfirmContextType {
  const ctx = useContext(ConfirmContext);
  if (!ctx) {
    throw new Error("useConfirm debe usarse dentro de un ConsoleShell");
  }
  return ctx;
}

/** Resultado especial que una página puede retornar desde onExecute
 *  para activar el flujo de confirmación en ConsoleShell. */
export interface ConfirmAction {
  _confirm: true;
  previewResult: unknown;
  confirmMessage: string;
  execute: () => Promise<unknown>;
  cancelMessage?: string;
}

export function isConfirmAction(result: unknown): result is ConfirmAction {
  return typeof result === "object" && result !== null && (result as any)._confirm === true;
}
