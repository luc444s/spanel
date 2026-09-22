import type { ReactNode } from "react";
import type { CalendarItem, CalendarResource, CalendarView } from "./resource-calendar-types";
import {
  buildDateRange,
  formatDayLabel,
  formatHourLabel,
  isSameDay,
  isoString,
  setHour,
} from "./resource-calendar-dates";
import { ResourceCalendarEventBlock } from "./resource-calendar-event-block";
import { cn } from "../cn";

type Props = {
  view: Exclude<CalendarView, "month">;
  focusDate: string;
  rangeStart: string;
  rangeEnd: string;
  resources: CalendarResource[];
  items: CalendarItem[];
  onSlotSelect?: (resourceId: string | null, start: string, end: string) => void;
  onItemClick?: (itemId: string) => void;
  renderItem?: (item: CalendarItem) => ReactNode;
  renderResourceHeader?: (resource: CalendarResource) => ReactNode;
};

const HOURS = Array.from({ length: 16 }, (_, index) => index + 6);
const HOUR_ROW_HEIGHT = 56;

function getVisibleDays(view: Exclude<CalendarView, "month">, rangeStart: string, rangeEnd: string) {
  if (view === "day") {
    return [new Date(rangeStart)];
  }
  return buildDateRange(rangeStart, rangeEnd);
}

function getMinutesFromMidnight(value: string) {
  const date = new Date(value);
  return date.getHours() * 60 + date.getMinutes();
}

export function ResourceCalendarTimeGrid({
  view,
  focusDate,
  rangeStart,
  rangeEnd,
  resources,
  items,
  onSlotSelect,
  onItemClick,
  renderItem,
  renderResourceHeader,
}: Props) {
  const days = getVisibleDays(view, rangeStart, rangeEnd);
  const today = new Date();

  return (
    <div className="overflow-auto rounded-2xl border border-border bg-card">
      <div className="min-w-[780px]">
        <div className="grid border-b border-border bg-muted/30" style={{ gridTemplateColumns: `72px repeat(${resources.length * days.length}, minmax(150px, 1fr))` }}>
          <div className="border-r border-border px-2 py-1.5 text-[11px] uppercase tracking-wide text-muted-foreground">Hora</div>
          {days.map((day) =>
            resources.map((resource) => (
              <div
                key={`${day.toISOString()}-${resource.id}`}
                className={cn(
                  "border-r border-border px-2 py-1.5 text-sm",
                  isSameDay(day, focusDate) && "bg-primary/10",
                  isSameDay(day, today) && "border-primary/40",
                )}
              >
                <div className="font-medium text-foreground">{renderResourceHeader ? renderResourceHeader(resource) : resource.label}</div>
                <div className={cn("text-[11px] text-muted-foreground", isSameDay(day, today) && "text-primary")}>
                  {view === "week" ? formatDayLabel(day) : resource.subtitle}
                </div>
              </div>
            )),
          )}
        </div>

        <div className="grid" style={{ gridTemplateColumns: `72px repeat(${resources.length * days.length}, minmax(150px, 1fr))` }}>
          <div className="border-r border-border">
            {HOURS.map((hour) => (
              <div
                key={hour}
                className="border-b border-border px-2 py-1.5 text-[11px] text-muted-foreground"
                style={{ height: HOUR_ROW_HEIGHT }}
              >
                {formatHourLabel(hour)}
              </div>
            ))}
          </div>

          {days.map((day) =>
            resources.map((resource) => {
              const columnItems = items.filter((item) => {
                const itemStart = new Date(item.start);
                return (
                  item.resourceId === resource.id &&
                  itemStart.getFullYear() === day.getFullYear() &&
                  itemStart.getMonth() === day.getMonth() &&
                  itemStart.getDate() === day.getDate()
                );
              });

              return (
                <div
                  key={`${day.toISOString()}-${resource.id}`}
                  className={cn(
                    "relative border-r border-border",
                    isSameDay(day, focusDate) && "bg-primary/5",
                  )}
                >
                  {HOURS.map((hour) => {
                    const slotStart = setHour(day, hour);
                    const slotEnd = new Date(slotStart);
                    slotEnd.setHours(slotStart.getHours() + 1, 0, 0, 0);
                    return (
                      <button
                        key={`${resource.id}-${hour}`}
                        type="button"
                        onClick={() => onSlotSelect?.(resource.id, isoString(slotStart), isoString(slotEnd))}
                        className={cn(
                          "block w-full border-b border-border text-left transition hover:bg-accent/15",
                          resource.disabled && "cursor-not-allowed bg-muted/30",
                        )}
                        style={{ height: HOUR_ROW_HEIGHT }}
                        disabled={resource.disabled}
                      />
                    );
                  })}

                  <div className="pointer-events-none absolute inset-0">
                    {columnItems.map((item) => {
                      const topMinutes = getMinutesFromMidnight(item.start) - HOURS[0] * 60;
                      const endMinutes = getMinutesFromMidnight(item.end);
                      const heightMinutes = Math.max(endMinutes - getMinutesFromMidnight(item.start), 30);
                      const top = Math.max(topMinutes / 60, 0) * HOUR_ROW_HEIGHT;
                      const height = (heightMinutes / 60) * HOUR_ROW_HEIGHT;
                      return (
                        <div key={item.id} className="absolute left-1 right-1" style={{ top, height }}>
                          <div className="pointer-events-auto h-full">
                            <ResourceCalendarEventBlock
                              onClick={() => onItemClick?.(item.id)}
                              className={cn(
                                "h-full",
                                item.isConflicted && "border-rose-400/50 bg-rose-500/10",
                                item.isLocked && "opacity-80",
                              )}
                            >
                              {renderItem ? renderItem(item) : item.title}
                            </ResourceCalendarEventBlock>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              );
            }),
          )}
        </div>
      </div>
    </div>
  );
}
