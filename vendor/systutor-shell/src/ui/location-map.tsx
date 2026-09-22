import { useEffect, useMemo, useRef } from "react";
import { MapContainer, Marker, Polyline, Popup, TileLayer, useMap, useMapEvents } from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

const leafletZoomStyles = `
.systutor-map .leaflet-top,
.systutor-map .leaflet-bottom { z-index: 1100; }
.systutor-map .systutor-marker { z-index: 700 !important; }
.systutor-map .leaflet-control-attribution { display: none; }
.systutor-map .leaflet-control-zoom a { color: #333; }
`;

import { cn } from "./cn";

type LatLng = { lat: number; lng: number };

export type LocationMapMarker = {
  id: string;
  position: LatLng;
  label?: string;
  /** Muestra el rótulo siempre visible (sin necesidad de clic). */
  labelVisible?: boolean;
  /** Color del marker: "origin" (verde), "assigned" (naranja), "completed" (verde oscuro) o default (azul). */
  color?: "origin" | "assigned" | "completed" | "default";
};

export type LocationMapPolyline = {
  id: string;
  points: LatLng[];
  color?: string;
  weight?: number;
  dashArray?: string;
};

type Props = {
  center: LatLng;
  zoom?: number;
  markers?: LocationMapMarker[];
  polylines?: LocationMapPolyline[];
  className?: string;
  height?: number;
  onMarkerClick?: (id: string) => void;
  onMarkerDrag?: (id: string, latlng: LatLng) => void;
  onMapClick?: (latlng: LatLng) => void;
  /** Ajusta el zoom para abarcar todos los markers cuando cambian. */
  autoFit?: boolean;
};

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

const lightTile = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";

function ChangeView({ center, zoom }: { center: LatLng; zoom: number }) {
  const map = useMap();
  // Clave primitiva: el effect solo corre cuando la vista objetivo cambia de
  // verdad, no en cada render del padre. Así el usuario puede hacer zoom/pan
  // manual sin que el mapa lo revierta a la vista programática.
  const viewKey = `${center.lat},${center.lng},${zoom}`;
  useEffect(() => {
    const current = map.getCenter();
    const alreadyThere =
      Math.abs(current.lat - center.lat) < 1e-7 &&
      Math.abs(current.lng - center.lng) < 1e-7 &&
      map.getZoom() === zoom;
    if (alreadyThere) {
      return;
    }
    map.setView([center.lat, center.lng], zoom);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewKey]);
  return null;
}

function StatefulMarker({
  marker,
  onMarkerClick,
  onMarkerDrag,
}: {
  marker: LocationMapMarker;
  onMarkerClick?: (id: string) => void;
  onMarkerDrag?: (id: string, latlng: LatLng) => void;
}) {
  const isOrigin = marker.color === "origin";
  const isAssigned = marker.color === "assigned";
  const isCompleted = marker.color === "completed";
  const pinColor = isOrigin ? "#16a34a" : isAssigned ? "#f59e0b" : isCompleted ? "#15803d" : "#2563eb";
  const iconLabel = isOrigin ? "A" : isCompleted ? "\u2713" : "\u2022";
  const icon = useMemo(
    () =>
      L.divIcon({
        className: "systutor-marker",
        html: `<div style="display:flex;flex-direction:column;align-items:center;gap:2px">
          <div style="width:22px;height:22px;border-radius:50% 50% 50% 0;transform:rotate(-45deg);background:${pinColor};border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.3);display:flex;align-items:center;justify-content:center">
            <span style="transform:rotate(45deg);font-size:11px;color:#fff">${iconLabel}</span>
          </div>
          ${marker.labelVisible && marker.label ? `<span style="background:${pinColor};color:#fff;padding:1px 6px;border-radius:4px;font-size:10px;white-space:nowrap;transform:rotate(0)">${marker.label}</span>` : ""}
        </div>`,
        iconSize: [22, 22],
        iconAnchor: [11, 22],
      }),
    [pinColor, marker.labelVisible, marker.label, isOrigin, iconLabel],
  );
  const markerRef = useRef<L.Marker | null>(null);

  useEffect(() => {
    if (markerRef.current) {
      markerRef.current.setIcon(icon);
    }
  }, [icon]);

  return (
    <Marker
      ref={markerRef}
      position={[marker.position.lat, marker.position.lng]}
      icon={icon}
      draggable={Boolean(onMarkerDrag)}
      eventHandlers={{
        ...(onMarkerClick ? { click: () => onMarkerClick(marker.id) } : {}),
        ...(onMarkerDrag
          ? {
              dragend: (e: L.LeafletEvent) => {
                const target = e.target as L.Marker;
                const pos = target.getLatLng();
                onMarkerDrag(marker.id, { lat: pos.lat, lng: pos.lng });
              },
            }
          : {}),
      }}
    >
      {marker.label ? (
        <Popup>
          <span className="text-sm">{marker.label}</span>
        </Popup>
      ) : null}
    </Marker>
  );
}


function FitMarkers({ markers }: { markers: LocationMapMarker[] }) {
  const map = useMap();
  useEffect(() => {
    if (markers.length === 0) {
      return;
    }
    const bounds = L.latLngBounds(markers.map((m) => [m.position.lat, m.position.lng]));
    map.fitBounds(bounds, { padding: [40, 40], maxZoom: 15 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [markers]);
  return null;
}

function MapClickHandler({ onClick }: { onClick: (latlng: LatLng) => void }) {
  useMapEvents({
    click: (e) => onClick({ lat: e.latlng.lat, lng: e.latlng.lng }),
  });
  return null;
}

export function LocationMap({
  center,
  zoom = 12,
  markers = [],
  polylines = [],
  className,
  height = 320,
  onMarkerClick,
  onMarkerDrag,
  onMapClick,
  autoFit = false,
}: Props) {

  return (
    <div className={cn("systutor-map isolate overflow-hidden rounded-md border border-border", className)} style={{ height }}>
      <style>{leafletZoomStyles}</style>
      <MapContainer center={[center.lat, center.lng]} zoom={zoom} className="h-full w-full" doubleClickZoom={!onMapClick} zoomControl={true} scrollWheelZoom={true}>
        <TileLayer
          url={lightTile}
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
        />
        <ChangeView center={center} zoom={zoom} />
        {autoFit ? <FitMarkers markers={markers} /> : null}
        {onMapClick ? <MapClickHandler onClick={onMapClick} /> : null}
        {polylines.map((polyline) => (
          <Polyline
            key={polyline.id}
            positions={polyline.points.map((point) => [point.lat, point.lng] as [number, number])}
            pathOptions={{
              color: polyline.color ?? "#2563eb",
              weight: polyline.weight ?? 4,
              dashArray: polyline.dashArray,
            }}
          />
        ))}
        {markers.map((marker) => (
          <StatefulMarker
            key={marker.id}
            marker={marker}
            onMarkerClick={onMarkerClick}
            onMarkerDrag={onMarkerDrag}
          />
        ))}
      </MapContainer>
    </div>
  );
}
