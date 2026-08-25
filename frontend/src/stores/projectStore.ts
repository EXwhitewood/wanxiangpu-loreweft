import { create } from "zustand";
import type { Project, ProjectCreate, ChapterListItem, Chapter, StoryState } from "@/types";
import * as api from "@/api/client";

interface ProjectStore {
  projects: Project[];
  currentProject: Project | null;
  chapters: ChapterListItem[];
  currentChapter: Chapter | null;
  storyState: StoryState | null;
  loading: boolean;
  error: string | null;
  outlineDesignerOpen: boolean;
  outlineView: "master" | number | null;
  leftPanelVisible: boolean;
  rightPanelVisible: boolean;
  fetchProjects: () => Promise<void>;
  createProject: (data: ProjectCreate) => Promise<Project>;
  selectProject: (id: string) => Promise<void>;
  fetchChapters: (projectId: string) => Promise<void>;
  fetchChapter: (projectId: string, chapterNumber: number) => Promise<void>;
  fetchState: (projectId: string) => Promise<void>;
  refreshProject: (projectId: string) => Promise<void>;
  setCurrentChapter: (chapter: Chapter | null) => void;
  clearCurrentProject: () => void;
  setOutlineDesignerOpen: (open: boolean) => void;
  setOutlineView: (view: "master" | number | null) => void;
  setLeftPanelVisible: (visible: boolean) => void;
  setRightPanelVisible: (visible: boolean) => void;
  toggleLeftPanel: () => void;
  toggleRightPanel: () => void;
}

export const useProjectStore = create<ProjectStore>((set) => ({
  projects: [],
  currentProject: null,
  chapters: [],
  currentChapter: null,
  storyState: null,
  loading: false,
  error: null,
  outlineDesignerOpen: false,
  outlineView: null,
  leftPanelVisible: true,
  rightPanelVisible: true,

  fetchProjects: async () => {
    set({ loading: true, error: null });
    try {
      const projects = await api.fetchProjects();
      set({ projects, loading: false });
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
    }
  },

  createProject: async (data) => {
    set({ loading: true, error: null });
    try {
      const project = await api.createProject(data);
      set((state) => ({
        projects: [project, ...state.projects],
        loading: false,
      }));
      return project;
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
      throw e;
    }
  },

  selectProject: async (id) => {
    set({ loading: true, error: null });
    try {
      const project = await api.getProject(id);
      set({ currentProject: project, loading: false });
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
    }
  },

  fetchChapters: async (projectId) => {
    set({ loading: true, error: null });
    try {
      const chapters = await api.getChapters(projectId);
      set({ chapters, loading: false });
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
    }
  },

  fetchChapter: async (projectId, chapterNumber) => {
    set({ loading: true, error: null });
    try {
      const chapter = await api.getChapter(projectId, chapterNumber);
      set({ currentChapter: chapter, loading: false });
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
    }
  },

  fetchState: async (projectId) => {
    set({ loading: true, error: null });
    try {
      const storyState = await api.getState(projectId);
      set({ storyState, loading: false });
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
    }
  },

  refreshProject: async (projectId) => {
    try {
      const project = await api.getProject(projectId);
      set({ currentProject: project });
    } catch {
    }
  },

  setCurrentChapter: (chapter) => {
    set({ currentChapter: chapter });
  },

  clearCurrentProject: () => {
    set({
      currentProject: null,
      chapters: [],
      currentChapter: null,
      storyState: null,
    });
  },

  setOutlineDesignerOpen: (open) => {
    set({ outlineDesignerOpen: open });
  },

  setOutlineView: (view) => {
    set({ outlineView: view });
  },

  setLeftPanelVisible: (visible) => {
    set({ leftPanelVisible: visible });
  },

  setRightPanelVisible: (visible) => {
    set({ rightPanelVisible: visible });
  },

  toggleLeftPanel: () => {
    set((s) => ({ leftPanelVisible: !s.leftPanelVisible }));
  },

  toggleRightPanel: () => {
    set((s) => ({ rightPanelVisible: !s.rightPanelVisible }));
  },
}));
