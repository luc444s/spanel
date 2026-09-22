import type { editor } from "monaco-editor";
import type { MutableRefObject } from "react";

export interface CompletionItem {
  label: string;
  detail?: string;
  insertText?: string;
  kind?: "keyword" | "entity" | "value" | "snippet";
  documentation?: string;
  sortText?: string;
  filterText?: string;
}

export type SuggestionResult = {
  items: CompletionItem[];
  incomplete: boolean;
};

export type CompletionContext = {
  textBeforeCursor: string;
  fullText: string;
};

export interface CompletionProvider {
  language: string;
  provideItems: (ctx: CompletionContext) => CompletionItem[] | SuggestionResult | Promise<CompletionItem[]>;
}

export interface TokenRule {
  pattern: RegExp | string;
  token: string;
}

export interface TokenProvider {
  language: string;
  keywords: string[];
  tokens: TokenRule[];
  ignoreCase?: boolean;
}

export interface ValidationMarker {
  message: string;
  severity: "error" | "warning" | "info";
  startLine: number;
  startColumn: number;
  endLine: number;
  endColumn: number;
}

export interface ValidationProvider {
  language: string;
  provideMarkers: (text: string) => ValidationMarker[];
}

export interface ConsoleConfig {
  fontSize?: number;
  lineNumbers?: "on" | "off";
  minimap?: boolean;
  wordWrap?: "on" | "off";
  placeholder?: string;
  readOnly?: boolean;
  lineHeight?: number;
  maxHeight?: number;
}

export interface ConsoleEditorProps {
  completionProvider: CompletionProvider;
  tokenProvider: TokenProvider;
  validationProvider?: ValidationProvider;
  onExecute: (command: string) => void;
  onChange?: (value: string) => void;
  onNavigate?: (direction: "up" | "down") => void;
  config?: ConsoleConfig;
  editorRef?: MutableRefObject<editor.IStandaloneCodeEditor | null>;
}
