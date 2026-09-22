import type { ReactNode } from "react";
import type { CalendarItem } from "./resource-calendar-types";
import {
  buildMonthGrid,
  endOfMonth,
  formatDayNumber,
  isSameDay,
  isoString,
  startOfDay,
  startOfMonth,
} from "./resource-calendar-dates";
import { ResourceCalendarEventBlock } from "./resource-calendar-event-block";
import { cn } from "../cn";

type Props = {
  focusDate: string;
  items: CalendarItem[];
  onSlotSelect?: (resourceId: string | null, start: string, end: string) => void;
  onItemClick?: (itemId: string) => void;
  renderItem?: (item: CalendarItem) => ReactNode;
};

const WEEKDAY_LABELS = ["Lun", "Mar", "Mie", "Jue", "Vie", "Sab", "Dom"];

export function ResourceCalendarMonthView({
  focusDate,
  items,
  onSlotSelect,
  onItemClick,
  renderItem,
}: Props) {
  const monthStart = startOfMonth(focusDate);
  const monthEnd = endOfMonth(focusDate);
  const days = buildMonthGrid(focusDate);
  const today = new Date();

  return (
    <div className="overflow-hidden rounded-2xl border border-border bg-card">
      <div className="grid grid-cols-7 border-b border-border bg-muted/30 text-[11px] uppercase tracking-wide text-muted-foreground">
        {WEEKDAY_LABELS.map((label) => (
          <div key={label} className="px-2 py-1.5 text-center">
            {label}
          </div>
        ))}
      </div>
      <div className="grid grid-cols-7">
        {days.map((day) => {
          const dayItems = items.filter((item) => isSameDay(item.start, day));
          const isCurrentMonth = day >= monthStart && day <= monthEnd;
          const isToday = isSameDay(day, today);
          const isFocusedDay = isSameDay(day, focusDate);
          const slotEnd = new Date(day);
          slotEnd.setHours(23, 59, 59, 999);
          return (
            <div
              key={day.toISOString()}
              onClick={() => onSlotSelect?.(null, isoString(startOfDay(day)), isoString(slotEnd))}
              className={cn(
                "min-h-24 min-w-0 overflow-hidden border-b border-r border-border p-1.5 align-top text-left transition hover:bg-accent/20",
                !isCurrentMonth && "bg-muted/20 text-muted-foreground",
                isFocusedDay && "bg-primary/10 ring-1 ring-inset ring-primary/40",
                isToday && "border-primary/40",
              )}
            >
              <div className="mb-1.5 flex items-center justify-between gap-1">
                <div
                  className={cn(
                    "inline-flex h-6 w-6 items-center justify-center rounded-full text-xs font-medium",
                    isFocusedDay && "bg-primary text-primary-foreground",
                    !isFocusedDay && isToday && "bg-primary/15 text-primary",
                  )}
                >
                  {formatDayNumber(day)}
                </div>
                {isToday ? <span className="text-[10px] font-medium text-primary">Hoy</span> : null}
              </div>
              <div className="space-y-0.5">
                {dayItems.slice(0, 3).map((item) => (
                  <ResourceCalendarEventBlock
                    key={item.id}
                    onClick={() => onItemClick?.(item.id)}
                    className={item.isConflicted ? "border-rose-400/40 bg-rose-500/10" : undefined}
                  >
                    {renderItem ? renderItem(item) : item.title}
                  </ResourceCalendarEventBlock>
                ))}
                {dayItems.length > 3 ? (
                  <div className="px-1 text-[10px] text-muted-foreground">+{dayItems.length - 3} más</div>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
