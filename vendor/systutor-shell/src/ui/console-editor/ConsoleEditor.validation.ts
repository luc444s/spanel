import { editor } from "monaco-editor";
import type { ValidationProvider, ValidationMarker } from "./ConsoleEditor.types";

const SEVERITY_MAP = {
  error: 8,
  warning: 4,
  info: 2,
} as const;

export function buildValidationDecorator(
  provider: ValidationProvider,
): (model: editor.ITextModel) => void {
  return (model: editor.ITextModel) => {
    const text = model.getValue();
    const markers = provider.provideMarkers(text);
    const monacoMarkers: editor.IMarkerData[] = markers.map(toMonacoMarker);
    editor.setModelMarkers(model, provider.language, monacoMarkers);
  };
}

function toMonacoMarker(marker: ValidationMarker): editor.IMarkerData {
  return {
    message: marker.message,
    severity: SEVERITY_MAP[marker.severity],
    startLineNumber: marker.startLine,
    startColumn: marker.startColumn,
    endLineNumber: marker.endLine,
    endColumn: marker.endColumn,
  };
}
