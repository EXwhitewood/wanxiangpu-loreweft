/* Hallmark · pre-emit critique: P5 H5 E5 S4 R5 V5 · sidebar: collapsed-first hover expansion · tone: quiet studio · anchor hue: pine */
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  BookOpen,
  PenTool,
  Layers,
  Settings,
  Globe,
  BarChart3,
  Activity,
  Bot,
  Palette,
  Moon,
  Sun,
} from "lucide-react";
import { cn } from "@/utils/cn";
import { useProjectStore } from "@/stores/projectStore";
import { getThemeTransitionOrigin, useTheme } from "@/hooks/useTheme";

const SIDEBAR_WIDTH_TRANSITION = {
  duration: 0.32,
  ease: [0.16, 1, 0.3, 1] as [number, number, number, number],
};

const BrandIcon = ({ className, strokeWidth = 1.5 }: any) => (
  <svg viewBox="0 0 24 24" fill="none" className={className}>
    {/* Outer Hexagon */}
    <path d="M12 2 L21 7.2 V16.8 L12 22 L3 16.8 V7.2 Z" stroke="currentColor" strokeWidth={strokeWidth} strokeLinejoin="round" />
    {/* Inner weave / neural connections */}
    <path d="M12 2 V22" stroke="currentColor" strokeWidth={strokeWidth} className="opacity-30" />
    <path d="M3 7.2 L21 16.8" stroke="currentColor" strokeWidth={strokeWidth} className="opacity-30" />
    <path d="M3 16.8 L21 7.2" stroke="currentColor" strokeWidth={strokeWidth} className="opacity-30" />
    {/* Core / Star */}
    <path className="brand-icon-core" d="M12 9 L13 11 L15 12 L13 13 L12 15 L11 13 L9 12 L11 11 Z" />
  </svg>
);

const HallIcon = ({ className, strokeWidth = 1.5 }: any) => (
  <svg viewBox="0 0 24 24" fill="none" className={className}>
    <rect x="3" y="3" width="7" height="7" rx="1.5" stroke="currentColor" strokeWidth={strokeWidth} />
    <rect x="14" y="3" width="7" height="7" rx="1.5" stroke="currentColor" strokeWidth={strokeWidth} className="opacity-40" />
    <rect x="14" y="14" width="7" height="7" rx="1.5" stroke="currentColor" strokeWidth={strokeWidth} />
    <rect x="3" y="14" width="7" height="7" rx="1.5" stroke="currentColor" strokeWidth={strokeWidth} className="opacity-40" />
    <path d="M10 6.5h4M10 17.5h4M6.5 10v4M17.5 10v4" stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round" className="opacity-20"/>
  </svg>
);

const ActivityBarItem = ({ to, icon: Icon, label, exact = false }: { to: string, icon: any, label: string, exact?: boolean }) => {
  const location = useLocation();
  const isActive = exact ? location.pathname === to : location.pathname.startsWith(to) && to !== "/";
  // Fix for Home which is "/"
  const actuallyActive = to === "/" ? location.pathname === "/" : isActive;

  return (
    <NavLink
      to={to}
      title={label}
      className="group relative flex h-14 w-full items-center justify-center text-pine-700 transition-all hover:text-pine-600"
    >
      {actuallyActive && (
        <motion.div
          layoutId="activity-bar-active"
          className="absolute inset-0 pointer-events-none"
          initial={false}
          transition={{ type: "spring", stiffness: 300, damping: 30 }}
        >
          <div className="absolute inset-x-2 inset-y-1 rounded-xl bg-pine-500/10" />
          <div className="activity-active-rail absolute left-1.5 top-3 h-8 w-1 rounded-full bg-pine-600 shadow-[0_0_10px_rgba(84,150,136,0.5)]" />
        </motion.div>
      )}
      <Icon 
        className={cn(
          "relative z-10 h-6 w-6 transition-all duration-300",
          actuallyActive ? "text-pine-700 drop-shadow-[0_2px_8px_rgba(84,150,136,0.4)] scale-110" : "group-hover:scale-110"
        )} 
        strokeWidth={actuallyActive ? 2 : 1.5} 
      />
    </NavLink>
  );
};

