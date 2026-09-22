import { ResourceCalendarMonthView } from "./resource-calendar-month-view";
import { ResourceCalendarTimeGrid } from "./resource-calendar-time-grid";
import type { ResourceCalendarProps } from "./resource-calendar-types";

export type {
  CalendarItem,
  CalendarResource,
  CalendarView,
  ResourceCalendarProps,
} from "./resource-calendar-types";

export function ResourceCalendar(props: ResourceCalendarProps) {
  if (props.view === "month") {
    return (
      <ResourceCalendarMonthView
        focusDate={props.focusDate ?? props.rangeStart}
        items={props.items}
        onSlotSelect={props.onSlotSelect}
        onItemClick={props.onItemClick}
        renderItem={props.renderItem}
      />
    );
  }

  return (
    <ResourceCalendarTimeGrid
      view={props.view}
      focusDate={props.focusDate ?? props.rangeStart}
      rangeStart={props.rangeStart}
      rangeEnd={props.rangeEnd}
      resources={props.resources}
      items={props.items}
      onSlotSelect={props.onSlotSelect}
      onItemClick={props.onItemClick}
      renderItem={props.renderItem}
      renderResourceHeader={props.renderResourceHeader}
    />
  );
}
