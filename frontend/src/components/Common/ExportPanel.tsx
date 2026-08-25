import { useState, useRef } from "react";
import {
  Download,
  Upload,
  FileText,
  FileDown,
  Loader2,
  X,
  Check,
  ChevronUp,
  ChevronDown,
} from "lucide-react";
import * as api from "@/api/client";

type ExportFormat = "archive" | "md" | "docx";

interface ExportPanelProps {
  projectId: string;
  projectName: string;
  compact?: boolean;
}

export default function ExportPanel({ projectId, projectName, compact }: ExportPanelProps) {
  const [expanded, setExpanded] = useState(false);
  const [exporting, setExporting] = useState<ExportFormat | null>(null);
  const [importing, setImporting] = useState(false);
  const [archiveCandidate, setArchiveCandidate] = useState<{
    archive: Record<string, unknown>;
    preview: api.ProjectArchivePreview;
  } | null>(null);
  const [importResult, setImportResult] = useState<{
    type: "success" | "error";
    message: string;
    details?: string;
  } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleExport = async (format: ExportFormat) => {
    setExporting(format);
    try {
      let blob: Blob;
      let filename: string;
      if (format === "archive") {
        blob = await api.exportProjectArchive(projectId);
        filename = `${projectName || "loreweft"}.loreweft.json`;
      } else if (format === "md") {
        blob = await api.exportMarkdown(projectId);
        filename = `${projectName || "loreweft"}.md`;
      } else {
        blob = await api.exportDocx(projectId);
        filename = `${projectName || "loreweft"}.docx`;
      }
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (e) {
      setImportResult({
        type: "error",
        message: `导出失败: ${(e as Error).message}`,
      });
      setTimeout(() => setImportResult(null), 4000);
    } finally {
      setExporting(null);
    }
  };

  const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const isDocx = file.name.endsWith(".docx");
    const isMd = file.name.endsWith(".md");
    const isArchive = file.name.endsWith(".loreweft.json");

    if (!isDocx && !isMd && !isArchive) {
      setImportResult({ type: "error", message: "仅支持 .loreweft.json、.md 和 .docx 文件" });
      setTimeout(() => setImportResult(null), 4000);
      return;
    }

    setImporting(true);
    setImportResult(null);

    try {
      if (isArchive) {
        const archive = JSON.parse(await file.text()) as Record<string, unknown>;
        const preview = await api.previewProjectArchive(projectId, archive);
        setArchiveCandidate({ archive, preview });
        setExpanded(true);
        setImportResult({
          type: "success",
          message: "无损归档预检通过，确认后才会恢复",
          details: `校验和 ${preview.checksum.slice(0, 12)}…`,
        });
      } else if (isMd) {
        const text = await file.text();
        const result = await api.importMarkdown(projectId, text);
        const details = [
          `章节: ${result.parsed_info.chapters_found}`,
          `人物: ${result.parsed_info.characters_found}`,
          `导入: ${result.imported.chapters}章`,
        ].filter(Boolean).join(" | ");
        setImportResult({ type: "success", message: result.message, details });
      } else {
        const arrayBuffer = await file.arrayBuffer();
        const bytes = new Uint8Array(arrayBuffer);
        let binary = "";
        for (let i = 0; i < bytes.length; i++) {
          binary += String.fromCharCode(bytes[i]);
        }
        const b64 = btoa(binary);
        const result = await api.importDocx(projectId, b64);
        const details = `章节: ${result.parsed_info.chapters_found} | 导入: ${result.imported.chapters}章`;
        setImportResult({ type: "success", message: result.message, details });
      }
    } catch (err) {
      setImportResult({ type: "error", message: `导入失败: ${(err as Error).message}` });
    } finally {
      setImporting(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
      setTimeout(() => setImportResult(null), 6000);
    }
  };

  const handleRestoreArchive = async () => {
    if (!archiveCandidate) return;
    const confirmation = window.prompt(`请输入项目名称“${projectName}”以确认恢复。恢复前系统会自动备份当前项目。`);
    if (!confirmation) return;
    setImporting(true);
    try {
      const result = await api.restoreProjectArchive(
        projectId,
        archiveCandidate.archive,
        confirmation,
      );
      setArchiveCandidate(null);
      setImportResult({
        type: "success",
        message: "项目已从无损归档恢复",
        details: `恢复前备份：${result.backup_path}`,
      });
      window.setTimeout(() => window.location.reload(), 800);
    } catch (cause) {
      setImportResult({ type: "error", message: `恢复失败：${(cause as Error).message}` });
    } finally {
      setImporting(false);
    }
  };

  if (compact) {
    return (
      <div className="relative">
        <div className="flex items-center justify-between px-2 py-1.5 text-xs text-pine-700">
          <div className="flex items-center gap-2">
            <button
              onClick={() => handleExport("archive")}
              disabled={exporting !== null}
              title="导出可恢复的无损项目归档"
              className="flex items-center gap-1 rounded px-1.5 py-0.5 transition-colors hover:text-magic-400 hover:bg-magic-500/10 disabled:opacity-50"
            >
              {exporting === "archive" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
              备份
            </button>
            <button
              onClick={() => handleExport("md")}
              disabled={exporting !== null}
              title="导出 Markdown"
              className="flex items-center gap-1 rounded px-1.5 py-0.5 transition-colors hover:text-magic-400 hover:bg-magic-500/10 disabled:opacity-50"
            >
              {exporting === "md" ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Download className="h-3.5 w-3.5" />
              )}
              Markdown
            </button>
            <button
              onClick={() => handleExport("docx")}
              disabled={exporting !== null}
              title="导出 Word"
              className="flex items-center gap-1 rounded px-1.5 py-0.5 transition-colors hover:text-magic-400 hover:bg-magic-500/10 disabled:opacity-50"
            >
              {exporting === "docx" ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <FileDown className="h-3.5 w-3.5" />
              )}
              Word
            </button>
            <button
              onClick={() => !importing && fileInputRef.current?.click()}
              disabled={importing}
              title="导入文件"
              className="flex items-center gap-1 rounded px-1.5 py-0.5 transition-colors hover:text-magic-400 hover:bg-magic-500/10 disabled:opacity-50"
            >
              {importing ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Upload className="h-3.5 w-3.5" />
              )}
              导入
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept=".loreweft.json,.md,.docx"
              onChange={handleFileSelect}
              className="hidden"
            />
          </div>
          <button
            onClick={() => setExpanded(!expanded)}
            title={expanded ? "收起" : "展开导入导出"}
            className="flex items-center gap-0.5 rounded px-1.5 py-0.5 transition-colors hover:text-magic-400 hover:bg-magic-500/10"
          >
            {expanded ? (
              <ChevronDown className="h-3.5 w-3.5" />
            ) : (
              <ChevronUp className="h-3.5 w-3.5" />
            )}
          </button>
        </div>

        {importResult && !expanded && (
          <div
            className={`mx-2 mb-1 flex items-center gap-1 rounded px-2 py-1 text-[11px] ${
              importResult.type === "success"
                ? "bg-emerald-50 text-emerald-600"
                : "bg-red-50 text-red-600"
            }`}
          >
            {importResult.type === "success" ? (
              <Check className="h-3 w-3 shrink-0" />
            ) : (
              <X className="h-3 w-3 shrink-0" />
            )}
            <span className="truncate">{importResult.message}</span>
          </div>
        )}

        {expanded && (
          <div className="absolute bottom-full left-0 right-0 border-t border-pine-200/40 bg-white/90 backdrop-blur-sm p-3 shadow-lg shadow-black/30">
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-xs font-medium text-pine-900">导入 / 导出</h3>
              <button
                onClick={() => setExpanded(false)}
                className="rounded p-0.5 text-pine-700 hover:text-pine-900 hover:bg-white/50"
              >
                <ChevronDown className="h-3.5 w-3.5" />
              </button>
            </div>

            <div className="grid grid-cols-3 gap-2 mb-2">
              <button
                onClick={() => handleExport("archive")}
                disabled={exporting !== null}
                className="flex items-center justify-center gap-1.5 rounded-lg border border-pine-200 bg-white px-2 py-2 text-xs font-medium text-pine-900 transition-colors hover:border-magic-500/40 hover:text-magic-600 hover:bg-magic-50 disabled:opacity-50"
              >
                {exporting === "archive" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
                无损备份
              </button>
              <button
                onClick={() => handleExport("md")}
                disabled={exporting !== null}
                className="flex items-center justify-center gap-1.5 rounded-lg border border-pine-200 bg-white px-2 py-2 text-xs font-medium text-pine-900 transition-colors hover:border-magic-500/40 hover:text-magic-600 hover:bg-magic-50 disabled:opacity-50"
              >
                {exporting === "md" ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Download className="h-3.5 w-3.5" />
                )}
                Markdown 阅读版
              </button>
              <button
                onClick={() => handleExport("docx")}
                disabled={exporting !== null}
                className="flex items-center justify-center gap-1.5 rounded-lg border border-pine-200 bg-white px-2 py-2 text-xs font-medium text-pine-900 transition-colors hover:border-magic-500/40 hover:text-magic-600 hover:bg-magic-50 disabled:opacity-50"
              >
                {exporting === "docx" ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <FileDown className="h-3.5 w-3.5" />
                )}
                Word 阅读版
              </button>
            </div>

            <div
              onClick={() => !importing && fileInputRef.current?.click()}
              className={`flex cursor-pointer items-center justify-center gap-1.5 rounded-lg border-2 border-dashed px-2 py-2 text-xs transition-colors ${
                importing
                  ? "border-pine-200 bg-white/50 text-pine-700"
                  : "border-pine-200 bg-white text-pine-800 hover:border-magic-500/40 hover:bg-magic-500/5 hover:text-magic-400"
              }`}
            >
              {importing ? (
                <><Loader2 className="h-3.5 w-3.5 animate-spin" />解析中...</>
              ) : (
                <><Upload className="h-3.5 w-3.5" />上传无损归档或旧格式阅读稿</>
              )}
            </div>

            {importResult && (
              <div
                className={`mt-2 flex items-start gap-1.5 rounded-lg p-2 ${
                  importResult.type === "success"
                    ? "border-emerald-200 bg-emerald-50"
                    : "border-red-200 bg-red-50"
                }`}
              >
                {importResult.type === "success" ? (
                  <Check className="mt-0.5 h-3 w-3 shrink-0 text-emerald-400" />
                ) : (
                  <X className="mt-0.5 h-3 w-3 shrink-0 text-red-400" />
                )}
                <div>
                  <p className={`text-[11px] font-medium ${importResult.type === "success" ? "text-emerald-400" : "text-red-400"}`}>
                    {importResult.message}
                  </p>
                  {importResult.details && (
                    <p className="text-[10px] text-pine-700">{importResult.details}</p>
                  )}
                </div>
              </div>
            )}

            {archiveCandidate && (
              <div className="mt-2 rounded-lg border border-amber-300 bg-amber-50 p-2 text-[11px] text-amber-900">
                <p className="font-medium">无损归档待确认</p>
                <p className="mt-1">
                  {archiveCandidate.preview.content_changed ? "当前项目与归档内容不同。" : "当前项目与归档内容一致。"}
                  涉及 {archiveCandidate.preview.differences.length} 个表计数差异。
                </p>
                <div className="mt-2 flex gap-2">
                  <button type="button" onClick={() => void handleRestoreArchive()} disabled={importing} className="rounded bg-amber-700 px-2 py-1 font-medium text-white disabled:opacity-50">
                    确认恢复
                  </button>
                  <button type="button" onClick={() => setArchiveCandidate(null)} disabled={importing} className="rounded border border-amber-400 px-2 py-1">
                    取消
                  </button>
                </div>
              </div>
            )}

            <p className="mt-2 text-[10px] text-pine-800">
              无损备份可恢复全部项目关系数据；Markdown / Word 仅供阅读，不作为备份。
            </p>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-white/50 bg-white/40 backdrop-blur-sm shadow-sm p-4">
      <h3 className="mb-3 text-sm font-medium text-pine-950">导入 / 导出</h3>
      <div className="grid grid-cols-3 gap-3 mb-3">
        <button
          onClick={() => handleExport("archive")}
          disabled={exporting !== null}
          className="flex items-center justify-center gap-2 rounded-lg border border-pine-200 bg-white px-3 py-2.5 text-xs font-medium text-pine-900 transition-colors hover:border-magic-500/40 hover:text-magic-600 hover:bg-magic-50 disabled:opacity-50"
        >
          {exporting === "archive" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
          无损备份
        </button>
        <button
          onClick={() => handleExport("md")}
          disabled={exporting !== null}
          className="flex items-center justify-center gap-2 rounded-lg border border-pine-200 bg-white px-3 py-2.5 text-xs font-medium text-pine-900 transition-colors hover:border-magic-500/40 hover:text-magic-600 hover:bg-magic-50 disabled:opacity-50"
        >
          {exporting === "md" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
          Markdown 阅读版
        </button>
        <button
          onClick={() => handleExport("docx")}
          disabled={exporting !== null}
          className="flex items-center justify-center gap-2 rounded-lg border border-pine-200 bg-white px-3 py-2.5 text-xs font-medium text-pine-900 transition-colors hover:border-magic-500/40 hover:text-magic-600 hover:bg-magic-50 disabled:opacity-50"
        >
          {exporting === "docx" ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileDown className="h-4 w-4" />}
          Word 阅读版
        </button>
      </div>
      <div
        onClick={() => !importing && fileInputRef.current?.click()}
        className={`flex cursor-pointer items-center justify-center gap-2 rounded-lg border-2 border-dashed px-3 py-3 text-xs transition-colors ${
          importing
            ? "border-pine-200 bg-white/50 text-pine-700"
            : "border-pine-200 bg-white text-pine-800 hover:border-magic-500/40 hover:bg-magic-500/5 hover:text-magic-400"
        }`}
      >
        {importing ? (
          <><Loader2 className="h-4 w-4 animate-spin" />正在解析文件...</>
        ) : (
          <><Upload className="h-4 w-4" />上传无损归档或旧格式阅读稿</>
        )}
      </div>
      <input ref={fileInputRef} type="file" accept=".loreweft.json,.md,.docx" onChange={handleFileSelect} className="hidden" />
      {importResult && (
        <div className={`mt-3 flex items-start gap-2 rounded-lg p-3 ${importResult.type === "success" ? "border-emerald-200 bg-emerald-50" : "border-red-200 bg-red-50"}`}>
          {importResult.type === "success" ? <Check className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400" /> : <X className="mt-0.5 h-4 w-4 shrink-0 text-red-400" />}
          <div>
            <p className={`text-xs font-medium ${importResult.type === "success" ? "text-emerald-400" : "text-red-400"}`}>{importResult.message}</p>
            {importResult.details && <p className="mt-1 text-[11px] text-pine-700">{importResult.details}</p>}
          </div>
        </div>
      )}
      {archiveCandidate && (
        <div className="mt-3 rounded-lg border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900">
          <p className="font-medium">无损归档预检通过，尚未写入</p>
          <p className="mt-1">表计数差异 {archiveCandidate.preview.differences.length} 处；恢复前会自动备份当前项目。</p>
          <div className="mt-2 flex gap-2">
            <button type="button" onClick={() => void handleRestoreArchive()} disabled={importing} className="rounded bg-amber-700 px-3 py-1.5 font-medium text-white disabled:opacity-50">确认恢复</button>
            <button type="button" onClick={() => setArchiveCandidate(null)} disabled={importing} className="rounded border border-amber-400 px-3 py-1.5">取消</button>
          </div>
        </div>
      )}
      <p className="mt-2 text-[11px] text-pine-800">
        <FileText className="mr-1 inline h-3 w-3" />无损备份用于恢复；Markdown / Word 仅供阅读。
      </p>
    </div>
  );
}
