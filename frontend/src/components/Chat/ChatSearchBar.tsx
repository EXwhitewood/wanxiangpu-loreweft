import { useState, useEffect, useCallback, useRef } from "react";
import { Search, X, ChevronUp, ChevronDown } from "lucide-react";

interface ChatSearchBarProps {
  onSearch: (query: string) => void;
  onClose: () => void;
  matchCount: number;
  currentMatch: number;
  onPrev: () => void;
  onNext: () => void;
}

export default function ChatSearchBar({
  onSearch,
  onClose,
  matchCount,
  currentMatch,
  onPrev,
  onNext,
}: ChatSearchBarProps) {
  const [query, setQuery] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const val = e.target.value;
      setQuery(val);
      onSearch(val);
    },
    [onSearch]
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter") {
        e.shiftKey ? onPrev() : onNext();
      }
      if (e.key === "Escape") {
        onClose();
      }
    },
    [onNext, onPrev, onClose]
  );

  return (
    <div className="flex items-center gap-2 rounded-lg border border-pine-200 bg-white/50 px-2 py-1.5 shadow-lg backdrop-blur-sm">
      <Search className="h-3.5 w-3.5 shrink-0 text-pine-700" />
      <input
        ref={inputRef}
        value={query}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        placeholder="搜索消息..."
        className="w-40 bg-transparent text-xs text-pine-700 outline-none placeholder:text-pine-700"
      />
      {query && (
        <span className="shrink-0 text-[10px] text-pine-700 tabular-nums">
          {matchCount > 0 ? `${currentMatch}/${matchCount}` : "无匹配"}
        </span>
      )}
      {query && matchCount > 0 && (
        <>
          <button
            onClick={onPrev}
            className="shrink-0 rounded p-0.5 text-pine-700 hover:bg-white/50 hover:text-pine-700"
          >
            <ChevronUp className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={onNext}
            className="shrink-0 rounded p-0.5 text-pine-700 hover:bg-white/50 hover:text-pine-700"
          >
            <ChevronDown className="h-3.5 w-3.5" />
          </button>
        </>
      )}
      <button
        onClick={onClose}
        className="shrink-0 rounded p-0.5 text-pine-700 hover:bg-white/50 hover:text-pine-700"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

export function highlightText(text: string, query: string): React.ReactNode {
  if (!query.trim()) return text;
  const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const parts = text.split(new RegExp(`(${escaped})`, "gi"));

  return parts.map((part, i) => {
    if (!part) return null;
    const isMatch = part.toLowerCase() === query.toLowerCase() ||
      new RegExp(`^${escaped}$`, "i").test(part);
    return isMatch ? (
      <mark key={i} className="rounded-sm px-0.5" style={{ backgroundColor: "var(--search-highlight-bg)", color: "var(--search-highlight-text)" }}>
        {part}
      </mark>
    ) : (
      part
    );
  });
}

export function useChatSearch(messages: Array<{ content: string; role: string }>) {
  const [searchQuery, setSearchQuery] = useState("");
  const [matchIndices, setMatchIndices] = useState<number[]>([]);
  const [currentMatchIdx, setCurrentMatchIdx] = useState(0);

  useEffect(() => {
    if (!searchQuery.trim()) {
      setMatchIndices([]);
      setCurrentMatchIdx(0);
      return;
    }
    const lower = searchQuery.toLowerCase();
    const indices: number[] = [];
    messages.forEach((msg, i) => {
      if (msg.content.toLowerCase().includes(lower)) {
        indices.push(i);
      }
    });
    setMatchIndices(indices);
    setCurrentMatchIdx(indices.length > 0 ? 1 : 0);
  }, [searchQuery, messages]);

  const currentMatch = matchIndices.length > 0 ? currentMatchIdx : 0;

  const goToNext = useCallback(() => {
    if (matchIndices.length === 0) return;
    const next = currentMatchIdx >= matchIndices.length ? 1 : currentMatchIdx + 1;
    setCurrentMatchIdx(next);
    scrollToMatch(matchIndices[next - 1]);
  }, [matchIndices, currentMatchIdx]);

  const goToPrev = useCallback(() => {
    if (matchIndices.length === 0) return;
    const prev = currentMatchIdx <= 1 ? matchIndices.length : currentMatchIdx - 1;
    setCurrentMatchIdx(prev);
    scrollToMatch(matchIndices[prev - 1]);
  }, [matchIndices, currentMatchIdx]);

  const scrollToMatch = (messageIndex: number) => {
    const el = document.querySelector(`[data-msg-index="${messageIndex}"]`);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      el.classList.add("search-highlight-flash");
      setTimeout(() => el.classList.remove("search-highlight-flash"), 1500);
    }
  };

  const isMatched = (messageIndex: number) => matchIndices.includes(messageIndex);
  const isCurrentMatch = (messageIndex: number) =>
    matchIndices[currentMatchIdx - 1] === messageIndex;

  return {
    searchQuery,
    setSearchQuery,
    matchCount: matchIndices.length,
    currentMatch,
    goToNext,
    goToPrev,
    isMatched,
    isCurrentMatch,
  };
}
