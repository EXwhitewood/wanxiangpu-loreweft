import { motion, useReducedMotion } from "framer-motion";
import { useLocation } from "react-router-dom";
import InteractiveNeuralBackground from "./InteractiveNeuralBackground";
import { useTheme } from "@/hooks/useTheme";

export default function BackgroundPatterns() {
  const location = useLocation();
  const { resolvedTheme } = useTheme();
  const reducedMotion = useReducedMotion();
  const projectIdMatch = location.pathname.match(/^\/project\/([^/]+)/);
  const id = projectIdMatch ? projectIdMatch[1] : null;
  const isDashboard = id
    ? (location.pathname === `/project/${id}` || location.pathname === `/project/${id}/`) && !location.search.includes("outline")
    : false;
  const isLobby = location.pathname === "/";
  const isOutlineWorkspace = Boolean(id && location.search.includes("outline"));
  const isStyleWorkspace = Boolean(id && location.pathname === `/project/${id}/style`);
  const mouseInteractionEnabled = isLobby || isDashboard || isOutlineWorkspace || isStyleWorkspace;
  const ambientMotionEnabled = mouseInteractionEnabled && !reducedMotion;

  return (
    <div
      className={`theme-atmosphere theme-atmosphere--${resolvedTheme} pointer-events-none fixed inset-0 z-0 overflow-hidden bg-[var(--atmosphere-base)]`}
      aria-hidden="true"
    >
      {resolvedTheme === "light" ? (
        <>
          <motion.div
            initial={{ x: -20, y: -50, scale: 0.9 }}
            animate={ambientMotionEnabled ? { x: 80, y: 20, scale: 1.1 } : undefined}
            transition={{ duration: 15, repeat: Infinity, repeatType: "mirror", ease: "easeInOut" }}
            className="absolute left-[-10%] top-[-10%] h-[60vw] w-[60vw] rounded-full bg-pine-200/60 mix-blend-multiply blur-[100px]"
          />
          <motion.div
            initial={{ x: 50, y: 80, scale: 1.1 }}
            animate={ambientMotionEnabled ? { x: -50, y: -20, scale: 0.9 } : undefined}
            transition={{ duration: 18, repeat: Infinity, repeatType: "mirror", ease: "easeInOut" }}
            className="absolute right-[-20%] top-[20%] h-[50vw] w-[50vw] rounded-full bg-tea-300/50 mix-blend-multiply blur-[120px]"
          />
          <motion.div
            initial={{ x: -30, y: 20, scale: 0.95 }}
            animate={ambientMotionEnabled ? { x: 40, y: -60, scale: 1.05 } : undefined}
            transition={{ duration: 16, repeat: Infinity, repeatType: "mirror", ease: "easeInOut" }}
            className="absolute bottom-[-20%] left-[20%] h-[70vw] w-[70vw] rounded-full bg-pine-300/40 mix-blend-multiply blur-[140px]"
          />
        </>
      ) : (
        <motion.svg
          viewBox="0 0 1600 900"
          preserveAspectRatio="xMidYMid slice"
          className="theme-crystal-field absolute -inset-[3%] h-[106%] w-[106%]"
          animate={ambientMotionEnabled ? { x: [-8, 7, -8], y: [-5, 6, -5] } : undefined}
          transition={{ duration: 32, repeat: Infinity, ease: "easeInOut" }}
        >
          <rect width="1600" height="900" fill="var(--atmosphere-base)" />
          <polygon points="0,0 640,0 460,330 0,430" fill="var(--crystal-facet-a)" />
          <polygon points="640,0 1180,0 920,390 460,330" fill="var(--crystal-facet-c)" />
          <polygon points="1180,0 1600,0 1600,360 920,390" fill="var(--crystal-facet-b)" />
          <polygon points="0,430 460,330 720,710 120,900 0,900" fill="var(--crystal-facet-c)" />
          <polygon points="460,330 920,390 1120,760 720,710" fill="var(--crystal-facet-a)" />
          <polygon points="920,390 1600,360 1600,900 1120,760" fill="var(--crystal-facet-c)" />
          <path d="M0 430L460 330L920 390L1600 360" fill="none" stroke="var(--astrolabe-grid)" strokeOpacity="0.055" strokeWidth="1" />
          <path d="M460 330L720 710L1120 760" fill="none" stroke="var(--astrolabe-grid)" strokeOpacity="0.045" strokeWidth="1" />
        </motion.svg>
      )}

      <InteractiveNeuralBackground
        theme={resolvedTheme}
        mouseInteractionEnabled={mouseInteractionEnabled}
        ambientMotionEnabled={ambientMotionEnabled}
      />

      {resolvedTheme === "light" ? (
        <>
          <div className="theme-astrolabe absolute -bottom-[20vw] -right-[10vw] h-[80vw] w-[80vw] mix-blend-color-burn">
            <motion.svg
              viewBox="0 0 1000 1000"
              className="h-full w-full"
              animate={ambientMotionEnabled ? { rotate: 360 } : undefined}
              transition={{ duration: 120, repeat: Infinity, ease: "linear" }}
            >
              <circle cx="500" cy="500" r="480" fill="none" stroke="#2c5e52" strokeWidth="2" strokeDasharray="10 10" />
              <circle cx="500" cy="500" r="380" fill="none" stroke="#2c5e52" strokeWidth="1" />
              <circle cx="500" cy="500" r="280" fill="none" stroke="#2c5e52" strokeWidth="4" strokeDasharray="20 40" />
              <polygon points="500,20 980,740 20,740" fill="none" stroke="#2c5e52" strokeWidth="1" />
              <polygon points="500,980 20,260 980,260" fill="none" stroke="#2c5e52" strokeWidth="1" />
              <line x1="500" y1="0" x2="500" y2="1000" stroke="#2c5e52" strokeWidth="1" opacity="0.5" />
              <line x1="0" y1="500" x2="1000" y2="500" stroke="#2c5e52" strokeWidth="1" opacity="0.5" />
              <line x1="146" y1="146" x2="854" y2="854" stroke="#2c5e52" strokeWidth="1" opacity="0.5" />
              <line x1="146" y1="854" x2="854" y2="146" stroke="#2c5e52" strokeWidth="1" opacity="0.5" />
            </motion.svg>
          </div>
          <div
            className="absolute inset-0 opacity-[0.25] mix-blend-multiply"
            style={{
              backgroundImage: `url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noiseFilter'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noiseFilter)'/%3E%3C/svg%3E")`,
            }}
          />
        </>
      ) : (
        <>
          <div className="theme-astrolabe absolute -bottom-[22vw] -right-[12vw] h-[82vw] w-[82vw]">
            <motion.svg
              viewBox="0 0 1000 1000"
              className="h-full w-full"
              animate={ambientMotionEnabled ? { rotate: 360 } : undefined}
              transition={{ duration: 150, repeat: Infinity, ease: "linear" }}
            >
              <circle cx="500" cy="500" r="480" fill="none" stroke="var(--astrolabe-grid)" strokeWidth="1.5" strokeDasharray="10 12" />
              <circle cx="500" cy="500" r="380" fill="none" stroke="var(--astrolabe-orbit)" strokeWidth="1" />
              <circle cx="500" cy="500" r="280" fill="none" stroke="var(--astrolabe-active)" strokeWidth="2.5" strokeDasharray="20 40" />
              <polygon points="500,20 980,740 20,740" fill="none" stroke="var(--astrolabe-grid)" strokeWidth="1" />
              <polygon points="500,980 20,260 980,260" fill="none" stroke="var(--astrolabe-grid)" strokeWidth="1" />
              <line x1="500" y1="0" x2="500" y2="1000" stroke="var(--astrolabe-grid)" strokeWidth="1" opacity="0.5" />
              <line x1="0" y1="500" x2="1000" y2="500" stroke="var(--astrolabe-grid)" strokeWidth="1" opacity="0.5" />
              <line x1="146" y1="146" x2="854" y2="854" stroke="var(--astrolabe-grid)" strokeWidth="1" opacity="0.5" />
              <line x1="146" y1="854" x2="854" y2="146" stroke="var(--astrolabe-grid)" strokeWidth="1" opacity="0.5" />
              <circle cx="500" cy="220" r="5" fill="var(--astrolabe-active)" />
              <circle cx="780" cy="500" r="4" fill="var(--astrolabe-node)" />
              <circle cx="310" cy="690" r="3.5" fill="var(--astrolabe-active)" />
            </motion.svg>
          </div>
          <div
            className="theme-atmosphere-texture absolute inset-0"
            style={{
              opacity: "var(--texture-opacity)",
              mixBlendMode: "var(--texture-blend)" as React.CSSProperties["mixBlendMode"],
              backgroundImage: `url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noiseFilter'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.72' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noiseFilter)'/%3E%3C/svg%3E")`,
            }}
          />
        </>
      )}
    </div>
  );
}
