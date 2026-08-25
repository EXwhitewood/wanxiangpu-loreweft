export default function ChatSkeleton() {
  return (
    <div className="flex-1 overflow-auto px-4 py-3 space-y-4 animate-pulse">
      <div className="flex justify-start">
        <div className="max-w-[75%] space-y-2">
          <div className="h-4 w-64 rounded" style={{ backgroundColor: "var(--skeleton-bg)" }} />
          <div className="h-4 w-48 rounded" style={{ backgroundColor: "var(--skeleton-bg)", opacity: 0.7 }} />
        </div>
      </div>
      <div className="flex justify-end">
        <div className="max-w-[60%] space-y-2">
          <div className="h-4 w-40 rounded" style={{ backgroundColor: "var(--skeleton-bg)", opacity: 0.5 }} />
        </div>
      </div>
      <div className="flex justify-start">
        <div className="max-w-[75%] space-y-2">
          <div className="h-4 w-56 rounded" style={{ backgroundColor: "var(--skeleton-bg)" }} />
          <div className="h-4 w-72 rounded" style={{ backgroundColor: "var(--skeleton-bg)", opacity: 0.7 }} />
          <div className="h-4 w-32 rounded" style={{ backgroundColor: "var(--skeleton-bg)", opacity: 0.5 }} />
        </div>
      </div>
      <div className="flex justify-end">
        <div className="max-w-[60%] space-y-2">
          <div className="h-4 w-48 rounded" style={{ backgroundColor: "var(--skeleton-bg)", opacity: 0.5 }} />
          <div className="h-4 w-32 rounded" style={{ backgroundColor: "var(--skeleton-bg)", opacity: 0.3 }} />
        </div>
      </div>
      <div className="flex justify-start">
        <div className="max-w-[75%] space-y-2">
          <div className="h-4 w-80 rounded" style={{ backgroundColor: "var(--skeleton-bg)" }} />
          <div className="h-4 w-64 rounded" style={{ backgroundColor: "var(--skeleton-bg)", opacity: 0.7 }} />
        </div>
      </div>
    </div>
  );
}
