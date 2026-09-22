import { useRef, useEffect, memo, useCallback, useMemo } from "react";
import MonacoEditor, { loader } from "@monaco-editor/react";
import { KeyMod, KeyCode } from "monaco-editor";
import type { ConsoleEditorProps } from "./ConsoleEditor.types";
import { buildCompletionProvider } from "./ConsoleEditor.completion";
import { buildMonarchLanguage, buildLanguageConfiguration } from "./ConsoleEditor.tokens";
import { buildValidationDecorator } from "./ConsoleEditor.validation";
import { CONSOLE_THEME, CONSOLE_THEME_NAME } from "./ConsoleEditor.theme";

loader.config({ paths: { vs: "https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs" } });

const registeredLanguages = new Set<string>();

function ConsoleEditorInner({
  completionProvider,
  tokenProvider,
  validationProvider,
  onExecute,
  onChange,
  onNavigate,
  config,
  editorRef,
}: ConsoleEditorProps) {
  const onExecuteRef = useRef(onExecute);
  const onNavigateRef = useRef(onNavigate);
  const disposables = useRef<IDisposable[]>([]);
  onExecuteRef.current = onExecute;
  onNavigateRef.current = onNavigate;

  const options = useMemo(
    () => ({
      fontSize: config?.fontSize ?? 13,
      fontFamily: "'JetBrains Mono', 'Geist Variable', monospace",
      lineNumbers: "off" as const,
      minimap: { enabled: false },
      wordWrap: "off" as const,
      readOnly: false,
      lineHeight: config?.lineHeight ?? 22,
      scrollBeyondLastLine: false,
      renderLineHighlight: "none" as const,
      cursorBlinking: "solid" as const,
      cursorStyle: "line" as const,
      smoothScrolling: false,
      folding: false,
      glyphMargin: false,
      lineDecorationsWidth: 0,
      lineNumbersMinChars: 0,
      overviewRulerBorder: false,
      overviewRulerLanes: 0,
      hideCursorInOverviewRuler: true,
      renderWhitespace: "none" as const,
      contextmenu: false,
      quickSuggestions: true,
      suggestOnTriggerCharacters: true,
      acceptSuggestionOnEnter: "off" as const,
      tabCompletion: "off" as const,
      wordBasedSuggestions: "off" as const,
      parameterHints: { enabled: false },
      padding: { top: 6, bottom: 6 },
      bracketPairColorization: { enabled: false },
      matchBrackets: "never" as const,
      autoClosingBrackets: "never" as const,
      autoClosingQuotes: "never" as const,
      autoSurround: "never" as const,
      colorDecorators: false,
      links: false,
      occurrencesHighlight: "off" as const,
      selectionHighlight: false,
      renderControlCharacters: false,
      guides: { indentation: false, bracketPairs: false },
      stickyScroll: { enabled: false },
      scrollbar: { vertical: "hidden" as const, horizontal: "hidden" as const },
    }),
    [config?.fontSize, config?.lineHeight],
  );

  const height = useMemo(
    () => (config?.maxHeight ? `${config.maxHeight}px` : "48px"),
    [config?.maxHeight],
  );

  const handleBeforeMount = useCallback(
    (monaco: typeof import("monaco-editor")) => {
      const { languages: ml } = monaco;

      monaco.editor.defineTheme(CONSOLE_THEME_NAME, CONSOLE_THEME);

      const langId = tokenProvider.language;
      if (!registeredLanguages.has(langId)) {
        ml.register({ id: langId });
        ml.setMonarchTokensProvider(langId, buildMonarchLanguage(tokenProvider));
        ml.setLanguageConfiguration(langId, buildLanguageConfiguration());
        registeredLanguages.add(langId);
      }

      const completionDisposable = ml.registerCompletionItemProvider(
        completionProvider.language,
        buildCompletionProvider(completionProvider),
      );
      disposables.current.push(completionDisposable);
    },
    [completionProvider, tokenProvider],
  );

  const handleMount = useCallback(
    (editorInstance: import("monaco-editor").editor.IStandaloneCodeEditor) => {
      if (editorRef) {
        editorRef.current = editorInstance;
      }

      if (validationProvider) {
        const validate = buildValidationDecorator(validationProvider);
        const model = editorInstance.getModel();
        if (model) validate(model);
        disposables.current.push(
          editorInstance.onDidChangeModelContent(() => {
            const m = editorInstance.getModel();
            if (m) validate(m);
          }),
        );
      }

      editorInstance.addAction({
        id: "execute-console-command",
        label: "Execute",
        keybindings: [KeyMod.CtrlCmd | KeyCode.Enter],
        run: (ed) => onExecuteRef.current(ed.getValue()),
      });

      editorInstance.addAction({
        id: "history-up",
        label: "History Up",
        keybindings: [KeyCode.UpArrow],
        run: () => onNavigateRef.current?.("up"),
      });

      editorInstance.addAction({
        id: "history-down",
        label: "History Down",
        keybindings: [KeyCode.DownArrow],
        run: () => onNavigateRef.current?.("down"),
      });

      editorInstance.focus();
    },
    [completionProvider, validationProvider, editorRef, onNavigate],
  );

  const handleChange = useMemo(
    () => (onChange ? (value: string | undefined) => onChange(value ?? "") : undefined),
    [onChange],
  );

  useEffect(() => {
    return () => {
      for (const d of disposables.current) d.dispose();
      disposables.current = [];
    };
  }, []);

  return (
    <MonacoEditor
      height={height}
      language={tokenProvider.language}
      theme={CONSOLE_THEME_NAME}
      options={options}
      beforeMount={handleBeforeMount}
      onMount={handleMount}
      onChange={handleChange}
      loading={null}
    />
  );
}

export const ConsoleEditor = memo(ConsoleEditorInner);

type IDisposable = { dispose(): void };
