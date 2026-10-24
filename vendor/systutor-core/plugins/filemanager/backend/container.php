<?php
// Fixed program executed by the filemanager driver inside an authorized container.
// Arguments are base64-encoded JSON; untrusted paths are never evaluated by a shell.
error_reporting(E_ALL);
ini_set('display_errors', 'stderr');
function fail_op(string $code, string $message): never {
    throw new RuntimeException($code . ':' . $message);
}
$args = json_decode(base64_decode($argv[1] ?? '', true) ?: '', true);
if (!is_array($args)) fail_op('400', 'Invalid request');
$root = realpath($args['root'] ?? '');
if ($root === false || !is_dir($root)) fail_op('404', 'File root unavailable');
$root = rtrim($root, '/');
$max = (int)($args['limit'] ?? 52428800);
function name_ok(string $name): void {
    if ($name === '' || $name === '.' || $name === '..' || str_contains($name, '/') ||
        str_contains($name, '\\') || str_contains($name, "\0")) fail_op('400', 'Invalid name');
}
function target_path(string $relative, bool $mayNotExist = false): string {
    global $root;
    if (str_starts_with($relative, '/') || str_contains($relative, '\\') || str_contains($relative, "\0"))
        fail_op('400', 'Invalid path');
    $current = $root;
    foreach (explode('/', $relative) as $part) {
        if ($part === '') continue;
        name_ok($part);
        $current .= '/' . $part;
        if (is_link($current)) fail_op('400', 'Symbolic links are not accessible');
        if (!file_exists($current) && !$mayNotExist) fail_op('404', 'File not found');
    }
    // The root itself can be requested for listing, but never for mutation.
    if (!$mayNotExist && !file_exists($current)) fail_op('404', 'File not found');
    return $current;
}
function nonroot(string $relative): void {
    if (trim($relative, '/') === '') fail_op('400', 'Cannot modify root');
}
function entry(string $path): array {
    return ['name' => basename($path), 'directory' => is_dir($path),
        'size' => is_file($path) ? filesize($path) : null, 'modified' => filemtime($path)];
}
function parent_dir(string $relative): string {
    $parent = target_path(dirname($relative) === '.' ? '' : dirname($relative));
    if (!is_dir($parent)) fail_op('400', 'Parent is not a directory');
    return $parent;
}
function atomic_input(string $dest, bool $replace, ?string $expected = null): array {
    global $max;
    if (file_exists($dest) && (!$replace || !is_file($dest))) fail_op('409', 'File exists');
    $temp = tempnam(dirname($dest), '.spanel-');
    if ($temp === false) fail_op('403', 'Cannot write here');
    try {
        $out = fopen($temp, 'wb');
        $in = fopen('php://stdin', 'rb');
        $bytes = 0;
        while (!feof($in)) {
            $chunk = fread($in, 65536);
            if ($chunk === false) fail_op('400', 'Upload failed');
            $bytes += strlen($chunk);
            if ($bytes > $max) fail_op('413', 'File too large');
            if ($chunk !== '' && fwrite($out, $chunk) !== strlen($chunk)) fail_op('403', 'Write failed');
        }
        fclose($out);
        // Recheck right before replacing: an editor must not silently undo a
        // concurrent save between the initial version check and this write.
        if ($expected !== null && (!is_file($dest) || !hash_equals(hash_file('sha256', $dest), $expected)))
            fail_op('409', 'File changed; reload it');
        if (file_exists($dest)) chmod($temp, fileperms($dest) & 0777);
        else chmod($temp, 0644);
        if (!rename($temp, $dest)) fail_op('403', 'Cannot save file');
        return ['size' => $bytes, 'version' => hash_file('sha256', $dest)];
    } finally { if (file_exists($temp)) unlink($temp); }
}
function delete_tree(string $path): void {
    if (is_link($path)) fail_op('400', 'Symbolic links are not accessible');
    if (is_dir($path)) {
        foreach (new FilesystemIterator($path) as $child) delete_tree($child->getPathname());
        if (!rmdir($path)) fail_op('403', 'Cannot remove directory');
    } elseif (!unlink($path)) fail_op('403', 'Cannot remove file');
}
function add_zip(ZipArchive $zip, string $source, string $archiveName, int &$total): void {
    global $max;
    if (is_link($source)) fail_op('400', 'Symbolic links are not accessible');
    if (is_dir($source)) {
        $zip->addEmptyDir($archiveName);
        foreach (new FilesystemIterator($source) as $child)
            add_zip($zip, $child->getPathname(), $archiveName . '/' . $child->getFilename(), $total);
    } else {
        $total += filesize($source);
        if ($total > $max) fail_op('413', 'Archive too large');
        if (!$zip->addFile($source, $archiveName)) fail_op('400', 'Cannot add file');
    }
}
try {
    $op = $args['op'] ?? '';
    $relative = (string)($args['path'] ?? '');
    $path = target_path($relative, in_array($op, ['upload', 'mkdir', 'rename', 'write'], true));
    $result = [];
    switch ($op) {
        case 'list':
            if (!is_dir($path)) fail_op('400', 'Not a directory');
            $items = [];
            foreach (new FilesystemIterator($path) as $child) {
                if (is_link($child->getPathname())) continue;
                $items[] = entry($child->getPathname());
            }
            usort($items, fn($a, $b) => ($b['directory'] <=> $a['directory']) ?: strcasecmp($a['name'], $b['name']));
            $result = ['entries' => $items];
            break;
        case 'stat':
            $result = entry($path);
            break;
        case 'read':
            if (!is_file($path) || filesize($path) > $max) fail_op('413', 'Not a small text file');
            $text = file_get_contents($path);
            if (!mb_check_encoding($text, 'UTF-8') || str_contains($text, "\0")) fail_op('400', 'Not UTF-8 text');
            $result = ['content' => $text, 'version' => hash('sha256', $text)];
            break;
        case 'write':
            nonroot($relative);
            parent_dir($relative);
            if (!is_file($path) || filesize($path) > $max) fail_op('413', 'Not a small text file');
            if (!hash_equals(hash_file('sha256', $path), (string)($args['version'] ?? '')))
                fail_op('409', 'File changed; reload it');
            $result = atomic_input($path, true, (string)($args['version'] ?? ''));
            break;
        case 'upload':
            nonroot($relative);
            parent_dir($relative);
            $result = atomic_input($path, ($args['replace'] ?? false) === true);
            break;
        case 'mkdir':
            nonroot($relative);
            parent_dir($relative);
            if (file_exists($path)) fail_op('409', 'Name already exists');
            if (!mkdir($path, 0755)) fail_op('403', 'Cannot create directory');
            break;
        case 'rename':
            nonroot($relative);
            $newName = (string)($args['name'] ?? '');
            name_ok($newName);
            $dest = parent_dir($relative) . '/' . $newName;
            if (file_exists($dest) || is_link($dest)) fail_op('409', 'Name already exists');
            if (!rename($path, $dest)) fail_op('403', 'Cannot rename');
            break;
        case 'delete':
            nonroot($relative);
            delete_tree($path);
            break;
        case 'download':
            if (!is_file($path)) fail_op('400', 'Not a file');
            $in = fopen($path, 'rb');
            while (!feof($in)) { $chunk = fread($in, 65536); if ($chunk === false) break; echo $chunk; }
            exit(0);
        case 'zip':
            if (!is_dir($path)) fail_op('400', 'Not a directory');
            $name = (string)($args['name'] ?? '');
            name_ok($name);
            if (!str_ends_with(strtolower($name), '.zip')) fail_op('400', 'ZIP name required');
            $dest = $path . '/' . $name;
            if (file_exists($dest)) fail_op('409', 'Archive exists');
            $selected = $args['entries'] ?? [];
            if (!is_array($selected) || count($selected) < 1 || count($selected) > 200) fail_op('400', 'Invalid selection');
            $temp = tempnam($path, '.spanel-');
            $zip = new ZipArchive();
            try {
                if ($zip->open($temp, ZipArchive::OVERWRITE) !== true) fail_op('403', 'Cannot create ZIP');
                $total = 0;
                foreach ($selected as $item) {
                    name_ok((string)$item);
                    if ($item === $name) fail_op('400', 'Archive cannot contain itself');
                    $prefix = trim($relative, '/');
                    $source = target_path($prefix === '' ? $item : $prefix . '/' . $item);
                    add_zip($zip, $source, $item, $total);
                }
                if (!$zip->close()) fail_op('400', 'ZIP failed');
                if (!rename($temp, $dest)) fail_op('403', 'Cannot save ZIP');
            } finally { if (is_file($temp)) unlink($temp); }
            $result = entry($dest);
            break;
        case 'extract':
            if (!is_file($path) || !str_ends_with(strtolower($path), '.zip')) fail_op('400', 'ZIP required');
            $destination = target_path((string)($args['destination'] ?? ''));
            if (!is_dir($destination)) fail_op('400', 'Destination not a directory');
            $zip = new ZipArchive();
            if ($zip->open($path) !== true) fail_op('400', 'Invalid ZIP');
            $files = []; $total = 0;
            try {
                if ($zip->numFiles > 2000) fail_op('413', 'Too many entries');
                for ($i = 0; $i < $zip->numFiles; $i++) {
                    $info = $zip->statIndex($i); $name = $info['name'];
                    if ($name === '' || str_starts_with($name, '/') || str_contains($name, '\\') ||
                        str_contains($name, "\0")) fail_op('400', 'Unsafe ZIP path');
                    foreach (explode('/', trim($name, '/')) as $piece) name_ok($piece);
                    $zip->getExternalAttributesIndex($i, $opsys, $attributes);
                    if (($attributes >> 16 & 0170000) === 0120000) fail_op('400', 'ZIP links forbidden');
                    $total += $info['size'];
                    if ($total > $max) fail_op('413', 'Extracted data too large');
                    $files[] = [$i, $name, str_ends_with($name, '/')];
                }
                $created = [];
                foreach ($files as [$i, $name, $directory]) {
                    $destination_relative = trim((string)($args['destination'] ?? ''), '/');
                    $entry_name = trim($name, '/');
                    $rel = $destination_relative === ''
                        ? $entry_name : $destination_relative . '/' . $entry_name;
                    $dest = target_path($rel, true);
                    $pieces = explode('/', trim($name, '/'));
                    $cursor = $destination;
                    foreach (array_slice($pieces, 0, $directory ? null : -1) as $part) {
                        $cursor .= '/' . $part;
                        if (is_link($cursor) || (file_exists($cursor) && !is_dir($cursor))) fail_op('409', 'Path conflict');
                        if (!is_dir($cursor)) { if (!mkdir($cursor, 0755)) fail_op('403', 'Cannot create directory'); $created[] = $cursor; }
                    }
                    if ($directory) continue;
                    if (file_exists($dest)) continue; // Default policy: skip existing files.
                    $src = $zip->getStream($name);
                    if ($src === false) fail_op('400', 'Cannot read ZIP entry');
                    $temp = tempnam(dirname($dest), '.spanel-');
                    try {
                        $out = fopen($temp, 'wb'); $bytes = stream_copy_to_stream($src, $out, $max + 1);
                        fclose($out); fclose($src);
                        if ($bytes === false || $bytes > $max) fail_op('413', 'Entry too large');
                        chmod($temp, 0644);
                        if (!rename($temp, $dest)) fail_op('403', 'Cannot extract file');
                        $created[] = $dest;
                    } finally { if (is_file($temp)) unlink($temp); }
                }
                $result = ['extracted' => count($created)];
            } catch (Throwable $e) {
                foreach (array_reverse($created ?? []) as $item) {
                    if (is_dir($item)) @rmdir($item); else @unlink($item);
                }
                throw $e;
            } finally { $zip->close(); }
            break;
        default: fail_op('400', 'Unknown operation');
    }
    echo json_encode(['ok' => true] + $result, JSON_INVALID_UTF8_SUBSTITUTE);
} catch (Throwable $e) {
    $parts = explode(':', $e->getMessage(), 2);
    $status = in_array($parts[0], ['400', '403', '404', '409', '413'], true) ? $parts[0] : '500';
    fwrite(STDERR, json_encode(['status' => (int)$status, 'message' => $parts[1] ?? 'File operation failed']));
    exit(1);
}