const SidebarSectionHeader = ({ label, expanded }: { label: string; expanded: boolean }) => (
  <div className="relative h-6 overflow-hidden" aria-hidden={!expanded}>
    <motion.span
      initial={false}
      animate={{ opacity: expanded ? 1 : 0, x: expanded ? 0 : -8 }}
      transition={{ duration: 0.16, delay: expanded ? 0.08 : 0, ease: [0.16, 1, 0.3, 1] }}
      className="absolute inset-x-3 top-0 whitespace-nowrap text-xs font-semibold text-pine-700"
    >
      {label}
    </motion.span>
    <motion.span
      initial={false}
      animate={{ opacity: expanded ? 0 : 1, scaleX: expanded ? 0.5 : 1 }}
      transition={{ duration: 0.14, delay: expanded ? 0 : 0.08, ease: "easeOut" }}
      className="absolute left-1/2 top-2 h-px w-4 -translate-x-1/2 origin-center bg-[var(--border-emphasis)]"
    />
  </div>
);

const SidebarItem = ({
  to,
  icon: Icon,
  label,
  onClick,
  isActiveOverride,
  wasDashboard,
  compact = false,
  onExpandIntent,
  onExpandCancel,
}: any) => {
  const location = useLocation();
  const isActuallyActive = isActiveOverride !== undefined 
    ? isActiveOverride 
    : location.pathname === to;

  const content = (
    <>
      {isActuallyActive && (
        <motion.div
          layoutId="project-sidebar-active"
          className="sidebar-project-active absolute inset-0 rounded-lg border border-[var(--border-emphasis)] bg-[var(--workspace-sidebar-active)]"
          initial={wasDashboard ? { opacity: 0, scale: 0.95 } : false}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ type: "spring", stiffness: 300, damping: 30 }}
        />
      )}
      <span className="relative z-10 flex h-10 w-full items-center">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center">
          <Icon className={cn("h-[18px] w-[18px] transition-colors", isActuallyActive ? "text-pine-700" : "text-pine-700 group-hover:text-pine-700")} strokeWidth={isActuallyActive ? 2.25 : 1.8} />
        </span>
        <AnimatePresence initial={false} mode="popLayout">
          {!compact && (
            <motion.span
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -8 }}
              transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}
              className={cn("ml-2.5 whitespace-nowrap text-[15px] transition-colors", isActuallyActive ? "font-semibold text-pine-950" : "font-medium text-pine-800 group-hover:text-pine-950")}
            >
              {label}
            </motion.span>
          )}
        </AnimatePresence>
      </span>
    </>
  );

  const baseClasses = cn(
    "project-sidebar-item group relative flex h-10 w-full items-center rounded-lg p-0 outline-none cursor-pointer transition-colors focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-pine-600/35 active:bg-pine-900/5",
    compact && "justify-center",
  );

  if (to) {
    return (
      <NavLink
        to={to}
        end={to === "/"}
        onClick={onClick}
        onPointerEnter={onExpandIntent}
        onPointerLeave={onExpandCancel}
        className={baseClasses}
        title={label}
      >
        {content}
      </NavLink>
    );
  }

  return (
    <button
      onClick={onClick}
      onPointerEnter={onExpandIntent}
      onPointerLeave={onExpandCancel}
      className={baseClasses}
      title={label}
    >
      {content}
    </button>
  );
};

