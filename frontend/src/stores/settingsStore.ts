import { create } from "zustand";
import type { AppSettings, AgentConfigItem, AgentOverride } from "@/types";
import * as api from "@/api/client";

interface SettingsStore {
  settings: AppSettings;
  agentConfigs: AgentConfigItem[];
  loading: boolean;
  error: string | null;
  fetchSettings: () => Promise<void>;
  updateSettings: (settings: AppSettings) => Promise<void>;
  fetchAgentConfigs: () => Promise<void>;
  updateAgentConfig: (agentName: string, override: AgentOverride) => Promise<void>;
  testConnection: (config: {
    api_format: string;
    api_key: string;
    base_url: string;
    model: string;
  }) => Promise<{ success: boolean; message: string }>;
}

const defaultSettings: AppSettings = {
  global: {
    openai_compatible: null,
    anthropic_compatible: null,
  },
  agent_overrides: {},
};

export const useSettingsStore = create<SettingsStore>((set) => ({
  settings: defaultSettings,
  agentConfigs: [],
  loading: false,
  error: null,

  fetchSettings: async () => {
    set({ loading: true, error: null });
    try {
      const settings = await api.getSettings();
      set({ settings, loading: false });
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
    }
  },

  updateSettings: async (settings) => {
    set({ loading: true, error: null });
    try {
      const updated = await api.updateSettings(settings);
      set({ settings: updated, loading: false });
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
      throw e;
    }
  },

  fetchAgentConfigs: async () => {
    set({ loading: true, error: null });
    try {
      const agentConfigs = await api.getAgentConfigs();
      set({ agentConfigs, loading: false });
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
    }
  },

  updateAgentConfig: async (agentName, override) => {
    set({ loading: true, error: null });
    try {
      await api.updateAgentConfig(agentName, override);
      const agentConfigs = await api.getAgentConfigs();
      set({ agentConfigs, loading: false });
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
      throw e;
    }
  },

  testConnection: async (config) => {
    try {
      const result = await api.testConnection(config);
      return { success: result.success, message: result.message };
    } catch (e) {
      return { success: false, message: (e as Error).message };
    }
  },
}));
