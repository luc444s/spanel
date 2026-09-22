import type { editor, Position, CancellationToken } from "monaco-editor";
import { languages } from "monaco-editor";
import type { CompletionItem, CompletionProvider, SuggestionResult } from "./ConsoleEditor.types";

export function buildCompletionProvider(
  provider: CompletionProvider,
): languages.CompletionItemProvider {
  return {
    provideCompletionItems(
      model: editor.ITextModel,
      position: Position,
      _context: languages.CompletionContext,
      _token: CancellationToken,
    ): languages.ProviderResult<languages.CompletionList> {
      const fullText = model.getValue();
      const textBeforeCursor = model.getValueInRange({
        startLineNumber: position.lineNumber,
        startColumn: 1,
        endLineNumber: position.lineNumber,
        endColumn: position.column,
      });

      const result = provider.provideItems({ textBeforeCursor, fullText });

      return normalizeResult(result, model, position);
    },

    resolveCompletionItem(
      item: languages.CompletionItem,
      _token: CancellationToken,
    ): languages.ProviderResult<languages.CompletionItem> {
      return item;
    },
  };
}

function normalizeResult(
  result: CompletionItem[] | SuggestionResult | Promise<CompletionItem[]>,
  model: editor.ITextModel,
  position: Position,
): languages.CompletionList | Promise<languages.CompletionList> {
  if (result instanceof Promise) {
    return result.then((items) => buildCompletionList(items, false, model, position));
  }

  if (Array.isArray(result)) {
    return buildCompletionList(result, false, model, position);
  }

  return buildCompletionList(result.items, result.incomplete, model, position);
}

function buildCompletionList(
  items: CompletionItem[],
  incomplete: boolean,
  model: editor.ITextModel,
  position: Position,
): languages.CompletionList {
  if (items.length === 0) {
    return { suggestions: [], incomplete };
  }

  const word = model.getWordUntilPosition(position);

  return {
    suggestions: items.map((item) => ({
      label: item.label,
      kind: mapKind(item.kind),
      detail: item.detail,
      documentation: item.documentation,
      insertText: item.insertText ?? item.label,
      sortText: item.sortText,
      filterText: item.filterText ?? item.label.toLowerCase(),
      range: {
        startLineNumber: position.lineNumber,
        endLineNumber: position.lineNumber,
        startColumn: word.startColumn,
        endColumn: word.endColumn,
      },
    })),
    incomplete,
  };
}

function mapKind(kind?: CompletionItem["kind"]): languages.CompletionItemKind {
  switch (kind) {
    case "keyword":
      return languages.CompletionItemKind.Keyword;
    case "entity":
      return languages.CompletionItemKind.Class;
    case "value":
      return languages.CompletionItemKind.Value;
    case "snippet":
      return languages.CompletionItemKind.Snippet;
    default:
      return languages.CompletionItemKind.Text;
  }
}
