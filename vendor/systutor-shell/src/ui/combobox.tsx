import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronsUpDown, Search } from "lucide-react";

import { cn } from "./cn";

export type ComboboxOption = {
  value: string;
  label: string;
  keywords?: string[];
};

type ComboboxProps = {
  value: string;
  onChange: (value: string) => void;
  options: ComboboxOption[];
  placeholder?: string;
  searchPlaceholder?: string;
  emptyMessage?: ReactNode;
  className?: string;
  required?: boolean;
  disabled?: boolean;
  searchValue?: string;
  onSearchValueChange?: (value: string) => void;
  onSubmitQuery?: (value: string) => void;
  variant?: "button" | "input";
  minSearchLength?: number;
  maxResults?: number;
  selectedLabel?: string;
  footer?: ReactNode;
};

function normalize(value: string) {
  return value
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .toLowerCase()
    .trim();
}

function tokenMatch(haystack: string, query: string): boolean {
  const tokens = query.split(/\s+/).filter(Boolean);
  if (tokens.length === 0) return true;

  const words = haystack.split(/\s+/);
  return tokens.every((token) =>
    words.some((word) => word.startsWith(token))
  );
}

export function Combobox({
  value,
  onChange,
  options,
  placeholder,
  searchPlaceholder,
  emptyMessage = "Sin opciones.",
  className,
  required,
  disabled,
  searchValue,
  onSearchValueChange,
  onSubmitQuery,
  variant = "button",
  minSearchLength = 0,
  maxResults = 50,
  selectedLabel,
  footer,
}: ComboboxProps) {
  const [open, setOpen] = useState(false);
  const [internalQuery, setInternalQuery] = useState("");
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const ref = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const [panelPos, setPanelPos] = useState<{
    left: number;
    width: number;
    top?: number;
    bottom?: number;
  } | null>(null);

  function updatePanelPosition() {
    const rect = ref.current?.getBoundingClientRect();
    if (!rect) return;
    const spaceBelow = window.innerHeight - rect.bottom;
    const openUp = spaceBelow < 260 && rect.top > 260;
    setPanelPos({
      left: rect.left,
      width: rect.width,
      ...(openUp
        ? { bottom: window.innerHeight - rect.top + 4 }
        : { top: rect.bottom + 4 }),
    });
  }

  useEffect(() => {
    if (!open) {
      setPanelPos(null);
      return;
    }
    updatePanelPosition();
    window.addEventListener("resize", updatePanelPosition);
    window.addEventListener("scroll", updatePanelPosition, true);
    return () => {
      window.removeEventListener("resize", updatePanelPosition);
      window.removeEventListener("scroll", updatePanelPosition, true);
    };
  }, [open]);

  const query = searchValue ?? internalQuery;
  const normalizedQuery = normalize(query);
  const canOpen = normalizedQuery.length >= minSearchLength;

  const selected = options.find((option) => option.value === value) ?? null;
  const displayLabel = selected?.label ?? selectedLabel ?? (value || null);

  const filteredOptions = useMemo(() => {
    if (!normalizedQuery) {
      return options;
    }
    const query = normalize(normalizedQuery);
    return options.filter((option) => {
      const haystack = [option.label, ...(option.keywords ?? [])]
        .map(normalize)
        .join(" ");
      return tokenMatch(haystack, query);
    });
  }, [normalizedQuery, options]);

  const visibleOptions = useMemo(
    () => filteredOptions.slice(0, maxResults),
    [filteredOptions, maxResults],
  );

  function updateQuery(nextValue: string) {
    if (onSearchValueChange) {
      onSearchValueChange(nextValue);
      return;
    }
    setInternalQuery(nextValue);
  }

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      const target = event.target as Node;
      if (ref.current?.contains(target)) return;
      if (panelRef.current?.contains(target)) return;
      setOpen(false);
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  useEffect(() => {
    if (!open) {
      if (variant === "button") {
        updateQuery("");
      }
      return;
    }
    const selectedIndex = visibleOptions.findIndex((option) => option.value === value);
    setHighlightedIndex(selectedIndex >= 0 ? selectedIndex : 0);
    const timer = window.setTimeout(() => inputRef.current?.focus(), 0);
    return () => window.clearTimeout(timer);
  }, [open, visibleOptions, value]);

  useEffect(() => {
    if (highlightedIndex >= visibleOptions.length) {
      setHighlightedIndex(visibleOptions.length > 0 ? visibleOptions.length - 1 : 0);
    }
  }, [visibleOptions.length, highlightedIndex]);

  useEffect(() => {
    if (variant === "input") {
      setOpen(canOpen);
    }
  }, [canOpen, variant]);

  function selectOption(option: ComboboxOption) {
    onChange(option.value);
    setOpen(false);
    updateQuery("");
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement | HTMLInputElement>) {
    if (variant === "input" && event.key === "Enter") {
      if (open && visibleOptions[highlightedIndex]) {
        event.preventDefault();
        selectOption(filteredOptions[highlightedIndex]);
        return;
      }
      if (normalizedQuery.length >= minSearchLength && onSubmitQuery) {
        event.preventDefault();
        onSubmitQuery(query);
      }
      return;
    }

    if (!open && (event.key === "ArrowDown" || event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      if (!disabled && canOpen) {
        setOpen(true);
      }
      return;
    }

    if (!open) {
      return;
    }

    if (event.key === "Escape") {
      event.preventDefault();
      setOpen(false);
      return;
    }

    if (event.key === "ArrowDown") {
      event.preventDefault();
      setHighlightedIndex((current) => {
        if (filteredOptions.length === 0) return 0;
        return current >= filteredOptions.length - 1 ? 0 : current + 1;
      });
      return;
    }

    if (event.key === "ArrowUp") {
      event.preventDefault();
      setHighlightedIndex((current) => {
        if (filteredOptions.length === 0) return 0;
        return current <= 0 ? filteredOptions.length - 1 : current - 1;
      });
      return;
    }

    if (event.key === "Enter" && visibleOptions[highlightedIndex]) {
      event.preventDefault();
      selectOption(filteredOptions[highlightedIndex]);
    }
  }

  return (
    <div ref={ref} className="relative" onKeyDown={handleKeyDown}>
      {/* Input variant: always a search input */}
      {variant === "input" && (
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <input
            ref={inputRef}
            value={query}
            disabled={disabled}
            aria-expanded={open}
            aria-haspopup="listbox"
            aria-required={required}
            onChange={(event) => updateQuery(event.target.value)}
            placeholder={searchPlaceholder ?? placeholder ?? "Buscar..."}
            className={cn(
              "w-full rounded-md border border-input bg-surface pl-9 pr-3 py-2 text-sm text-foreground outline-none transition placeholder:text-muted-foreground focus:border-ring focus:ring-1 focus:ring-ring",
              className,
            )}
          />
        </div>
      )}

      {/* Button variant: button when closed, search input when open */}
      {variant === "button" && !open && (
        <button
          type="button"
          disabled={disabled}
          aria-expanded={open}
          aria-haspopup="listbox"
          aria-required={required}
          onClick={() => {
            if (!disabled) {
              setOpen(true);
            }
          }}
          className={cn(
            "flex w-full items-center justify-between gap-2 rounded-md border border-input bg-surface px-3 py-2 text-sm text-foreground transition hover:border-ring disabled:cursor-not-allowed disabled:opacity-60",
            !displayLabel && placeholder && "text-muted-foreground",
            className,
          )}
        >
          <span className="truncate text-left">{displayLabel ?? placeholder ?? "Seleccionar"}</span>
          <ChevronsUpDown className="h-4 w-4 shrink-0 text-muted-foreground" />
        </button>
      )}

      {variant === "button" && open && (
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => updateQuery(event.target.value)}
            placeholder={searchPlaceholder ?? "Buscar..."}
            aria-expanded={open}
            aria-haspopup="listbox"
            aria-required={required}
            className={cn(
              "w-full rounded-md border border-ring bg-surface pl-9 pr-3 py-2 text-sm text-foreground outline-none ring-1 ring-ring",
              className,
            )}
          />
        </div>
      )}

      {/* Dropdown list — portal a document.body para escapar de contenedores
          con overflow-hidden (dialogs) y pintar por encima de todo (§zindex). */}
      {open && panelPos
        ? createPortal(
            <div
              ref={panelRef}
              style={{
                position: "fixed",
                zIndex: 1100,
                left: panelPos.left,
                width: panelPos.width,
                ...(panelPos.top !== undefined ? { top: panelPos.top } : {}),
                ...(panelPos.bottom !== undefined ? { bottom: panelPos.bottom } : {}),
              }}
              className="rounded-md border border-border bg-popover shadow-lg"
            >
              <div className="max-h-60 overflow-auto py-1" role="listbox">
            {visibleOptions.length > 0 ? (
              visibleOptions.map((option, index) => {
                const isSelected = option.value === value;
                const isHighlighted = index === highlightedIndex;

                return (
                  <button
                    key={option.value}
                    type="button"
                    role="option"
                    aria-selected={isSelected}
                    onMouseEnter={() => setHighlightedIndex(index)}
                    onClick={() => selectOption(option)}
                    className={cn(
                      "flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition",
                      isHighlighted
                        ? "bg-accent text-accent-foreground"
                        : "text-popover-foreground hover:bg-accent hover:text-accent-foreground",
                    )}
                  >
                    <Check className={cn("h-4 w-4 shrink-0", isSelected ? "opacity-100" : "opacity-0")} />
                    <span className="truncate">{option.label}</span>
                  </button>
                );
              })
            ) : (
              <p className="px-3 py-2 text-sm text-muted-foreground">{emptyMessage}</p>
            )}
          </div>
          {footer && (
            <div className="border-t border-border px-1 py-1">{footer}</div>
          )}
          {filteredOptions.length > maxResults ? (
            <div className="border-t border-border px-3 py-2 text-xs text-muted-foreground">
              Mostrando {visibleOptions.length} de {filteredOptions.length}
            </div>
          ) : null}
        </div>,
        document.body
      ) : null}
    </div>
  );
}
