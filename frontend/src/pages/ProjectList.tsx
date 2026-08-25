import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { motion, useMotionValue, useTransform, useSpring } from "framer-motion";
import { Plus, BookOpen, Clock, FileText, Trash2, Sparkles, BookMarked, ArrowRight } from "lucide-react";
import { useProjectStore } from "@/stores/projectStore";
import * as api from "@/api/client";
import Modal from "@/components/Common/Modal";
import Toast from "@/components/Common/Toast";
import ProjectCreateDialog from "@/components/Project/ProjectCreateDialog";
import { AnimatedCard } from "@/components/UI/AnimatedCard";
import { GlassButton } from "@/components/UI/GlassButton";
import { FadeInWrapper } from "@/components/UI/FadeInWrapper";
import { useTheme } from "@/hooks/useTheme";

const NightLoomDial = () => (
  <svg
    viewBox="0 0 200 200"
    className="lobby-night-dial h-[184px] w-[184px]"
    fill="none"
    aria-hidden="true"
  >
    <circle className="lobby-night-dial__halo" cx="100" cy="100" r="82" />
    <circle className="lobby-night-dial__track" cx="100" cy="100" r="72" />
    <circle
      className="lobby-night-dial__arc lobby-night-dial__arc--mint"
      cx="100"
      cy="100"
      r="63"
      pathLength="100"
      transform="rotate(132 100 100)"
    />
    <circle
      className="lobby-night-dial__arc lobby-night-dial__arc--gold"
      cx="100"
      cy="100"
      r="63"
      pathLength="100"
      transform="rotate(-48 100 100)"
    />

    <g className="lobby-night-dial__ticks">
      <path d="M100 31V41 M169 100H159 M100 169V159 M31 100H41" />
      <path d="M51 51L58 58 M149 51L142 58 M149 149L142 142 M51 149L58 142" />
    </g>

    <g className="lobby-night-dial__weave">
      <path d="M61 74Q100 104 139 74" />
      <path d="M61 126Q100 96 139 126" />
      <path d="M74 61Q104 100 74 139" />
      <path d="M126 61Q96 100 126 139" />
      <circle cx="100" cy="100" r="34" />
    </g>

    <motion.g
      className="lobby-night-dial__needle"
      variants={{
        rest: { rotate: 0 },
        hover: { rotate: 4, transition: { duration: 0.35, ease: "easeOut" } },
      }}
      style={{ originX: "100px", originY: "100px" }}
    >
      <path className="lobby-night-dial__needle-tail" d="M96 103L61 151L103 106L100 100Z" />
      <path className="lobby-night-dial__needle-head" d="M100 100L139 49L104 97L103 106Z" />
      <path className="lobby-night-dial__needle-ridge" d="M64 146L136 53" />
    </motion.g>

    <circle className="lobby-night-dial__hub-ring" cx="100" cy="100" r="15" />
    <circle className="lobby-night-dial__hub" cx="100" cy="100" r="7" />
    <circle className="lobby-night-dial__node lobby-night-dial__node--gold" cx="139" cy="49" r="3.5" />
    <circle className="lobby-night-dial__node" cx="61" cy="151" r="3" />
    <path className="lobby-night-dial__spark" d="M151 68L153 73L158 75L153 77L151 82L149 77L144 75L149 73Z" />
  </svg>
);

const ProjectCodexIcon = () => (
  <svg viewBox="0 0 24 24" className="h-7 w-7" fill="none" aria-hidden="true">
    <path
      className="lobby-project-mark__page"
      d="M4 5.5C6.7 4.7 9.35 5.25 12 7.1V19C9.5 17.45 6.85 16.9 4 17.75V5.5Z"
    />
    <path
      className="lobby-project-mark__page lobby-project-mark__page--right"
      d="M20 5.5C17.3 4.7 14.65 5.25 12 7.1V19C14.5 17.45 17.15 16.9 20 17.75V5.5Z"
    />
    <path className="lobby-project-mark__spine" d="M12 7.1V19" />
    <path className="lobby-project-mark__line" d="M6.6 8.4C7.7 8.2 8.8 8.45 9.8 9" />
    <path className="lobby-project-mark__line" d="M14.2 9C15.25 8.45 16.35 8.2 17.45 8.4" />
    <path className="lobby-project-mark__star" d="M17.25 11.4L17.85 12.75L19.2 13.35L17.85 13.95L17.25 15.3L16.65 13.95L15.3 13.35L16.65 12.75Z" />
  </svg>
);

