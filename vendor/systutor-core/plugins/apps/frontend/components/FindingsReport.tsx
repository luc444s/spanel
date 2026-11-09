import { Alert } from "@systutor/shell/ui/alert";

import { groupedFindings } from "../logic";
import type { Finding } from "../types";

const LABELS = { error: "Errores", warning: "Advertencias", info: "Información" } as const;

export function FindingsReport({ findings }: { findings: Finding[] }) {
  const grouped = groupedFindings(findings);
  return (
    <div className="space-y-3">
      <Alert title="Auditoría estática">
        La auditoría estática no demuestra seguridad. Docker build ejecuta código no confiable del repositorio.
      </Alert>
      {(["error", "warning", "info"] as const).map((severity) => (
        <section key={severity} className="space-y-2">
          <h3 className="text-sm font-semibold text-foreground">{LABELS[severity]} ({grouped[severity].length})</h3>
          {grouped[severity].map((finding, index) => (
            <article key={`${finding.code}-${finding.path}-${finding.line}-${index}`} className="rounded-md border border-border p-3 text-sm">
              <div className="flex flex-wrap gap-2 font-medium text-foreground">
                <code>{finding.code}</code>
                {finding.service ? <span>· {finding.service}</span> : null}
                <span>· {finding.path}:{finding.line}</span>
              </div>
              <p className="mt-1 text-foreground">{finding.message}</p>
              <p className="mt-1 text-muted-foreground">Remediación: {finding.remediation}</p>
            </article>
          ))}
        </section>
      ))}
    </div>
  );
}
