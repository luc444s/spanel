export function toDate(value: string | Date): Date {
  return value instanceof Date ? value : new Date(value);
}

export function startOfDay(value: string | Date): Date {
  const date = toDate(value);
  return new Date(date.getFullYear(), date.getMonth(), date.getDate(), 0, 0, 0, 0);
}

export function addDays(value: string | Date, days: number): Date {
  const date = startOfDay(value);
  date.setDate(date.getDate() + days);
  return date;
}

export function startOfWeek(value: string | Date): Date {
  const date = startOfDay(value);
  const day = date.getDay();
  const diff = day === 0 ? -6 : 1 - day;
  return addDays(date, diff);
}

export function endOfWeek(value: string | Date): Date {
  const start = startOfWeek(value);
  const end = addDays(start, 6);
  end.setHours(23, 59, 59, 999);
  return end;
}

export function startOfMonth(value: string | Date): Date {
  const date = toDate(value);
  return new Date(date.getFullYear(), date.getMonth(), 1, 0, 0, 0, 0);
}

export function endOfMonth(value: string | Date): Date {
  const date = toDate(value);
  return new Date(date.getFullYear(), date.getMonth() + 1, 0, 23, 59, 59, 999);
}

export function buildMonthGrid(focus: string | Date): Date[] {
  const monthStart = startOfMonth(focus);
  const gridStart = startOfWeek(monthStart);
  return Array.from({ length: 42 }, (_, index) => addDays(gridStart, index));
}

export function buildDateRange(start: string | Date, end: string | Date): Date[] {
  const result: Date[] = [];
  let cursor = startOfDay(start);
  const last = startOfDay(end);
  while (cursor <= last) {
    result.push(new Date(cursor));
    cursor = addDays(cursor, 1);
  }
  return result;
}

export function isSameDay(left: string | Date, right: string | Date): boolean {
  const a = startOfDay(left);
  const b = startOfDay(right);
  return a.getTime() === b.getTime();
}

export function formatDayNumber(value: string | Date): string {
  return String(toDate(value).getDate());
}

export function formatDayLabel(value: string | Date): string {
  return toDate(value).toLocaleDateString(undefined, { weekday: "short", day: "numeric" });
}

export function formatHourLabel(hour: number): string {
  return `${String(hour).padStart(2, "0")}:00`;
}

export function isoString(value: Date): string {
  return value.toISOString();
}

export function setHour(value: string | Date, hour: number): Date {
  const date = startOfDay(value);
  date.setHours(hour, 0, 0, 0);
  return date;
}
