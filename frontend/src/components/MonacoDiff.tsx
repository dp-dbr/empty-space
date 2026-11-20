import { DiffEditor, loader } from "@monaco-editor/react";
import * as monaco from "monaco-editor";

// Use the bundled Monaco package (no CDN). Monaco's ESM build creates its web workers via
// `new URL(..., import.meta.url)`, which Vite bundles automatically.
loader.config({ monaco });

export default function MonacoDiff({ original, modified, language }: { original: string; modified: string; language: string }) {
  return (
    <DiffEditor
      height="420px"
      original={original}
      modified={modified}
      language={language}
      options={{ readOnly: true, renderSideBySide: true, minimap: { enabled: false }, wordWrap: "on" }}
    />
  );
}
