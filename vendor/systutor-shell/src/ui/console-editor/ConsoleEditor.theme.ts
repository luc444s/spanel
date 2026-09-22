import type { editor } from "monaco-editor";

export const CONSOLE_THEME: editor.IStandaloneThemeData = {
  base: "vs-dark",
  inherit: true,
  rules: [
    { token: "keyword", foreground: "C586C0", fontStyle: "bold" },
    { token: "entity", foreground: "4FC1FF" },
    { token: "entity.customer", foreground: "4FC1FF" },
    { token: "entity.product", foreground: "4EC9B0" },
    { token: "entity.vehicle", foreground: "DCDCAA" },
    { token: "value", foreground: "CE9178" },
    { token: "value.quantity", foreground: "B5CEA8" },
    { token: "value.date", foreground: "569CD6" },
    { token: "value.time", foreground: "569CD6" },
    { token: "value.price", foreground: "B5CEA8" },
    { token: "operator", foreground: "D4D4D4" },
    { token: "string", foreground: "CE9178" },
    { token: "number", foreground: "B5CEA8" },
    { token: "comment", foreground: "6A9955", fontStyle: "italic" },
    { token: "invalid", foreground: "F44747", fontStyle: "italic" },
  ],
  colors: {
    "editor.background": "#0D1117",
    "editor.foreground": "#C9D1D9",
    "editor.lineHighlightBackground": "#161B22",
    "editor.selectionBackground": "#264F78",
    "editorCursor.foreground": "#58A6FF",
    "editorLineNumber.foreground": "#484F58",
    "editorLineNumber.activeForeground": "#C9D1D9",
    "editorWidget.background": "#161B22",
    "editorWidget.border": "#30363D",
    "editorSuggestWidget.background": "#161B22",
    "editorSuggestWidget.border": "#30363D",
    "editorSuggestWidget.selectedBackground": "#1F6FEB33",
    "editorSuggestWidget.highlightForeground": "#58A6FF",
    "editorHoverWidget.background": "#161B22",
    "editorHoverWidget.border": "#30363D",
  },
};

export const CONSOLE_THEME_NAME = "systutor-console";
