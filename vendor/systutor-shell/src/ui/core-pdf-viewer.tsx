import { Viewer, Worker } from "@react-pdf-viewer/core";

import "@react-pdf-viewer/core/lib/styles/index.css";

type CorePdfViewerProps = {
  fileUrl: string | null;
  workerUrl: string;
  emptyMessage?: string;
  loadingMessage?: string;
  heightClassName?: string;
};

export function CorePdfViewer({
  fileUrl,
  workerUrl,
  emptyMessage = "Sin PDF disponible.",
  loadingMessage = "Cargando PDF...",
  heightClassName = "h-[500px]",
}: CorePdfViewerProps) {
  if (!fileUrl) {
    return (
      <div className="p-4 text-center text-sm text-muted-foreground">
        {emptyMessage}
      </div>
    );
  }

  return (
    <div className={`overflow-hidden rounded border bg-background ${heightClassName}`}>
      <Worker workerUrl={workerUrl}>
        <Viewer
          fileUrl={fileUrl}
          renderError={() => (
            <div className="p-4 text-center text-sm text-muted-foreground">
              {loadingMessage}
            </div>
          )}
        />
      </Worker>
    </div>
  );
}