export default function ProjectList() {
  const navigate = useNavigate();
  const { resolvedTheme } = useTheme();
  const { projects, loading, fetchProjects, createProject } = useProjectStore();
  const [showCreate, setShowCreate] = useState(false);
  const [toast, setToast] = useState<{ type: "success" | "error"; message: string } | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<{id: string; name: string} | null>(null);
  const [deleting, setDeleting] = useState(false);

  const x = useMotionValue(0);
  const y = useMotionValue(0);

  const mouseXSpring = useSpring(x, { stiffness: 150, damping: 15 });
  const mouseYSpring = useSpring(y, { stiffness: 150, damping: 15 });

  const rotateX = useTransform(mouseYSpring, [-0.5, 0.5], [15, -15]);
  const rotateY = useTransform(mouseXSpring, [-0.5, 0.5], [-15, 15]);

  const handleMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const width = rect.width;
    const height = rect.height;
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;
    x.set(mouseX / width - 0.5);
    y.set(mouseY / height - 0.5);
  };

  const handleMouseLeave = () => {
    x.set(0);
    y.set(0);
  };

  useEffect(() => {
    fetchProjects();
  }, [fetchProjects]);

  const formatTime = (dateStr: string) => {
    const d = new Date(dateStr);
    return d.toLocaleDateString("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  };

  const handleDelete = async () => {
    if (!deleteTarget || deleting) return;
    setDeleting(true);
    try {
      await api.deleteProject(deleteTarget.id);
      setDeleteTarget(null);
      setToast({ type: "success", message: "项目已归于虚无" });
      fetchProjects();
    } catch {
      setToast({ type: "error", message: "操作失败，请重试" });
    } finally {
      setDeleting(false);
    }
  };

  const formatWords = (n: number) => {
    if (n >= 10000) return `${(n / 10000).toFixed(1)}万`;
    if (n >= 1000) return `${(n / 1000).toFixed(1)}千`;
    return `${n}`;
  };

  const containerVariants = {
    hidden: { opacity: 0 },
    show: {
      opacity: 1,
      transition: {
        staggerChildren: 0.1
      }
    }
  };

  const itemVariants = {
    hidden: { opacity: 0, y: 20 },
    show: { opacity: 1, y: 0, transition: { type: "spring" as const, stiffness: 300, damping: 24 } }
  };

  return (
    <div className="lobby-page min-h-screen relative overflow-y-auto bg-transparent">
      <FadeInWrapper className="mx-auto w-full max-w-7xl px-8 py-16 relative z-10">
        <div className="mb-20 flex flex-col lg:flex-row items-center justify-between gap-12">
          
          <div className="flex-1 max-w-2xl">
            <motion.div 
              initial={{ opacity: 0, x: -30 }} 
              animate={{ opacity: 1, x: 0 }} 
              transition={{ duration: 0.8, ease: "easeOut" }}
            >
              <div className="inline-flex items-center gap-2 px-4 py-1.5 mb-8 rounded-full bg-tea-100/80 border border-tea-200 text-pine-700 text-sm font-medium shadow-sm backdrop-blur-md">
                <Sparkles className="h-4 w-4 text-pine-500" />
                <span className="tracking-widest">智能小说引擎</span>
              </div>
              
              <h1 className="text-5xl md:text-7xl font-black tracking-tight mb-6 drop-shadow-sm">
                <span className="block mb-2 text-pine-700">编织千丝万缕</span>
                <span className="block text-pine-700">筑梦无垠宇宙</span>
              </h1>
              
              <p className="text-xl text-pine-700 leading-relaxed font-light max-w-xl mb-10">
                这里是 <span className="font-semibold text-pine-700">万象谱 Loreweft</span>。选择一张现成的残卷继续你的创作，或者展开一张全新的白纸，让 AI 与你一同构建百万字级的宏大史诗。
              </p>

              <button 
                onClick={() => setShowCreate(true)}
                className="lobby-create-button group relative overflow-hidden rounded-full bg-pine-600 px-8 py-4 text-white shadow-lg transition-all hover:bg-pine-700 hover:shadow-pine-500/30 hover:-translate-y-1"
              >
                <div className="absolute inset-0 w-full h-full bg-gradient-to-r from-transparent via-white/20 to-transparent -translate-x-full group-hover:animate-[slideRight_1.5s_infinite]" />
                <div className="flex items-center gap-2 text-lg font-medium tracking-wide">
                  <Plus className="h-5 w-5" />
                  新建宇宙
                </div>
              </button>
            </motion.div>
          </div>

          {/* Loreweft Large Logo Illustration */}
          <motion.div 
            initial={{ opacity: 0, scale: 0.9, rotate: -5 }} 
            animate={{ opacity: 1, scale: 1, rotate: 0 }} 
            transition={{ duration: 1, ease: "easeOut", delay: 0.2 }}
            className="flex-1 flex justify-center lg:justify-end shrink-0"
            style={{ perspective: 1000 }}
          >
            <motion.div 
              className="relative w-80 h-80 md:w-96 md:h-96 cursor-pointer"
              whileHover="hover"
              initial="rest"
              animate="rest"
              style={{ rotateX, rotateY, transformStyle: "preserve-3d" }}
              onMouseMove={handleMouseMove}
              onMouseLeave={handleMouseLeave}
            >
              {/* Floating Decorative Tags */}
              <motion.div
                initial={{ y: -10 }}
                animate={{ y: resolvedTheme === "night" ? 6 : 10 }}
                style={{ zIndex: 30, transform: "translateZ(50px)" }}
                transition={{ duration: 3, repeat: Infinity, repeatType: "mirror", ease: "easeInOut" }}
                className="lobby-floating-tag absolute top-[10%] -left-[15%] hidden md:flex items-center gap-2 px-4 py-2 rounded-full bg-white/60 border border-white/80 backdrop-blur-xl shadow-xl pointer-events-none"
              >
                <div className="lobby-status-star w-2 h-2 rounded-full bg-pine-500 shadow-[0_0_8px_#549688] animate-pulse" />
                <span className="text-xs font-bold text-pine-800 tracking-wider">Loreweft Engine</span>
              </motion.div>

              <motion.div
                initial={{ y: 10 }}
                animate={{ y: resolvedTheme === "night" ? -6 : -10 }}
                style={{ zIndex: 30, transform: "translateZ(30px)" }}
                transition={{ duration: 4, repeat: Infinity, repeatType: "mirror", ease: "easeInOut", delay: 0.5 }}
                className="lobby-floating-tag absolute bottom-[15%] -left-[10%] hidden md:flex items-center gap-3 px-4 py-2.5 rounded-2xl bg-white/40 border border-white/60 backdrop-blur-xl shadow-xl pointer-events-none"
              >
                <div className="lobby-capacity-icon p-1.5 rounded-lg bg-tea-200/50 text-pine-700">
                  <BookOpen className="w-4 h-4" />
                </div>
                <div className="flex flex-col">
                  <span className="text-[10px] text-pine-700 font-bold uppercase tracking-widest">Capacity</span>
                  <span className="text-sm font-black text-pine-700">1,000,000+ Words</span>
                </div>
              </motion.div>

              <motion.div
                initial={{ y: -8, rotate: -5 }}
                animate={resolvedTheme === "night" ? { y: 5, rotate: 3 } : { y: 8, rotate: 5 }}
                style={{ zIndex: 30, transform: "translateZ(40px)" }}
                transition={{ duration: 3.5, repeat: Infinity, repeatType: "mirror", ease: "easeInOut", delay: 1 }}
                className="lobby-sparkle-chip absolute top-[40%] -right-[8%] hidden md:flex items-center justify-center w-14 h-14 rounded-full bg-tea-100/60 border border-tea-200/80 backdrop-blur-xl shadow-xl pointer-events-none"
              >
                <Sparkles className="w-6 h-6 text-pine-600" />
              </motion.div>

              {/* Outer rotating ring */}
              <motion.div 
                animate={{ rotate: 360 }}
                transition={{ duration: 40, repeat: Infinity, ease: "linear" }}
                className="lobby-outer-ring absolute inset-0 rounded-full border border-dashed border-pine-400/40"
              />
              {/* Inner counter-rotating ring */}
              <motion.div 
                animate={{ rotate: -360 }}
                transition={{ duration: 30, repeat: Infinity, ease: "linear" }}
                className="lobby-inner-ring absolute inset-4 rounded-full border border-pine-500/20"
              />
              {/* Core shape */}
              <motion.div 
                variants={{
                  rest: { scale: 1 },
                  hover: { scale: resolvedTheme === "night" ? 1.015 : 1.05 }
                }}
                transition={{ type: "spring", stiffness: 300, damping: 20 }}
                className="lobby-compass-core absolute inset-6 rounded-full bg-white/30 shadow-[0_0_100px_rgba(84,150,136,0.15)] flex items-center justify-center border border-white/50 backdrop-blur-2xl"
              >
                {resolvedTheme === "night" ? (
                  <NightLoomDial />
                ) : (
                <svg width="160" height="160" viewBox="0 0 200 200" fill="none" xmlns="http://www.w3.org/2000/svg">
                  <motion.path 
                    variants={{
                      hover: { scale: 1.02, transition: { repeat: Infinity, repeatType: "mirror", duration: 1, ease: "easeInOut" } }
                    }}
                    style={{ originX: '100px', originY: '100px' }}
                    d="M100 20C55.8172 20 20 55.8172 20 100C20 144.183 55.8172 180 100 180C144.183 180 180 144.183 180 100" stroke="#549688" strokeWidth="4" strokeLinecap="round"/>
                  <path d="M100 20C144.183 20 180 55.8172 180 100" stroke="#d0a44b" strokeWidth="4" strokeLinecap="round" strokeDasharray="8 8"/>
                  
                  {/* Loom threads */}
                  <motion.path 
                    variants={{
                      hover: { opacity: 1, strokeWidth: 2.5, transition: { duration: 1.5, repeat: Infinity, repeatType: "mirror", ease: "easeInOut" } }
                    }}
                    d="M45 60 L155 140 M45 140 L155 60 M60 45 L140 155 M140 45 L60 155 M100 30 L100 170 M30 100 L170 100" stroke="#7eb0a5" strokeWidth="1.5" className="opacity-60" />
                  
                  {/* Quill / Pen shape intersecting the dreamcatcher */}
                  <motion.g
                    variants={{
                      rest: { rotate: 0, y: 0 },
                      hover: { rotate: 5, y: -3, transition: { duration: 1.5, repeat: Infinity, repeatType: "mirror", ease: "easeInOut" } }
                    }}
                    style={{ originX: '90px', originY: '110px' }}
                  >
                    <path d="M130 50 C120 70 110 90 90 110 C70 130 50 140 50 140 L45 135 C45 135 60 110 80 90 C100 70 120 60 135 55 Z" fill="url(#paint0_linear)"/>
                    <path d="M50 140 L40 160 L60 150 Z" fill="#549688"/>
                  </motion.g>
                  
                  <motion.circle 
                    variants={{ hover: { scale: 1.2, fill: "#dfc172", transition: { repeat: Infinity, repeatType: "mirror", duration: 1, ease: "easeInOut" } } }}
                    cx="100" cy="100" r="15" fill="#F4EAC5" style={{ originX: '100px', originY: '100px' }} />
                  <circle cx="100" cy="100" r="8" fill="#549688" />
                  <motion.circle 
                    variants={{ hover: { scale: 1.5, opacity: 0.5, transition: { repeat: Infinity, repeatType: "mirror", duration: 1, ease: "easeInOut" } } }}
                    cx="145" cy="55" r="4" fill="#d0a44b" style={{ originX: '145px', originY: '55px' }} />
                  <motion.circle 
                    variants={{ hover: { scale: 1.5, opacity: 0.5, transition: { repeat: Infinity, repeatType: "mirror", duration: 1, ease: "easeInOut", delay: 0.5 } } }}
                    cx="45" cy="155" r="3" fill="#7eb0a5" style={{ originX: '45px', originY: '155px' }} />

                  <defs>
                    <linearGradient id="paint0_linear" x1="130" y1="50" x2="50" y2="140" gradientUnits="userSpaceOnUse">
                      <stop stopColor="#7eb0a5"/>
                      <stop offset="1" stopColor="#d0a44b"/>
                    </linearGradient>
                  </defs>
                </svg>
                )}
              </motion.div>
            </motion.div>
          </motion.div>
        </div>

        {loading && projects.length === 0 ? (
          <div className="flex h-64 items-center justify-center">
            <motion.div animate={{ rotate: 360 }} transition={{ repeat: Infinity, duration: 1, ease: "linear" }}>
              <div className="h-10 w-10 rounded-full border-2 border-tea-200 border-t-pine-500" />
            </motion.div>
          </div>
        ) : projects.length === 0 ? (
          <FadeInWrapper delay={0.2} className="flex h-96 flex-col items-center justify-center text-pine-700 bg-white/60 backdrop-blur-md rounded-[2rem] border border-tea-200 shadow-sm relative overflow-hidden">
            <div className="absolute inset-0 bg-gradient-to-br from-white/40 to-transparent pointer-events-none" />
            <div className="h-24 w-24 rounded-full bg-tea-100 flex items-center justify-center mb-6 shadow-inner relative z-10">
              <BookMarked className="h-12 w-12 text-pine-400" strokeWidth={1.5} />
            </div>
            <h3 className="text-2xl font-bold text-pine-700 mb-2 relative z-10">卷轴尚空</h3>
            <p className="text-pine-700 max-w-sm text-center mb-8 relative z-10">
              未有笔墨落于此间。点击新建按钮，开始织就你的第一部作品。
            </p>
            <button 
              onClick={() => setShowCreate(true)}
              className="relative z-10 rounded-full bg-white border border-pine-200 px-6 py-2.5 text-pine-700 font-medium hover:bg-pine-50 hover:border-pine-300 transition-colors shadow-sm"
            >
              起笔
            </button>
          </FadeInWrapper>
        ) : (
          <motion.div 
            variants={containerVariants}
            initial="hidden"
            animate="show"
            className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
          >
            {projects.map((project) => (
              <motion.div key={project.id} variants={itemVariants} className="h-full">
                <AnimatedCard 
                  glowColor="none"
                  className="lobby-project-card group relative cursor-pointer flex flex-col h-full bg-white/40 backdrop-blur-xl border border-white/60 hover:border-pine-300/60 hover:bg-white/60 hover:shadow-2xl hover:shadow-pine-500/20 rounded-[2rem] transition-all duration-500 overflow-hidden"
                  onClick={() => navigate(`/project/${project.id}`)}
                >
                  {/* Subtle top gradient bar */}
                  <div className="absolute top-0 left-0 right-0 h-1 bg-gradient-to-r from-pine-300 to-tea-300 opacity-0 group-hover:opacity-100 transition-opacity duration-300" />
                  
                  <div className="absolute right-4 top-4 opacity-0 transition-opacity duration-200 group-hover:opacity-100 z-10">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setDeleteTarget({ id: project.id, name: project.name });
                      }}
                      className="rounded-full p-2 text-pine-700 hover:bg-red-50 hover:text-red-600 transition-colors backdrop-blur-sm bg-white/50"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                  
                  <div className="flex-1 p-6 pb-4">
                    <div className="lobby-project-mark mb-5 flex h-12 w-12 items-center justify-center rounded-lg">
                      <ProjectCodexIcon />
                    </div>
                    <h3 className="text-xl font-bold text-pine-700 pr-8 line-clamp-1 mb-2 group-hover:text-pine-800 transition-colors">{project.name}</h3>
                    {project.genre && (
                      <span className="inline-block rounded-md bg-tea-50 px-2.5 py-1 text-xs font-semibold text-tea-700 border border-tea-200 group-hover:bg-pine-50 group-hover:text-pine-700 group-hover:border-pine-200 transition-colors">
                        {project.genre}
                      </span>
                    )}
                    {project.description ? (
                      <p className="mt-4 line-clamp-3 text-sm text-pine-700 leading-relaxed font-light">
                        {project.description}
                      </p>
                    ) : (
                      <p className="mt-4 text-sm text-pine-700 italic font-light">暂无故事背景描述</p>
                    )}
                  </div>
                  
                  <div className="mt-auto px-6 py-4 bg-white/50 border-t border-tea-100 flex items-center justify-between group-hover:bg-pine-50/30 transition-colors">
                    <div className="flex gap-4 text-[11px] font-medium text-pine-700 uppercase tracking-wider">
                      <span className="flex items-center gap-1.5" title="总字数">
                        <FileText className="h-3 w-3 text-pine-500/70" />
                        {formatWords(project.total_words)}字
                      </span>
                      <span className="flex items-center gap-1.5" title="最后更新">
                        <Clock className="h-3 w-3 text-tea-600/70" />
                        {formatTime(project.updated_at)}
                      </span>
                    </div>
                    <div className="h-8 w-8 rounded-full bg-white border border-pine-200 flex items-center justify-center text-pine-700 group-hover:bg-pine-500 group-hover:border-pine-500 group-hover:text-white transition-all shadow-sm group-hover:shadow-md group-hover:-translate-y-0.5">
                       <ArrowRight className="h-3.5 w-3.5" />
                    </div>
                  </div>
                </AnimatedCard>
              </motion.div>
            ))}
          </motion.div>
        )}

        <ProjectCreateDialog
          isOpen={showCreate}
          onClose={() => setShowCreate(false)}
          onCreate={createProject}
          onCreated={(project) => navigate(`/project/${project.id}?outline=master`)}
        />

        {toast && (
          <Toast type={toast.type} message={toast.message} onClose={() => setToast(null)} />
        )}

        {deleteTarget && (
          <Modal
            title="焚毁卷轴"
            onClose={() => setDeleteTarget(null)}
            footer={
              <>
                <GlassButton variant="ghost" onClick={() => setDeleteTarget(null)}>手下留情</GlassButton>
                <GlassButton variant="danger" onClick={handleDelete} disabled={deleting}>
                  {deleting ? "执行中..." : "确认焚毁"}
                </GlassButton>
              </>
            }
          >
            <div className="py-2">
              <p className="text-sm text-pine-700 leading-relaxed">
                确定要焚毁宇宙 <span className="font-bold text-pine-700">「{deleteTarget.name}」</span> 吗？
                <br/><br/>
                <span className="text-red-500 font-medium">警告：此操作不可逆。所有的历史、人物和设定将化为灰烬。</span>
              </p>
            </div>
          </Modal>
        )}
      </FadeInWrapper>
      
      {/* Custom keyframes for button animation */}
      <style>{`
        @keyframes slideRight {
          0% { transform: translateX(-100%) skewX(-15deg); }
          100% { transform: translateX(200%) skewX(-15deg); }
        }
      `}</style>
    </div>
  );
}
