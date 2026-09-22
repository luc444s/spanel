import { useCallback, useEffect, useRef, useState } from "react";
import {
  MapContainer,
  Marker,
  TileLayer,
  useMapEvents,
  useMap,
} from "react-leaflet";
import type { Map as LeafletMap } from "leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

const leafletZoomStyles = `
.systutor-map .leaflet-top,
.systutor-map .leaflet-bottom { z-index: 1100; }
.systutor-map .systutor-marker { z-index: 700 !important; }
.systutor-map .leaflet-control-attribution { display: none; }
.systutor-map .leaflet-control-zoom a { color: #333; }
`;

import { getApiBaseUrl } from "../api/client";
import { cn } from "./cn";
import { Input } from "./input";

import type { ReactNode } from "react";

type LatLng = { lat: number; lng: number };

type GeocodeResult = {
  lat: number;
  lng: number;
  display_name: string;
  address?: Record<string, string>;
};

type LocationPickerProps = {
  value: LatLng | null;
  onChange: (location: LatLng) => void;
  onAddressResolved?: (result: { display_name: string; address: Record<string, string> } | null) => void;
  className?: string;
  placeholder?: string;
  searchPlaceholder?: string;
  height?: number;
  /** Centro del mapa cuando no hay value (evita hardcodear región en el Core). */
  defaultCenter?: LatLng;
};

const DEFAULT_ZOOM = 13;

const lightTile = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";
const nominatimBaseUrl = "https://nominatim.openstreetmap.org";

const defaultIcon = L.icon({
  iconUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
  iconRetinaUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png",
  shadowUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
  iconSize: [25, 41],
  iconAnchor: [12, 41],
  popupAnchor: [1, -34],
  shadowSize: [41, 41],
});

L.Marker.prototype.options.icon = defaultIcon;

function ChangeView({ center }: { center: LatLng }) {
  const map = useMap();
  useEffect(() => {
    map.setView(center, map.getZoom());
  }, [center, map]);
  return null;
}

function ClickMarker({
  value,
  onChange,
}: {
  value: LatLng | null;
  onChange: (location: LatLng) => void;
}) {
  useMapEvents({
    click(event) {
      onChange({ lat: event.latlng.lat, lng: event.latlng.lng });
    },
  });

  if (!value) return null;

  return (
    <Marker
      position={[value.lat, value.lng]}
      draggable
      eventHandlers={{
        dragend(event) {
          const marker = event.target;
          const position = marker.getLatLng();
          onChange({ lat: position.lat, lng: position.lng });
        },
      }}
    />
  );
}

