const TEXT_EXTENSIONS = new Set([
  "txt", "md", "markdown", "css", "scss", "less", "js", "jsx", "ts", "tsx",
  "json", "php", "html", "htm", "xml", "yml", "yaml", "sql", "log", "ini",
  "conf", "env", "twig", "sh", "bash", "htaccess", "gitignore",
]);

const TEXT_NAMES = new Set([".htaccess", ".env", ".gitignore", ".editorconfig"]);

export function within(folder: string, name: string) {
  return folder ? `${folder}/${name}` : name;
}

export function parentPath(path: string) {
  const slash = path.lastIndexOf("/");
  return slash < 0 ? "" : path.slice(0, slash);
}

export function isZipFile(name: string) {
  return name.toLowerCase().endsWith(".zip");
}

export function isTextFile(name: string) {
  const lower = name.toLowerCase();
  if (TEXT_NAMES.has(lower)) return true;
  const dot = lower.lastIndexOf(".");
  if (dot < 0 || dot === lower.length - 1) return false;
  return TEXT_EXTENSIONS.has(lower.slice(dot + 1));
}

export function isCoarsePointer() {
  return typeof window !== "undefined" && Boolean(window.matchMedia?.("(pointer: coarse)").matches);
}

const FILE_ICONS: Record<string, string> = {
  png: "🖼️", jpg: "🖼️", jpeg: "🖼️", gif: "🖼️", webp: "🖼️", svg: "🖼️", avif: "🖼️",
  zip: "🗜️", tar: "🗜️", gz: "🗜️", rar: "🗜️", "7z": "🗜️",
  pdf: "📕", doc: "📘", docx: "📘", xls: "📗", xlsx: "📗", ppt: "📙", pptx: "📙",
  mp3: "🎵", wav: "🎵", ogg: "🎵", flac: "🎵",
  mp4: "🎬", mov: "🎬", mkv: "🎬", webm: "🎬",
  php: "🐘", js: "📜", jsx: "📜", ts: "📜", tsx: "📜", css: "🎨", scss: "🎨",
  json: "🧾", xml: "🧾", yml: "🧾", yaml: "🧾", sql: "🗄️", md: "📝", txt: "📄",
  html: "🌐", htm: "🌐", sh: "⚙️", ini: "⚙️", conf: "⚙️", env: "⚙️",
};

export function fileIcon(name: string, directory: boolean) {
  if (directory) return "📁";
  const dot = name.lastIndexOf(".");
  const extension = dot < 0 ? "" : name.slice(dot + 1).toLowerCase();
  return FILE_ICONS[extension] ?? "📄";
}
