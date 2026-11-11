import { useMemo, useState } from "react";

import { Button } from "@systutor/shell/ui/button";
import { Input } from "@systutor/shell/ui/input";

import { useT } from "@/i18n/use-i18n";

import type { Operation } from "../types";

export function ServiceLogs({ operation, canManage, pending, onRequest }: { operation?: Operation; canManage: boolean; pending: boolean; onRequest: () => void }) {
  const t = useT();
  const [lines, setLines] = useState("200");
  const [search, setSearch] = useState("");
  const output = String(operation?.result?.output ?? t("Request logs; the redacted result will appear here."));
  const shown = useMemo(() => output.split("\n").filter((line) => line.toLowerCase().includes(search.toLowerCase())).slice(-Number(lines)).join("\n"), [output, search, lines]);
  return <div className="space-y-3"><div className="flex flex-wrap gap-2"><select className="rounded border border-input bg-background px-2" value={lines} onChange={(event) => setLines(event.target.value)}><option>100</option><option>200</option><option>500</option></select><Input aria-label={t("Search logs")} placeholder={t("Search")} value={search} onChange={(event) => setSearch(event.target.value)} />{canManage ? <Button disabled={pending} onClick={onRequest}>{t("Request logs")}</Button> : null}</div><pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded border border-border bg-muted p-3 text-xs">{shown}</pre><p className="text-xs text-muted-foreground">{t("Known secrets are automatically redacted.")}</p></div>;
}
