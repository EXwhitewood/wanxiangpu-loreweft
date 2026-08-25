import { motion, type Variants } from "framer-motion";
import { useNavigate, useParams } from "react-router-dom";
import { useEffect, useState } from "react";
import { 
  PenTool, 
  Globe, 
  BookOpen, 
  BarChart3, 
  Clock, 
  ChevronRight,
  Sparkles,
  LayoutGrid
} from "lucide-react";
import { useProjectStore } from "@/stores/projectStore";
import * as api from "@/api/client";

const DEFAULT_DAILY_INSPIRATION =
  "试着让本章的阻力来自一个看似合理的选择：当主角做出决定时，谁会因此失去最重要的东西？";

function getLocalDateKey() {
  const now = new Date();
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

export default function ProjectDashboard() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { currentProject, chapters } = useProjectStore();
  const [dailyInspiration, setDailyInspiration] = useState(DEFAULT_DAILY_INSPIRATION);
  const [inspirationPending, setInspirationPending] = useState(false);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    const dateKey = getLocalDateKey();
    const storageKey = `loreweft:daily-inspiration:${id}:${dateKey}`;
    try {
      const cached = window.localStorage.getItem(storageKey);
      if (cached) {
        setDailyInspiration(cached);
        setInspirationPending(false);
        return;
      }
    } catch {
      // Continue with the API when storage is unavailable in an embedded view.
    }
    setInspirationPending(true);
    api.getDailyInspiration(id, dateKey)
      .then((result) => {
        if (!cancelled && result.content) {
          setDailyInspiration(result.content);
          if (result.source === "llm") {
            try {
              window.localStorage.setItem(storageKey, result.content);
            } catch {
              // Ignore storage quota and privacy-mode failures.
            }
          }
        }
      })
      .catch(() => {
        // Keep the local copy visible when the provider is unavailable.
      })
      .finally(() => {
        if (!cancelled) setInspirationPending(false);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  if (!currentProject) return null;

  const totalWords = currentProject.total_words || 0;
  const wordTarget = currentProject.word_count_target || 100000;
  const progress = Math.min(Math.round((totalWords / wordTarget) * 100), 100);

  const latestChapter = chapters.length > 0 
    ? chapters[chapters.length - 1] 
    : null;

  const containerVariants: Variants = {
    hidden: { opacity: 0 },
    show: {
      opacity: 1,
      transition: {
        staggerChildren: 0.1
      }
    }
  };

  const itemVariants: Variants = {
    hidden: { opacity: 0, y: 20 },
    show: { opacity: 1, y: 0, transition: { type: "spring", stiffness: 300, damping: 24 } }
  };

  return (
    <div className="flex-1 h-full overflow-y-auto overflow-x-hidden scrollbar-none relative">
      <div className="max-w-5xl mx-auto px-10 py-16 flex flex-col gap-10">
        
        {/* Hero Section */}
        <motion.div 
          initial={{ opacity: 0, y: 30 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
          className="flex flex-col gap-4"
        >
          <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-pine-500/10 text-pine-700 text-xs font-semibold uppercase tracking-widest w-fit border border-pine-500/20">
            <LayoutGrid className="w-3.5 h-3.5" />
            Project Dashboard
          </div>
          <h1 className="text-5xl font-extrabold text-pine-950 tracking-tight">
            {currentProject.name}
          </h1>
          {currentProject.description && (
            <p className="text-lg text-pine-700/80 max-w-2xl leading-relaxed mt-2">
              {currentProject.description}
            </p>
          )}

          {/* Quick Stats */}
          <div className="flex items-center gap-8 mt-6">
            <div className="flex flex-col gap-1">
              <span className="text-sm font-medium text-pine-600 uppercase tracking-wider">总字数</span>
              <div className="flex items-baseline gap-2">
                <span className="text-3xl font-bold text-pine-900">{totalWords.toLocaleString()}</span>
                <span className="text-sm text-pine-500">/ {wordTarget.toLocaleString()} 字</span>
              </div>
              <div className="w-48 h-1.5 bg-pine-100 rounded-full mt-2 overflow-hidden">
                <div 
                  className="h-full bg-magic-500 rounded-full" 
                  style={{ width: `${progress}%` }}
                />
              </div>
            </div>

            <div className="w-px h-12 bg-pine-200/50" />

            <div className="flex flex-col gap-1">
              <span className="text-sm font-medium text-pine-600 uppercase tracking-wider">当前进度</span>
              <span className="text-3xl font-bold text-pine-900">{chapters.length} <span className="text-base font-normal text-pine-600">章</span></span>
            </div>

            <div className="w-px h-12 bg-pine-200/50" />

            <div className="flex flex-col gap-1">
              <span className="text-sm font-medium text-pine-600 uppercase tracking-wider">上次更新</span>
              <span className="text-lg font-medium text-pine-900 mt-1 flex items-center gap-2">
                <Clock className="w-4 h-4 text-pine-500" />
                {new Date(currentProject.updated_at).toLocaleDateString()}
              </span>
            </div>
          </div>
        </motion.div>

        {/* Bento Grid */}
        <motion.div 
          variants={containerVariants}
          initial="hidden"
          animate="show"
          className="grid grid-cols-3 gap-6 mt-4"
        >
          {/* Main Action Card (Immersive Creation) */}
          <motion.div 
            variants={itemVariants}
            onClick={() => navigate(`/project/${id}/editor`)}
            className="col-span-2 group relative overflow-hidden rounded-3xl bg-white/40 backdrop-blur-xl border border-white/60 shadow-[0_8px_32px_rgba(0,0,0,0.03)] cursor-pointer hover:bg-white/60 hover:shadow-[0_16px_48px_rgba(0,0,0,0.06)] transition-all duration-500 p-8 flex flex-col justify-between min-h-[280px]"
          >
            <div className="absolute top-0 right-0 w-64 h-64 bg-gradient-to-br from-magic-200/40 to-transparent rounded-full blur-3xl -translate-y-1/2 translate-x-1/2 group-hover:scale-110 transition-transform duration-700" />
            
            <div className="relative z-10 flex flex-col items-start gap-4">
              <div className="w-12 h-12 rounded-2xl bg-magic-100 text-magic-600 flex items-center justify-center shadow-inner">
                <PenTool className="w-6 h-6" />
              </div>
              <h2 className="text-2xl font-bold text-pine-900">沉浸创作</h2>
              <p className="text-pine-700 max-w-sm">
                进入无干扰的写作环境。AI 智能体将在后台实时待命，协助你推进剧情与优化文笔。
              </p>
            </div>

            <div className="relative z-10 w-full flex items-center justify-between mt-8 p-4 rounded-2xl bg-white/50 border border-white/50 backdrop-blur-sm group-hover:bg-white/80 transition-colors">
              <div className="flex flex-col">
                <span className="text-xs font-semibold text-pine-500 uppercase tracking-widest mb-1">最近编辑</span>
                <span className="font-medium text-pine-900">
                  {latestChapter ? `第${latestChapter.chapter_number}章：${latestChapter.title}` : "还未创建任何章节"}
                </span>
              </div>
              <div className="flex items-center gap-2 text-magic-600 font-semibold bg-magic-50 px-4 py-2 rounded-xl group-hover:bg-magic-100 transition-colors">
                继续创作
                <ChevronRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
              </div>
            </div>
          </motion.div>

          {/* Worldbuilding Card */}
          <motion.div 
            variants={itemVariants}
            onClick={() => navigate(`/project/${id}/worldbuilding`)}
            className="col-span-1 group relative overflow-hidden rounded-3xl bg-white/40 backdrop-blur-xl border border-white/60 shadow-[0_8px_32px_rgba(0,0,0,0.03)] cursor-pointer hover:bg-white/60 hover:shadow-[0_16px_48px_rgba(0,0,0,0.06)] transition-all duration-500 p-8 flex flex-col justify-between"
          >
            <div className="relative z-10 flex flex-col items-start gap-4">
              <div className="w-12 h-12 rounded-2xl bg-pine-100 text-pine-600 flex items-center justify-center shadow-inner">
                <Globe className="w-6 h-6" />
              </div>
              <div>
                <h2 className="text-xl font-bold text-pine-900">世界法则</h2>
                <p className="text-sm text-pine-600 mt-2">
                  管理角色、地点与设定法则。这是 AI 理解你小说世界的基石。
                </p>
              </div>
            </div>
            <div className="relative z-10 mt-8 w-10 h-10 rounded-full bg-pine-50 text-pine-400 flex items-center justify-center group-hover:bg-pine-600 group-hover:text-white transition-colors self-end">
              <ChevronRight className="w-5 h-5 group-hover:translate-x-0.5 transition-transform" />
            </div>
          </motion.div>

          {/* Outline Card */}
          <motion.div 
            variants={itemVariants}
            onClick={() => navigate(`/project/${id}?outline=master`)}
            className="col-span-1 group relative overflow-hidden rounded-3xl bg-white/40 backdrop-blur-xl border border-white/60 shadow-[0_8px_32px_rgba(0,0,0,0.03)] cursor-pointer hover:bg-white/60 hover:shadow-[0_16px_48px_rgba(0,0,0,0.06)] transition-all duration-500 p-8 flex flex-col justify-between"
          >
            <div className="relative z-10 flex flex-col items-start gap-4">
              <div className="w-12 h-12 rounded-2xl bg-amber-100 text-amber-600 flex items-center justify-center shadow-inner">
                <BookOpen className="w-6 h-6" />
              </div>
              <div>
                <h2 className="text-xl font-bold text-pine-900">结构大纲</h2>
                <p className="text-sm text-pine-600 mt-2">
                  全局视角俯瞰剧情脉络。AI 会协助你检查逻辑漏洞与冲突设定。
                </p>
              </div>
            </div>
            <div className="relative z-10 mt-8 w-10 h-10 rounded-full bg-amber-50 text-amber-500 flex items-center justify-center group-hover:bg-amber-500 group-hover:text-white transition-colors self-end">
              <ChevronRight className="w-5 h-5 group-hover:translate-x-0.5 transition-transform" />
            </div>
          </motion.div>

          {/* Analytics / Intelligence Card */}
          <motion.div 
            variants={itemVariants}
            onClick={() => navigate(`/project/${id}/intelligence`)}
            className="col-span-2 group relative overflow-hidden rounded-3xl bg-gradient-to-br from-pine-900 to-pine-950 border border-pine-800 shadow-[0_8px_32px_rgba(0,0,0,0.2)] cursor-pointer hover:shadow-[0_16px_48px_rgba(0,0,0,0.3)] transition-all duration-500 p-8 flex"
          >
            <div className="absolute top-0 right-0 w-full h-full bg-[url('https://www.transparenttextures.com/patterns/carbon-fibre.png')] opacity-20 pointer-events-none" />
            <div className="absolute top-1/2 right-10 w-48 h-48 bg-magic-500/20 rounded-full blur-[80px] -translate-y-1/2 pointer-events-none group-hover:bg-magic-400/30 transition-colors" />

            <div className="relative z-10 flex-1 flex flex-col justify-between">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-xl bg-white/10 text-pine-100 flex items-center justify-center backdrop-blur-md">
                  <BarChart3 className="w-5 h-5" />
                </div>
                <h2 className="text-xl font-bold text-white tracking-wide">市场情报与诊断</h2>
              </div>
              <div className="mt-6 flex flex-col gap-2">
                <p className="text-pine-300 max-w-sm">
                  基于最新的大模型数据，扫描小说的市场潜力、受众画像与毒点分析。
                </p>
                <div className="flex items-center gap-2 mt-2 text-magic-300 font-medium group-hover:text-magic-200 transition-colors">
                  查看最新评估报告 <ChevronRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
                </div>
              </div>
            </div>
          </motion.div>
        </motion.div>

        {/* AI Inspiration */}
        <motion.div 
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.4 }}
          className="mt-4 p-6 rounded-3xl bg-gradient-to-r from-magic-50/50 to-transparent border border-magic-100 flex items-start gap-4"
        >
          <div className="w-10 h-10 shrink-0 rounded-full bg-magic-100 text-magic-600 flex items-center justify-center">
            <Sparkles className="w-5 h-5" />
          </div>
          <div>
            <h3 className="font-bold text-pine-900 flex items-center gap-2">
              今日 AI 灵感
              <span className="text-[10px] font-bold px-2 py-0.5 rounded bg-magic-200 text-magic-800 uppercase tracking-wider">Beta</span>
            </h3>
            <p className={`text-sm text-pine-700 mt-2 leading-relaxed max-w-3xl transition-opacity duration-300 ${inspirationPending ? "opacity-60" : "opacity-100"}`}>
              {dailyInspiration}
            </p>
          </div>
        </motion.div>
      </div>
    </div>
  );
}
