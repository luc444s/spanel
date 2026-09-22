import type { languages } from "monaco-editor";
import type { TokenProvider } from "./ConsoleEditor.types";

export function buildMonarchLanguage(provider: TokenProvider): languages.IMonarchLanguage {
  const keywordPattern = provider.ignoreCase
    ? new RegExp(`\\b(${provider.keywords.join("|")})\\b`, "i")
    : new RegExp(`\\b(${provider.keywords.join("|")})\\b`);

  const rules: languages.IMonarchLanguageRule[] = [[keywordPattern, "keyword"]];

  for (const rule of provider.tokens) {
    rules.push([rule.pattern, rule.token] as languages.IMonarchLanguageRule);
  }

  rules.push(
    [/#.*$/, "comment"],
    [/\b\d+(\.\d+)?\b/, "number"],
    [/"[^"]*"/, "string"],
    [/[:,;]/, "operator"],
  );

  return {
    ignoreCase: provider.ignoreCase ?? false,
    tokenizer: {
      root: rules,
    },
  };
}

export function buildLanguageConfiguration(): languages.LanguageConfiguration {
  return {
    brackets: [["[", "]"]],
    autoClosingPairs: [
      { open: "[", close: "]" },
      { open: '"', close: '"' },
    ],
    surroundingPairs: [
      { open: "[", close: "]" },
      { open: '"', close: '"' },
    ],
  };
}