function SearchControl({
  onSelect,
  placeholder,
  searchPlaceholder,
}: {
  onSelect: (location: LatLng) => void;
  placeholder?: string;
  searchPlaceholder?: string;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<GeocodeResult[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  function handleSearch(value: string) {
    setQuery(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);

    if (!value.trim()) {
      setResults([]);
      setOpen(false);
      return;
    }

    debounceRef.current = setTimeout(async () => {
      setLoading(true);
      try {
        let response = await fetch(
          `${getApiBaseUrl()}/api/v1/geocode/search?q=${encodeURIComponent(value)}&limit=5`,
          { headers: { "Accept-Language": "es" } },
        );
        if (!response.ok) {
          response = await fetch(
            `${nominatimBaseUrl}/search?format=jsonv2&addressdetails=1&q=${encodeURIComponent(value)}&limit=5`,
            { headers: { "Accept-Language": "es" } },
          );
        }
        if (!response.ok) return;
        const data = (await response.json()).map((item: { lat: string | number; lon?: string | number; lng?: string | number; display_name: string; address?: Record<string, string> }) => ({
          lat: Number(item.lat),
          lng: Number(item.lng ?? item.lon),
          display_name: item.display_name,
          address: item.address,
        }));
        setResults(data);
        setOpen(data.length > 0);
      } catch {
        setResults([]);
      } finally {
        setLoading(false);
      }
    }, 400);
  }

  function selectResult(result: (typeof results)[0]) {
    onSelect({ lat: result.lat, lng: result.lng });
    setQuery(result.display_name);
    setOpen(false);
    setResults([]);
  }

  return (
    <div ref={containerRef} className="relative">
      <Input
        value={query}
        onChange={(event) => handleSearch(event.target.value)}
        placeholder={searchPlaceholder ?? "Buscar direcci\u00f3n..."}
        className={cn(loading && "opacity-60")}
      />
      {open ? (
        <div className="absolute left-0 right-0 top-full z-[9999] mt-1 max-h-48 overflow-auto rounded-md border border-border bg-popover shadow-lg">
          {results.map((result) => (
            <button
              key={result.display_name}
              type="button"
              className="flex w-full px-3 py-2 text-left text-sm text-popover-foreground transition hover:bg-accent hover:text-accent-foreground"
              onClick={() => selectResult(result)}
            >
              <span className="line-clamp-2">{result.display_name}</span>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function LocationPicker({
  value,
  onChange,
  onAddressResolved,
  className,
  placeholder,
  searchPlaceholder,
  height = 300,
  defaultCenter,
}: LocationPickerProps) {
  const mapRef = useRef<LeafletMap | null>(null);
  const [mapReady, setMapReady] = useState(false);

  const handleMapReady = useCallback(() => {
    setMapReady(true);
  }, []);

  const initialCenter: LatLng = value ?? defaultCenter ?? { lat: -8.115994, lng: -79.029858 };
  const [address, setAddress] = useState<string | null>(null);
  const [resolving, setResolving] = useState(false);

  useEffect(() => {
    if (!value) {
      setAddress(null);
      return;
    }
    let cancelled = false;
    setResolving(true);
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 8000);
    fetch(`${getApiBaseUrl()}/api/v1/geocode/reverse?lat=${value.lat}&lng=${value.lng}`, {
      headers: { "Accept-Language": "es" },
      signal: controller.signal,
    })
      .then((r) => {
        if (r.ok) return r.json();
        return fetch(
          `${nominatimBaseUrl}/reverse?format=jsonv2&addressdetails=1&lat=${value.lat}&lon=${value.lng}`,
          { headers: { "Accept-Language": "es" }, signal: controller.signal },
        ).then((fallback) => (fallback.ok ? fallback.json() : null));
      })
      .then((data) => {
        if (!cancelled) {
          setAddress(data?.display_name ?? null);
          setResolving(false);
          if (data?.display_name) {
            onAddressResolved?.({ display_name: data.display_name, address: data.address ?? {} });
          }
        }
      })
      .catch((error) => {
        console.error("[GEOCODE-ERROR]", error);
        if (!cancelled) {
          setAddress(null);
          setResolving(false);
        }
      })
      .finally(() => window.clearTimeout(timeout));
    return () => { cancelled = true; controller.abort(); };
  }, [value?.lat, value?.lng]);

  return (
    <div className={cn("space-y-2", className)}>
      <SearchControl
        onSelect={onChange}
        placeholder={placeholder}
        searchPlaceholder={searchPlaceholder}
      />
      <div
        className="systutor-map isolate overflow-hidden rounded-md border border-border"
        style={{ height }}
      >
        <style>{leafletZoomStyles}</style>
        <MapContainer
          center={[initialCenter.lat, initialCenter.lng]}
          zoom={value ? DEFAULT_ZOOM : 6}
          className="h-full w-full"
          ref={mapRef}
          whenReady={handleMapReady}
        >
          <TileLayer
            url={lightTile}
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
          />
          <ClickMarker value={value} onChange={onChange} />
          {value ? <ChangeView center={value} /> : null}
        </MapContainer>
      </div>
      {value ? (
        <div className="space-y-1">
          <p className="text-xs text-muted-foreground">
            {value.lat.toFixed(6)}, {value.lng.toFixed(6)}
          </p>
          {resolving ? (
            <p className="text-xs text-muted-foreground italic">Resolviendo direcci\u00f3n...</p>
          ) : address ? (
            <p className="text-xs text-muted-foreground">{address}</p>
          ) : null}
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">
          {placeholder ?? "Haz clic en el mapa para seleccionar una ubicaci\u00f3n"}
        </p>
      )}
    </div>
  );
}
