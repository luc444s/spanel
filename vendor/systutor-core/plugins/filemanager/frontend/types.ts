export type Entry = {
  name: string;
  directory: boolean;
  size: number | null;
  modified: number;
};

export type Listing = { entries: Entry[] };
export type TextFile = { content: string; version: string };
export type UploadProgress = { name: string; percent: number; status: string };
