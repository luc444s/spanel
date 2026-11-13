import { Toaster as SonnerToaster } from "sonner";

export function Toaster() {
  return (
    <SonnerToaster
      position="top-right"
      toastOptions={{
        classNames: {
          toast:
            "group toast group-[.toaster]:bg-card/95 group-[.toaster]:text-foreground/90 group-[.toaster]:border-border/60 group-[.toaster]:shadow-none group-[.toaster]:text-xs",
          description: "group-[.toast]:text-muted-foreground",
          actionButton:
            "group-[.toast]:bg-muted group-[.toast]:text-foreground",
          cancelButton:
            "group-[.toast]:bg-muted group-[.toast]:text-muted-foreground",
          success:
            "group-[.toast]:!border-border/60 group-[.toast]:!bg-card/95",
          error: "group-[.toast]:!border-border/60 group-[.toast]:!bg-card/95",
        },
      }}
      closeButton
      richColors={false}
    />
  );
}

export { toast } from "sonner";
