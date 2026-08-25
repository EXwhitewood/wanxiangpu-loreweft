/**
 * 娱乐弹窗 (Entertainment Modal)
 * ===============================
 *
 * 等待娱乐功能的主弹窗。包含两个 Tab：
 * 1. 跳转平台：分组展示视频/音乐/小说/自定义链接，点击调用 openExternalUrl
 * 2. 小游戏：贪吃蛇 / 2048，可在弹窗内直接游玩
 *
 * 设计要点
 * --------
 * - 复用通用 Modal 组件，传入 maxWidth="max-w-2xl" 拓宽
 * - Tab 切换使用底部下划线样式，与项目现有 tab 风格一致
 * - 跳转卡片用网格布局，悬浮微动画（whileHover scale 1.02）
 * - 工作流完成触发时显示"返回工作台"快捷按钮（footer）
 * - 设置为关闭时清空当前选中的游戏，避免下次打开仍停留在游戏页
 */

import { useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { ExternalLink, Gamepad2, Sparkles, ArrowLeft } from "lucide-react";
import Modal from "@/components/Common/Modal";
import { GlassButton } from "@/components/UI/GlassButton";
import { useEntertainmentStore } from "@/stores/entertainmentStore";
import { openExternalUrl } from "@/utils/platform";
import type { EntertainmentLink, LinkCategory } from "@/types";
import SnakeGame from "./games/SnakeGame";
import Game2048 from "./games/Game2048";

// 链接分类元信息（显示名 + emoji + 颜色）
const CATEGORY_META: Record<LinkCategory, { label: string; emoji: string; accent: string }> = {
  video: { label: "视频平台", emoji: "📺", accent: "text-magic-700" },
  music: { label: "音乐平台", emoji: "🎵", accent: "text-magic-700" },
  novel: { label: "小说平台", emoji: "📚", accent: "text-magic-700" },
  custom: { label: "自定义", emoji: "✨", accent: "text-amber-600" },
};

// 游戏元信息
const GAMES: Record<string, { label: string; emoji: string; description: string }> = {
  snake: { label: "贪吃蛇", emoji: "🐍", description: "经典红白机贪吃蛇" },
  "2048": { label: "2048", emoji: "🔢", description: "合并数字达到 2048" },
};

type Tab = "links" | "games";

export default function EntertainmentModal() {
  const { isModalOpen, closeModal, settings, triggerSource } = useEntertainmentStore();
  const [activeTab, setActiveTab] = useState<Tab>("links");
  const [activeGame, setActiveGame] = useState<string | null>(null);

  // 弹窗关闭时重置内部状态
  useEffect(() => {
    if (!isModalOpen) {
      const timer = setTimeout(() => {
        setActiveTab("links");
        setActiveGame(null);
      }, 200);  // 等待退出动画完成
      return () => clearTimeout(timer);
    }
  }, [isModalOpen]);

  // 阻止键盘事件穿透到游戏外（避免影响父组件快捷键）
  // 这里仅做被动监听，让游戏内部自行处理键盘
  if (!settings) return null;

  // 按分类分组链接
  const groupedLinks: Record<LinkCategory, EntertainmentLink[]> = {
    video: [],
    music: [],
    novel: [],
    custom: [],
  };
  settings.links.forEach((link) => {
    if (link.enabled) {
      groupedLinks[link.category].push(link);
    }
  });

  // 是否有任何链接或游戏可显示
  const hasLinks = settings.links.some((l) => l.enabled);
  const hasGames = settings.enabled_games.length > 0;

  // 处理跳转点击
  const handleLinkClick = async (link: EntertainmentLink) => {
    const success = await openExternalUrl(link.url);
    if (!success) {
      console.warn(`[EntertainmentModal] 打开链接失败: ${link.url}`);
    }
  };

  return (
    <Modal
      title="等待娱乐"
      isOpen={isModalOpen}
      onClose={closeModal}
      maxWidth="max-w-2xl"
      bodyMaxHeight="max-h-[75vh]"
      footer={
        triggerSource === "workflow_complete" ? (
          <GlassButton variant="ghost" onClick={closeModal}>
            <ArrowLeft className="mr-1.5 h-4 w-4" />
            返回工作台
          </GlassButton>
        ) : undefined
      }
    >
      {/* 顶部欢迎语 */}
      <div className="mb-4 flex items-center gap-2 rounded-xl border border-pine-200/60 bg-pine-50/60 px-4 py-3">
        <Sparkles className="h-4 w-4 text-amber-500" />
        <p className="text-xs text-pine-700">
          {triggerSource === "workflow_complete"
            ? "🎉 工作流已完成！趁着切换回工作台的间隙放松一下"
            : "工作流进行中，趁机放松一下，劳逸结合更高效"}
        </p>
      </div>

      {/* Tab 切换 */}
      <div className="mb-5 flex gap-1 border-b border-pine-200">
        <button
          onClick={() => setActiveTab("links")}
          className={`flex items-center gap-1.5 px-4 py-2 text-sm font-medium transition-colors ${
            activeTab === "links"
              ? "border-b-2 border-magic-500 text-magic-700"
              : "text-pine-700 hover:text-pine-900"
          }`}
        >
          <ExternalLink className="h-3.5 w-3.5" />
          跳转平台
        </button>
        <button
          onClick={() => setActiveTab("games")}
          className={`flex items-center gap-1.5 px-4 py-2 text-sm font-medium transition-colors ${
            activeTab === "games"
              ? "border-b-2 border-magic-500 text-magic-700"
              : "text-pine-700 hover:text-pine-900"
          }`}
        >
          <Gamepad2 className="h-3.5 w-3.5" />
          小游戏
        </button>
      </div>

      <AnimatePresence mode="wait">
        {/* === Tab 1: 跳转平台 === */}
        {activeTab === "links" && (
          <motion.div
            key="links"
            initial={{ opacity: 0, y: 5 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -5 }}
            transition={{ duration: 0.15 }}
          >
            {hasLinks ? (
              <div className="space-y-5">
                {(Object.keys(groupedLinks) as LinkCategory[]).map((category) => {
                  const links = groupedLinks[category];
                  if (links.length === 0) return null;
                  const meta = CATEGORY_META[category];
                  return (
                    <div key={category}>
                      <div className="mb-2 flex items-center gap-2">
                        <span className="text-sm">{meta.emoji}</span>
                        <h3 className={`text-xs font-semibold ${meta.accent}`}>{meta.label}</h3>
                        <span className="text-[10px] text-pine-700/60">({links.length})</span>
                      </div>
                      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                        {links.map((link, idx) => (
                          <motion.button
                            key={`${link.name}-${idx}`}
                            onClick={() => handleLinkClick(link)}
                            whileHover={{ scale: 1.02 }}
                            whileTap={{ scale: 0.98 }}
                            className="group flex items-center gap-2 rounded-xl border border-pine-200/70 bg-white px-3 py-2.5 text-left transition-colors hover:border-magic-400 hover:bg-magic-50/40"
                          >
                            <span className="text-base">{link.icon || "🔗"}</span>
                            <div className="min-w-0 flex-1">
                              <p className="truncate text-xs font-medium text-pine-800">{link.name}</p>
                              <p className="truncate text-[10px] text-pine-700/60">
                                {link.url.replace(/^https?:\/\//, "").split("/")[0]}
                              </p>
                            </div>
                            <ExternalLink className="h-3 w-3 text-pine-700/40 transition-colors group-hover:text-magic-500" />
                          </motion.button>
                        ))}
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center py-10 text-center">
                <p className="text-sm text-pine-700">暂无可用链接</p>
                <p className="mt-1 text-xs text-pine-700/60">
                  请到「设置 → 等待娱乐」中添加自定义链接
                </p>
              </div>
            )}
          </motion.div>
        )}

        {/* === Tab 2: 小游戏 === */}
        {activeTab === "games" && (
          <motion.div
            key="games"
            initial={{ opacity: 0, y: 5 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -5 }}
            transition={{ duration: 0.15 }}
          >
            {hasGames ? (
              activeGame ? (
                // 游戏进行中：显示返回按钮 + 游戏组件
                <div>
                  <button
                    onClick={() => setActiveGame(null)}
                    className="mb-4 flex items-center gap-1 text-xs font-medium text-pine-700 transition-colors hover:text-magic-700"
                  >
                    <ArrowLeft className="h-3.5 w-3.5" />
                    返回游戏列表
                  </button>
                  <div className="flex justify-center">
                    {activeGame === "snake" && <SnakeGame />}
                    {activeGame === "2048" && <Game2048 />}
                  </div>
                </div>
              ) : (
                // 游戏列表
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  {settings.enabled_games.map((gameId) => {
                    const meta = GAMES[gameId];
                    if (!meta) return null;
                    return (
                      <motion.button
                        key={gameId}
                        onClick={() => setActiveGame(gameId)}
                        whileHover={{ scale: 1.02 }}
                        whileTap={{ scale: 0.98 }}
                        className="flex items-center gap-3 rounded-xl border border-pine-200/70 bg-white px-4 py-4 text-left transition-colors hover:border-magic-400 hover:bg-magic-50/40"
                      >
                        <span className="text-2xl">{meta.emoji}</span>
                        <div>
                          <p className="text-sm font-semibold text-pine-800">{meta.label}</p>
                          <p className="text-[11px] text-pine-700/60">{meta.description}</p>
                        </div>
                      </motion.button>
                    );
                  })}
                </div>
              )
            ) : (
              <div className="flex flex-col items-center justify-center py-10 text-center">
                <p className="text-sm text-pine-700">暂无可用游戏</p>
                <p className="mt-1 text-xs text-pine-700/60">
                  请到「设置 → 等待娱乐」中启用游戏
                </p>
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </Modal>
  );
}
