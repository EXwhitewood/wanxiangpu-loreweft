import { lazy, Suspense } from "react";
import { Routes, Route, Navigate, useLocation } from "react-router-dom";
import { AnimatePresence } from "framer-motion";
import Sidebar from "@/components/Layout/Sidebar";
import BackgroundPatterns from "@/components/Layout/BackgroundPatterns";
import { useTheme } from "@/hooks/useTheme";

const ProjectList = lazy(() => import("@/pages/ProjectList"));
const ProjectWorkspace = lazy(() => import("@/pages/ProjectWorkspace"));
const EditorPage = lazy(() => import("@/pages/EditorPage"));
const StateEditorPage = lazy(() => import("@/pages/StateEditorPage"));
const SettingsPage = lazy(() => import("@/pages/SettingsPage"));
const WorldbuildingPage = lazy(() => import("@/pages/WorldbuildingPage"));
const IntelligencePage = lazy(() => import("@/pages/IntelligencePage"));
const StylePage = lazy(() => import("@/pages/StylePage"));
const ProjectHealthPage = lazy(() => import("@/pages/ProjectHealthPage"));
const ReaderCorpusAdminPage = lazy(() => import("@/pages/ReaderCorpusAdminPage"));
const AgentCenterPage = lazy(() => import("@/pages/AgentCenterPage"));
const EntertainmentModal = lazy(() => import("@/components/Entertainment/EntertainmentModal"));
const DesktopUpdateNotice = lazy(() => import("@/components/DesktopUpdateNotice"));

function RouteFallback() {
  return (
    <div className="flex h-full items-center justify-center text-sm tracking-[0.18em] text-pine-600">
      正在展开书页
    </div>
  );
}

export default function App() {
  const { resolvedTheme } = useTheme();
  const location = useLocation();

  return (
    <div
      data-app-theme={resolvedTheme}
      className="app-shell fixed inset-0 flex overflow-hidden bg-[#fdfbf6] text-surface-900 font-sans selection:bg-magic-500/20 selection:text-magic-700"
    >
      {/* Global Background Layer */}
      <BackgroundPatterns />

      <Sidebar />
      <main className="flex-1 min-w-0 h-full relative flex flex-col bg-transparent">
        <AnimatePresence mode="wait">
          <Suspense fallback={<RouteFallback />}>
            <Routes location={location} key={location.pathname}>
              <Route path="/" element={<ProjectList />} />
              <Route path="/project/:id" element={<ProjectWorkspace />}>
                <Route path="editor" element={<EditorPage />} />
                <Route path="state" element={<StateEditorPage />} />
                <Route path="worldbuilding" element={<WorldbuildingPage />} />
                <Route path="style" element={<StylePage />} />
                <Route path="intelligence" element={<IntelligencePage />} />
                <Route path="health" element={<ProjectHealthPage />} />
                <Route path="fbi" element={<Navigate to="../editor" replace />} />
                <Route path="foreshadowing" element={<Navigate to="../worldbuilding?tab=foreshadowing" replace />} />
              </Route>
              <Route path="/settings" element={<SettingsPage />} />
              <Route
                path="/admin/corpus"
                element={import.meta.env.DEV ? <ReaderCorpusAdminPage /> : <Navigate to="/" replace />}
              />
              <Route path="/agents" element={<AgentCenterPage />} />
              <Route path="/agents/:agentName" element={<AgentCenterPage />} />
              <Route path="/monitor" element={<Navigate to="/agents" replace />} />
            </Routes>
          </Suspense>
        </AnimatePresence>
      </main>

      {/* 娱乐弹窗保持全局挂载；触发入口由编辑器的工作流启动事件控制。 */}
      <Suspense fallback={null}>
        <EntertainmentModal />
      </Suspense>
      <Suspense fallback={null}>
        <DesktopUpdateNotice />
      </Suspense>
    </div>
  );
}
