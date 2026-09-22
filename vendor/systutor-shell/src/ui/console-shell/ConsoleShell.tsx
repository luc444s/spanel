import { useState, useRef, useCallback, useEffect } from "react";
import type { editor } from "monaco-editor";
import { ConsoleEditor } from "../console-editor";
import type { ConsoleShellProps } from "./ConsoleShell.types";
import { SYSTUTOR_NEOFETCH, isNeofetchCommand } from "../../neofetch";
import { ConfirmContext } from "../../confirm";
import type { ConfirmRequest, ConfirmAction } from "../../confirm";
import { isConfirmAction } from "../../confirm";

export function ConsoleShell({
  completionProvider,
  tokenProvider,
  validationProvider,
  onExecute,
  renderResult,
  placeholder,
}: ConsoleShellProps) {
  const [history, setHistory] = useState<string[]>([]);
  const [historyIndex, setHistoryIndex] = useState(-1);
  const [result, setResult] = useState<unknown | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isAsciiArt, setIsAsciiArt] = useState(false);
  const [confirmRequest, setConfirmRequest] = useState<ConfirmRequest | null>(null);
  const outputRef = useRef<HTMLDivElement>(null);
  const editorRef = useRef<editor.IStandaloneCodeEditor | null>(null);
  const currentInputRef = useRef("");
  const historyRef = useRef(history);
  const historyIndexRef = useRef(historyIndex);

  function clearEditor() {
    editorRef.current?.setValue("");
    currentInputRef.current = "";
  }

  useEffect(() => {
    historyRef.current = history;
  }, [history]);

  useEffect(() => {
    historyIndexRef.current = historyIndex;
  }, [historyIndex]);

  const handleConfirmResponse = useCallback(
    async (response: string) => {
      const trimmed = response.trim();
      const lower = trimmed.toLowerCase();
      if (lower === "y" || lower === "yes") {
        setResult(null);
        setError(null);
        setConfirmRequest(null);
        clearEditor();
        try {
          const data = await confirmRequest!.onConfirm();
          setResult(data ?? null);
        } catch (err) {
          setError(err instanceof Error ? err.message : "Error al confirmar");
        }
      } else {
        const cancelMsg = confirmRequest?.onCancel?.() ?? "Cancelado";
        setResult(cancelMsg);
        setError(null);
        setConfirmRequest(null);
        clearEditor();
      }
      if (outputRef.current) {
        outputRef.current.scrollTop = outputRef.current.scrollHeight;
      }
    },
    [confirmRequest],
  );

  const requestConfirm = useCallback((req: ConfirmRequest) => {
    setResult(req.renderPreview ?? req.message);
    setConfirmRequest(req);
    setError(null);
    if (outputRef.current) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight;
    }
  }, []);

  const clearConfirm = useCallback(() => {
    setConfirmRequest(null);
  }, []);

  const handleExecute = useCallback(
    async (command: string) => {
      const trimmed = command.trim();
      if (!trimmed) return;

      if (confirmRequest) {
        await handleConfirmResponse(trimmed);
        return;
      }

      if (trimmed === "clear") {
        setHistory([]);
        setResult(null);
        setError(null);
        setConfirmRequest(null);
        clearEditor();
        return;
      }

      if (trimmed === "history") {
        setResult(historyRef.current.length > 0 ? historyRef.current.map((c, i) => `${i + 1}  ${c}`).join("\n") : "(sin historial)");
        setError(null);
        setHistory((prev) => [...prev, trimmed]);
        setHistoryIndex(-1);
        clearEditor();
        return;
      }

      if (isNeofetchCommand(trimmed)) {
        setResult(SYSTUTOR_NEOFETCH);
        setIsAsciiArt(true);
        setError(null);
        setHistory((prev) => [...prev, trimmed]);
        setHistoryIndex(-1);
        clearEditor();
        return;
      }

      setHistory((prev) => [...prev, trimmed]);
      setHistoryIndex(-1);
      setError(null);
      setResult(null);
      setIsAsciiArt(false);

      try {
        const data = await onExecute(trimmed);
        if (isConfirmAction(data)) {
          setResult(data.previewResult);
          setConfirmRequest({
            message: data.confirmMessage,
            renderPreview: data.previewResult,
            onConfirm: data.execute,
            onCancel: () => data.cancelMessage ?? "Cancelado",
          });
        } else {
          setResult(data ?? null);
        }
        clearEditor();
      } catch (err) {
        setError(err instanceof Error ? err.message : "Error al ejecutar comando");
        clearEditor();
      }

      if (outputRef.current) {
        outputRef.current.scrollTop = outputRef.current.scrollHeight;
      }
    },
    [onExecute, confirmRequest, handleConfirmResponse],
  );

  const handleNavigate = useCallback((direction: "up" | "down") => {
    if (direction === "up") {
      if (historyRef.current.length === 0) return;
      const newIndex = historyIndexRef.current === -1 ? historyRef.current.length - 1 : Math.max(0, historyIndexRef.current - 1);
      setHistoryIndex(newIndex);
      const value = historyRef.current[newIndex];
      currentInputRef.current = value;
      editorRef.current?.setValue(value);
    } else {
      if (historyIndexRef.current === -1) return;
      const newIndex = historyIndexRef.current + 1;
      if (newIndex >= historyRef.current.length) {
        setHistoryIndex(-1);
        currentInputRef.current = "";
        editorRef.current?.setValue("");
      } else {
        setHistoryIndex(newIndex);
        const value = historyRef.current[newIndex];
        currentInputRef.current = value;
        editorRef.current?.setValue(value);
      }
    }
  }, []);

  const handleChange = useCallback((value: string) => {
    currentInputRef.current = value;
  }, []);

  const promptLabel = confirmRequest ? "y/n" : placeholder ?? "escribí un comando...";

  return (
    <ConfirmContext.Provider value={{ requestConfirm, clearConfirm }}>
      <div
        className="flex flex-col bg-[#0D1117] font-mono text-sm text-[#C9D1D9]"
        style={{ maxHeight: "800px", height: "calc(100vh - 12rem)", minHeight: "300px" }}
      >
        <div ref={outputRef} className="flex-1 overflow-y-auto px-4 py-2">
          {history.map((cmd, i) => (
            <div key={i} className="leading-relaxed">
              <span className="text-primary select-none">&gt; </span>
              <span className="text-muted-foreground/70">{cmd}</span>
            </div>
          ))}

          {confirmRequest && (
            <div className="my-1">
              <pre className="whitespace-pre-wrap text-xs leading-relaxed text-yellow-400">
                {confirmRequest.message}
              </pre>
              <span className="text-yellow-400 text-xs">Confirmar? (y/n)</span>
            </div>
          )}

          {result != null && !confirmRequest && (
            <div className="my-1">
              {typeof result === "string" ? (
                <pre className={`whitespace-pre-wrap text-xs ${isAsciiArt ? "leading-none" : "leading-relaxed"}`}>{result}</pre>
              ) : (
                renderResult(result)
              )}
            </div>
          )}
          {error && (
            <div className="my-1 text-red-400">{"\u2717"} {error}</div>
          )}
        </div>

        <div className="flex items-start gap-2 px-4 py-2">
          <span className="mt-0.5 shrink-0 text-primary select-none">&gt;</span>
          <div className="flex-1">
            <ConsoleEditor
              completionProvider={completionProvider}
              tokenProvider={tokenProvider}
              validationProvider={validationProvider}
              onExecute={handleExecute}
              onChange={handleChange}
              onNavigate={handleNavigate}
              editorRef={editorRef}
              config={{
                fontSize: 13,
                lineNumbers: "off",
                minimap: false,
                wordWrap: "off",
                placeholder: promptLabel,
              }}
            />
          </div>
        </div>
      </div>
    </ConfirmContext.Provider>
  );
}
