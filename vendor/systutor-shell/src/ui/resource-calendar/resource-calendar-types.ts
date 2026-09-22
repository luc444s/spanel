import type { ReactNode } from "react";

export type CalendarView = "month" | "week" | "day";

export type CalendarResource = {
  id: string;
  label: string;
  subtitle?: string;
  disabled?: boolean;
};

export type CalendarItem = {
  id: string;
  resourceId: string;
  start: string;
  end: string;
  title: string;
  status?: string;
  colorVariant?: string;
  isConflicted?: boolean;
  isLocked?: boolean;
};

export type ResourceCalendarProps = {
  view: CalendarView;
  focusDate?: string;
  rangeStart: string;
  rangeEnd: string;
  resources: CalendarResource[];
  items: CalendarItem[];
  onRangeChange?: (nextStart: string, nextEnd: string) => void;
  onSlotSelect?: (resourceId: string | null, start: string, end: string) => void;
  onItemClick?: (itemId: string) => void;
  onItemMove?: (itemId: string, resourceId: string, start: string, end: string) => void;
  onItemResize?: (itemId: string, start: string, end: string) => void;
  renderItem?: (item: CalendarItem) => ReactNode;
  renderResourceHeader?: (resource: CalendarResource) => ReactNode;
};
