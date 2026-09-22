import { useEffect, useRef, useState } from "react";
import { Button } from "./button";
import { Combobox } from "./combobox";

type SearchResult = {
  display_name: string;
  lat: string;
  lon: string;
};

type Props = {
  phase: "idle" | "picking_start" | "picking_end" | "picking_stops";
  onSelect: (lat: number, lng: number) => void;
  onAddStop: () => void;
};

export function LocationSearch({ phase, onSelect, onAddStop }: Props) {
  const [query, setQuery] = useState("");
  const [options, setOptions] = useState<{ value: string; label: string }[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const isBuilding = phase !== "idle";

  useEffect(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    if (query.length < 3) {
      setOptions([]);
      return;
    }
    timerRef.current = setTimeout(async () => {
      setIsSearching(true);
      try {
        const res = await fetch(
          `https://nominatim.openstreetmap.org/search?format=json&q=${encodeURIComponent(query)}&countrycodes=es&limit=5`
        );
        const data = (await res.json()) as SearchResult[];
        setOptions(
          data.map((r) => ({
            value: `${r.lat},${r.lon}`,
            label: r.display_name,
          }))
        );
      } catch {
        setOptions([]);
      } finally {
        setIsSearching(false);
      }
    }, 300);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [query]);

  function handleChange(value: string) {
    const [lat, lng] = value.split(",").map(Number);
    if (!isNaN(lat) && !isNaN(lng)) {
      onSelect(lat, lng);
      setQuery("");
      setOptions([]);
    }
  }

  const placeholder =
    phase === "picking_start"
      ? "Buscar partida..."
      : phase === "picking_end"
        ? "Buscar destino..."
        : "Buscar parada...";

  if (!isBuilding) return null;

  return (
    <div className="space-y-2">
      <Combobox
        value=""
        onChange={handleChange}
        options={options}
        variant="input"
        placeholder={placeholder}
        emptyMessage={isSearching ? "Buscando..." : query.length < 3 ? "Escribe al menos 3 letras" : "Sin resultados"}
        searchValue={query}
        onSearchValueChange={setQuery}
      />

      {phase === "picking_stops" ? (
        <Button variant="secondary" onClick={onAddStop}>
          Agregar parada manual
        </Button>
      ) : null}
    </div>
  );
}
