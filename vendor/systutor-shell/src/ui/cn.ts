import { clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...values: Parameters<typeof clsx>) {
  return twMerge(clsx(values));
}
