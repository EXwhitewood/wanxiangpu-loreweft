/**
 * 娱乐设置 Store (Entertainment Settings Store)
 * =============================================
 *
 * 集中管理"等待娱乐"功能的状态：包括设置数据 + 弹窗开闭状态。
 *
 * 设计原则
 * --------
 * - 设置数据：通过 API 持久化到后端 data/entertainment_settings.json
 * - 弹窗状态：纯前端运行时状态，不持久化（页面刷新即关闭）
 * - 单例 store：全局唯一，所有组件共享同一份状态
 */

import { create } from "zustand";
import type { EntertainmentSettings } from "@/types";
import * as api from "@/api/client";

interface EntertainmentStore {
  // === 设置数据 ===
  settings: EntertainmentSettings | null;        // null 表示尚未加载
  loading: boolean;
  error: string | null;

  // === 弹窗运行时状态 ===
  isModalOpen: boolean;                           // 弹窗是否打开
  triggerSource: "workflow_wait" | "workflow_complete" | null;  // 触发来源（用于分析/日志）
  workflowJustCompleted: boolean;                 // 工作流刚完成标记（仅用于弹窗内显示"返回工作台"提示）

  // === Actions ===
  fetchSettings: () => Promise<void>;
  saveSettings: (settings: EntertainmentSettings) => Promise<void>;
  resetSettings: () => Promise<void>;
  openModal: (source?: "workflow_wait" | "workflow_complete") => void;
  closeModal: () => void;
  /** 工作流完成时调用：若弹窗已打开，则更新提示为"工作流已完成"，不主动弹窗（弱提醒） */
  notifyWorkflowComplete: () => void;
}

// 默认设置（首次加载前用作 fallback，避免 null 检查散落）
const fallbackSettings: EntertainmentSettings = {
  enabled: true,
  show_trigger_button: true,
  auto_open_on_workflow_wait: false,
  notify_on_workflow_complete: true,
  links: [],
  enabled_games: ["snake", "2048"],
};

export const useEntertainmentStore = create<EntertainmentStore>((set, get) => ({
  settings: null,
  loading: false,
  error: null,

  isModalOpen: false,
  triggerSource: null,
  workflowJustCompleted: false,

  fetchSettings: async () => {
    set({ loading: true, error: null });
    try {
      const settings = await api.getEntertainmentSettings();
      set({ settings, loading: false });
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
    }
  },

  saveSettings: async (settings) => {
    set({ loading: true, error: null });
    try {
      const updated = await api.updateEntertainmentSettings(settings);
      set({ settings: updated, loading: false });
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
      throw e;
    }
  },

  resetSettings: async () => {
    set({ loading: true, error: null });
    try {
      const reset = await api.resetEntertainmentSettings();
      set({ settings: reset, loading: false });
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
      throw e;
    }
  },

  openModal: (source = "workflow_wait") => {
    const current = get().settings ?? fallbackSettings;
    if (!current.enabled) return;
    set({ isModalOpen: true, triggerSource: source });
  },

  closeModal: () => {
    set({ isModalOpen: false, triggerSource: null, workflowJustCompleted: false });
  },

  notifyWorkflowComplete: () => {
    const { isModalOpen } = get();
    // 弱提醒策略：仅当弹窗已打开时更新提示，不主动弹窗打扰用户
    if (isModalOpen) {
      set({ triggerSource: "workflow_complete", workflowJustCompleted: true });
    }
  },
}));
