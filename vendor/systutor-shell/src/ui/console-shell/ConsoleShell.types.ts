import type { ReactNode } from "react";
import type { CompletionProvider, TokenProvider, ValidationProvider } from "../console-editor/ConsoleEditor.types";

export interface ConsoleShellProps {
  completionProvider: CompletionProvider;
  tokenProvider: TokenProvider;
  validationProvider?: ValidationProvider;
  onExecute: (command: string) => Promise<unknown> | unknown;
  renderResult: (data: unknown) => ReactNode;
  placeholder?: string;
}
