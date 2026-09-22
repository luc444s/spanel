import { type DragEvent, useRef, useState } from "react";
import { cn } from "./cn";

type FileUploadProps = {
  onFiles: (files: File[]) => void;
  accept?: string;
  multiple?: boolean;
  maxSize?: number;
  className?: string;
};

export function FileUpload({
  onFiles,
  accept,
  multiple = false,
  maxSize,
  className,
}: FileUploadProps) {
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  function handleFiles(fileList: FileList | null) {
    if (!fileList) return;
    const files = Array.from(fileList);
    if (maxSize) {
      const valid = files.filter((f) => f.size <= maxSize);
      if (valid.length !== files.length) return;
      onFiles(valid);
    } else {
      onFiles(files);
    }
  }

  function handleDragOver(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragOver(true);
  }

  function handleDragLeave() {
    setDragOver(false);
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragOver(false);
    handleFiles(event.dataTransfer.files);
  }

  return (
    <div
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      onClick={() => inputRef.current?.click()}
      className={cn(
        "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-md border-2 border-dashed p-8 text-sm transition",
        dragOver
          ? "border-primary bg-primary/5"
          : "border-border bg-surface hover:border-ring",
        className
      )}
    >
      <svg
        className="h-8 w-8 text-muted-foreground"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
      >
        <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M17 8l-5-5-5 5M12 3v12" />
      </svg>
      <p className="text-foreground">
        {dragOver ? "Suelta los archivos aqu\u00ed" : "Arrastra archivos o haz clic para subir"}
      </p>
      <p className="text-xs text-muted-foreground">
        {accept ? `Formatos: ${accept}` : "Todos los formatos"}
        {maxSize ? ` | M\u00e1x: ${(maxSize / (1024 * 1024)).toFixed(0)}MB` : ""}
      </p>
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        multiple={multiple}
        className="hidden"
        onChange={(event) => handleFiles(event.target.files)}
      />
    </div>
  );
}