export default function Sidebar() {
  const location = useLocation();
  const navigate = useNavigate();
  const setOutlineView = useProjectStore((s) => s.setOutlineView);
  const setOutlineDesignerOpen = useProjectStore((s) => s.setOutlineDesignerOpen);
  const setLeftPanelVisible = useProjectStore((s) => s.setLeftPanelVisible);
  const { resolvedTheme, toggleTheme } = useTheme();
  const [compactViewport, setCompactViewport] = useState(
    () => typeof window !== "undefined" && window.innerWidth < 900,
  );
  const [sidebarHovered, setSidebarHovered] = useState(false);
  const [firstEntryExpanded, setFirstEntryExpanded] = useState(false);
  const expandTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const collapseTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const projectIdMatch = location.pathname.match(/^\/project\/([^/]+)/);
  const id = projectIdMatch ? projectIdMatch[1] : null;
  const isProjectDashboard = Boolean(
    id
    && (location.pathname === `/project/${id}` || location.pathname === `/project/${id}/`)
    && !location.search.includes("outline"),
  );

  const previousPathRef = useRef(location.pathname);
  const previousSearchRef = useRef(location.search);
  
  useEffect(() => {
    const routeChanged = previousPathRef.current !== location.pathname
      || previousSearchRef.current !== location.search;
    if (routeChanged && id) {
      setFirstEntryExpanded(isProjectDashboard);
    }
    previousPathRef.current = location.pathname;
    previousSearchRef.current = location.search;
  }, [id, isProjectDashboard, location.pathname, location.search]);

  useEffect(() => {
    if (!id || typeof window === "undefined") {
      clearSidebarExpandTimer();
      setFirstEntryExpanded(false);
      return;
    }

    // The project dashboard stays expanded; detail routes get a short
    // compact rail. Expansion on detail routes is pointer-intent driven.
    // This also works in embedded webviews where Web Storage may be absent.
    setFirstEntryExpanded(isProjectDashboard);
  }, [id]);

  useEffect(() => {
    const updateViewportMode = () => setCompactViewport(window.innerWidth < 900);
    window.addEventListener("resize", updateViewportMode);
    return () => window.removeEventListener("resize", updateViewportMode);
  }, []);

  const wasDashboard = id ? (previousPathRef.current === `/project/${id}` || previousPathRef.current === `/project/${id}/`) && !previousSearchRef.current.includes('outline') : false;

  const handleOutlineClick = () => {
    if (!id) return;
    setOutlineView("master");
    setOutlineDesignerOpen(true);
    setLeftPanelVisible(true);
    navigate(`/project/${id}?outline=master`);
  };

  const handleOtherNavClick = () => {
    setOutlineView(null);
    setOutlineDesignerOpen(false);
    setLeftPanelVisible(true);
  };

  const clearSidebarExpandTimer = () => {
    if (expandTimerRef.current !== null) {
      window.clearTimeout(expandTimerRef.current);
      expandTimerRef.current = null;
    }
  };

  const clearSidebarCollapseTimer = () => {
    if (collapseTimerRef.current !== null) {
      window.clearTimeout(collapseTimerRef.current);
      collapseTimerRef.current = null;
    }
  };

  const scheduleSidebarExpand = () => {
    if (!id || compactViewport || firstEntryExpanded || sidebarHovered) return;
    clearSidebarCollapseTimer();
    clearSidebarExpandTimer();
    expandTimerRef.current = window.setTimeout(() => {
      expandTimerRef.current = null;
      setSidebarHovered(true);
    }, 1500);
  };

  const scheduleSidebarCollapse = () => {
    // The dashboard is intentionally persistent; only hover-expanded detail
    // routes collapse after the pointer has actually left the rail.
    if (!id || compactViewport || firstEntryExpanded || !sidebarHovered) return;
    clearSidebarCollapseTimer();
    collapseTimerRef.current = window.setTimeout(() => {
      collapseTimerRef.current = null;
      setSidebarHovered(false);
    }, 150);
  };

  useEffect(() => () => {
    clearSidebarExpandTimer();
    clearSidebarCollapseTimer();
  }, []);

  const sidebarExpanded = Boolean(id && !compactViewport && (firstEntryExpanded || sidebarHovered));



  return (
    <motion.aside
      initial={false}
      animate={{ width: sidebarExpanded ? 220 : 64 }}
      transition={SIDEBAR_WIDTH_TRANSITION}
      onPointerEnter={clearSidebarCollapseTimer}
      onMouseLeave={() => {
        clearSidebarExpandTimer();
        scheduleSidebarCollapse();
      }}
      onPointerLeave={() => {
        clearSidebarExpandTimer();
        scheduleSidebarCollapse();
      }}
      className={cn(
        "workspace-shell-glass relative z-40 flex shrink-0 flex-col overflow-hidden shadow-[4px_0_24px_rgba(0,0,0,0.02)]",
        sidebarExpanded ? "sidebar-expanded" : "sidebar-collapsed",
      )}
    >
      <AnimatePresence initial={false}>
        {!id ? (
          <motion.div
            key="activity-bar"
            initial={{ opacity: 0, x: -20 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -20 }}
            transition={{ duration: 0.2 }}
            className="absolute inset-y-0 left-0 w-16 flex flex-col items-center h-full py-4 shrink-0"
          >
            <div className="flex w-full items-center justify-center mb-8 relative group cursor-pointer" onClick={() => navigate('/')}>
              <div className="brand-icon-surface relative flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-b from-pine-500 to-pine-800 shadow-[0_8px_16px_rgba(84,150,136,0.3)] ring-1 ring-white/30 overflow-hidden transition-transform duration-500 group-hover:scale-105">
                <div className="absolute inset-0 bg-gradient-to-tr from-transparent to-white/20" />
                <BrandIcon className="relative z-10 h-6 w-6 drop-shadow-md" strokeWidth={2} />
              </div>
            </div>

            <nav className="flex w-full flex-1 flex-col items-center gap-2">
              <ActivityBarItem to="/" icon={HallIcon} label="项目大厅" exact />
              <ActivityBarItem to="/agents" icon={Bot} label="智能体中心" />
            </nav>
            <div className="flex w-full flex-col items-center pb-2">
              <button
                type="button"
                onClick={(event) => toggleTheme(getThemeTransitionOrigin(event.currentTarget))}
                title={resolvedTheme === "night" ? "切换浅色主题" : "切换暗夜主题"}
                aria-label={resolvedTheme === "night" ? "切换浅色主题" : "切换暗夜主题"}
                className="theme-quick-toggle flex h-12 w-full items-center justify-center text-pine-700 transition-colors hover:text-pine-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-pine-600/35"
              >
                {resolvedTheme === "night" ? <Sun className="h-5 w-5" /> : <Moon className="h-5 w-5" />}
              </button>
              <ActivityBarItem to="/settings" icon={Settings} label="设置" />
            </div>
          </motion.div>
        ) : (
          <motion.div
            key="project-sidebar"
            initial={{ opacity: 0, x: 20, width: sidebarExpanded ? 220 : 64 }}
            animate={{ opacity: 1, x: 0, width: sidebarExpanded ? 220 : 64 }}
            exit={{ opacity: 0, x: 20 }}
            transition={{
              opacity: { duration: 0.2 },
              x: { duration: 0.2 },
              width: SIDEBAR_WIDTH_TRANSITION,
            }}
            className="absolute inset-y-0 left-0 flex h-full shrink-0 flex-col"
          >
            <div className="flex h-14 shrink-0 items-center px-3">
              <button 
                onClick={() => navigate('/')}
                title="返回大厅"
                aria-label="返回大厅"
                onPointerEnter={scheduleSidebarExpand}
                onPointerLeave={clearSidebarExpandTimer}
                className="sidebar-hall-return flex h-10 w-full items-center rounded-lg text-pine-700 transition-colors hover:text-pine-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-pine-600/35"
              >
                <span className="brand-icon-surface sidebar-return-mark relative flex h-10 w-10 shrink-0 items-center justify-center overflow-hidden rounded-lg">
                  <span className="absolute inset-0 bg-gradient-to-tr from-transparent to-white/10" />
                  <BrandIcon className="relative z-10 h-[22px] w-[22px]" strokeWidth={1.8} />
                </span>
                <AnimatePresence initial={false} mode="popLayout">
                  {sidebarExpanded && (
                    <motion.span
                      initial={{ opacity: 0, x: -8 }}
                      animate={{ opacity: 1, x: 0 }}
                      exit={{ opacity: 0, x: -8 }}
                      transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}
                      className="ml-2.5 whitespace-nowrap text-sm font-semibold"
                    >
                      返回大厅
                    </motion.span>
                  )}
                </AnimatePresence>
              </button>
            </div>

            <nav className="flex-1 overflow-y-auto px-3 pb-6 space-y-6 scrollbar-none">
              <div>
                <SidebarSectionHeader label="工作区" expanded={sidebarExpanded} />
                <div className="space-y-0.5">
                  <SidebarItem 
                    icon={BookOpen} 
                    label="结构大纲" 
                    onClick={handleOutlineClick} 
                    isActiveOverride={location.search.includes('outline')} 
                    wasDashboard={wasDashboard}
                    compact={!sidebarExpanded}
                    onExpandIntent={scheduleSidebarExpand}
                    onExpandCancel={clearSidebarExpandTimer}
                  />
                  <SidebarItem to={`/project/${id}/worldbuilding`} icon={Globe} label="世界法则" onClick={handleOtherNavClick} wasDashboard={wasDashboard} compact={!sidebarExpanded} onExpandIntent={scheduleSidebarExpand} onExpandCancel={clearSidebarExpandTimer} />
                  <SidebarItem to={`/project/${id}/editor`} icon={PenTool} label="沉浸创作" onClick={handleOtherNavClick} wasDashboard={wasDashboard} compact={!sidebarExpanded} onExpandIntent={scheduleSidebarExpand} onExpandCancel={clearSidebarExpandTimer} />
                  <SidebarItem to={`/project/${id}/state`} icon={Layers} label="状态总览" onClick={handleOtherNavClick} wasDashboard={wasDashboard} compact={!sidebarExpanded} onExpandIntent={scheduleSidebarExpand} onExpandCancel={clearSidebarExpandTimer} />
                  <SidebarItem to={`/project/${id}/style`} icon={Palette} label="文笔工坊" onClick={handleOtherNavClick} wasDashboard={wasDashboard} compact={!sidebarExpanded} onExpandIntent={scheduleSidebarExpand} onExpandCancel={clearSidebarExpandTimer} />
                </div>
              </div>
              
              <div>
                <SidebarSectionHeader label="分析与诊断" expanded={sidebarExpanded} />
                <div className="space-y-0.5">
                  <SidebarItem to={`/project/${id}/intelligence`} icon={BarChart3} label="市场情报" onClick={handleOtherNavClick} wasDashboard={wasDashboard} compact={!sidebarExpanded} onExpandIntent={scheduleSidebarExpand} onExpandCancel={clearSidebarExpandTimer} />
                  <SidebarItem to={`/project/${id}/health`} icon={Activity} label="健康体检" onClick={handleOtherNavClick} wasDashboard={wasDashboard} compact={!sidebarExpanded} onExpandIntent={scheduleSidebarExpand} onExpandCancel={clearSidebarExpandTimer} />
                </div>
              </div>
            </nav>

            <div className="theme-quick-toggle-wrap space-y-1 border-t border-pine-900/10 px-3 py-3">
              <button
                type="button"
                onClick={(event) => toggleTheme(getThemeTransitionOrigin(event.currentTarget))}
                title={resolvedTheme === "night" ? "切换浅色主题" : "切换暗夜主题"}
                aria-label={resolvedTheme === "night" ? "切换浅色主题" : "切换暗夜主题"}
                onPointerEnter={scheduleSidebarExpand}
                onPointerLeave={clearSidebarExpandTimer}
                className={cn(
                  "theme-quick-toggle flex h-10 w-full items-center rounded-lg p-0 text-sm font-medium text-pine-800 transition-colors hover:bg-[var(--color-accent-soft)] hover:text-pine-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-pine-600/35",
                  !sidebarExpanded && "justify-center",
                )}
              >
                <span className="flex h-10 w-10 shrink-0 items-center justify-center">
                  {resolvedTheme === "night" ? <Sun className="h-[18px] w-[18px]" /> : <Moon className="h-[18px] w-[18px]" />}
                </span>
                <AnimatePresence initial={false}>
                  {sidebarExpanded && (
                    <motion.span
                      initial={{ opacity: 0, x: -8 }}
                      animate={{ opacity: 1, x: 0 }}
                      exit={{ opacity: 0, x: -8 }}
                      transition={{ duration: 0.16, delay: 0.06, ease: [0.16, 1, 0.3, 1] }}
                      className="ml-2.5 whitespace-nowrap"
                    >
                      {resolvedTheme === "night" ? "切换浅色主题" : "切换暗夜主题"}
                    </motion.span>
                  )}
                </AnimatePresence>
              </button>

              <NavLink
                to="/settings"
                title="设置"
                aria-label="设置"
                onPointerEnter={scheduleSidebarExpand}
                onPointerLeave={clearSidebarExpandTimer}
                className={cn(
                  "sidebar-settings-link flex h-10 w-full items-center rounded-lg p-0 text-sm font-medium text-pine-800 transition-colors hover:bg-[var(--color-accent-soft)] hover:text-pine-950 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-pine-600/35",
                  !sidebarExpanded && "justify-center",
                )}
              >
                <span className="flex h-10 w-10 shrink-0 items-center justify-center">
                  <Settings className="h-[18px] w-[18px]" />
                </span>
                <AnimatePresence initial={false}>
                  {sidebarExpanded && (
                    <motion.span
                      initial={{ opacity: 0, x: -8 }}
                      animate={{ opacity: 1, x: 0 }}
                      exit={{ opacity: 0, x: -8 }}
                      transition={{ duration: 0.16, delay: 0.06, ease: [0.16, 1, 0.3, 1] }}
                      className="ml-2.5 whitespace-nowrap"
                    >
                      设置
                    </motion.span>
                  )}
                </AnimatePresence>
              </NavLink>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.aside>
  );
}
