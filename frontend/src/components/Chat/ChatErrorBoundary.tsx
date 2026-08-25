import { Component, type ReactNode } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";

interface ChatErrorBoundaryProps {
  children: ReactNode;
  agentName?: string;
}

interface ChatErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

export default class ChatErrorBoundary extends Component<ChatErrorBoundaryProps, ChatErrorBoundaryState> {
  constructor(props: ChatErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error };
  }

  handleRetry = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex h-full flex-col items-center justify-center px-8 py-12">
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-crimson-400/10 mb-4">
            <AlertTriangle className="h-7 w-7 text-crimson-400" />
          </div>
          <h3 className="text-sm font-semibold text-pine-700 mb-1">
            {this.props.agentName ? `${this.props.agentName}遇到了问题` : "聊天组件遇到了问题"}
          </h3>
          <p className="text-xs text-pine-700 text-center max-w-sm mb-4">
            {this.state.error?.message || "发生了未知错误"}
          </p>
          <button
            onClick={this.handleRetry}
            className="flex items-center gap-2 rounded-lg border border-pine-200 bg-white/50 px-4 py-2 text-xs text-pine-700 transition-colors hover:border-magic-500/30 hover:text-magic-400"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            重新加载
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}
