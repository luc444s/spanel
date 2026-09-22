import { Button } from "./button";
import { Card, CardContent, CardHeader, CardTitle } from "./card";
import { EmptyState } from "./empty-state";
import { Skeleton } from "./skeleton";

export interface TraceabilityEvent {
  timestamp: string;
  event_type: string;
  description: string;
  actor: string | null;
  metadata: Record<string, unknown>;
}

export interface TraceabilityPagination {
  page: number;
  per_page: number;
  total: number;
  total_pages: number;
}

interface TraceabilityTimelineProps {
  events: TraceabilityEvent[];
  pagination?: TraceabilityPagination;
  loading?: boolean;
  emptyMessage?: string;
  onLoadMore?: () => void;
  loadingMore?: boolean;
}

const EVENT_ICONS: Record<string, string> = {
  created: "\u{2795}",
  state_change: "\u{1f504}",
  scan: "\u{1f4f7}",
  loaded: "\u{1f69a}",
  unloaded: "\u{1f6cf}",
  moved: "\u{1f9f3}",
  hydrotest: "\u{1f4e6}",
  retimbrado: "\u{1f527}",
  service: "\u{1f6e0}",
  warranty: "\u{1f6e1}",
  ownership: "\u{1f464}",
  label_print: "\u{1f4c4}",
  weight_updated: "\u2696",
  medical_flag_changed: "\u2695",
  contract_assigned: "\u{1f4cb}",
  contract_released: "\u{1f4c5}",
};

function formatTimestamp(ts: string): { date: string; time: string } {
  const d = new Date(ts);
  const date = d.toLocaleDateString("es-PE", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  const time = d.toLocaleTimeString("es-PE", {
    hour: "2-digit",
    minute: "2-digit",
  });
  return { date, time };
}

function groupByDate(events: TraceabilityEvent[]): Map<string, TraceabilityEvent[]> {
  const groups = new Map<string, TraceabilityEvent[]>();
  for (const event of events) {
    const { date } = formatTimestamp(event.timestamp);
    const list = groups.get(date) ?? [];
    list.push(event);
    groups.set(date, list);
  }
  return groups;
}

export function TraceabilityTimeline({
  events,
  pagination,
  loading,
  emptyMessage = "No hay eventos de trazabilidad",
  onLoadMore,
  loadingMore,
}: TraceabilityTimelineProps) {
  if (loading) {
    return (
      <div className="space-y-4">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-16 w-full" />
        ))}
      </div>
    );
  }

  if (events.length === 0) {
    return <EmptyState title={emptyMessage} />;
  }

  const grouped = groupByDate(events);
  const hasMore = pagination ? pagination.page < pagination.total_pages : false;

  return (
    <div className="space-y-6">
      {Array.from(grouped.entries()).map(([date, dateEvents]) => (
        <div key={date}>
          <div className="relative mb-4">
            <div className="absolute inset-0 flex items-center">
              <span className="w-full border-t" />
            </div>
            <div className="relative flex justify-center">
              <span className="bg-card px-3 text-xs text-muted-foreground">
                {"\u2500".repeat(20)} {date} {"\u2500".repeat(20)}
              </span>
            </div>
          </div>
          <div className="space-y-3">
            {dateEvents.map((event, idx) => (
              <Card key={`${event.timestamp}-${event.event_type}-${idx}`}>
                <CardHeader className="py-3">
                  <CardTitle className="flex items-center gap-2 text-sm font-medium">
                    <span className="text-lg">
                      {EVENT_ICONS[event.event_type] ?? "\u2022"}
                    </span>
                    <span>{event.description}</span>
                  </CardTitle>
                </CardHeader>
                <CardContent className="pb-3 text-xs text-muted-foreground">
                  <div className="flex gap-4">
                    <span>
                      {"\u{1f4c5}"} {formatTimestamp(event.timestamp).time}
                    </span>
                    {event.actor ? <span>{"\u{1f464}"} {event.actor}</span> : null}
                  </div>
                  {Object.keys(event.metadata).length > 0 ? (
                    <details className="mt-2">
                      <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">
                        metadata
                      </summary>
                      <pre className="mt-1 rounded bg-muted p-2 text-xs">
                        {JSON.stringify(event.metadata, null, 2)}
                      </pre>
                    </details>
                  ) : null}
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      ))}
      {hasMore ? (
        <div className="flex justify-center pt-2">
          <Button variant="secondary" onClick={onLoadMore} disabled={loadingMore}>
            {loadingMore ? "Cargando..." : "Cargar m\u00e1s"}
          </Button>
        </div>
      ) : null}
    </div>
  );
}
