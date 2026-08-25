import { create } from "zustand";
import * as api from "@/api/client";
import type {
  WorldbuildingOverview,
  WorldRule,
  Character,
  Location,
  PromotionProposal,
  WorldviewObservation,
  WorldviewObservationList,
  WorldviewObservationListParams,
  WorldviewObservationReviewCounts,
} from "@/types";

export const DEFAULT_OBSERVATION_PAGE_SIZE = 50;

const DEFAULT_OBSERVATION_QUERY: Required<Pick<WorldviewObservationListParams, "reviewState" | "page" | "pageSize">> = {
  reviewState: "pending",
  page: 1,
  pageSize: DEFAULT_OBSERVATION_PAGE_SIZE,
};

const EMPTY_OBSERVATION_COUNTS: WorldviewObservationReviewCounts = {
  all: 0,
  pending: 0,
  auto: 0,
  manual: 0,
  confirmed: 0,
};

interface WorldbuildingState {
  projectId: string | null;
  overview: WorldbuildingOverview | null;
  rules: WorldRule[];
  characters: Character[];
  locations: Location[];
  promotions: PromotionProposal[];
  observations: WorldviewObservation[];
  observationCounts: WorldviewObservationReviewCounts;
  observationTotal: number;
  observationPage: number;
  observationPageSize: number;
  observationTotalPages: number;
  observationHasMore: boolean;
  observationReviewState: WorldviewObservationListParams["reviewState"];
  
  loading: boolean;
  promotionsLoading: boolean;
  observationsLoading: boolean;
  promotionsLoaded: boolean;
  observationsLoaded: boolean;
  error: Error | null;
  promotionsError: Error | null;
  observationsError: Error | null;

  fetchData: (projectId: string, force?: boolean) => Promise<void>;
  fetchPromotions: (projectId: string, force?: boolean) => Promise<void>;
  fetchObservations: (
    projectId: string,
    options?: WorldviewObservationListParams,
    force?: boolean,
  ) => Promise<WorldviewObservationList | null>;
  invalidateProjectedData: (projectId: string) => void;
  
  clear: () => void;
}

const dataRequests = new Map<string, Promise<void>>();
const promotionRequests = new Map<string, Promise<void>>();
const observationRequests = new Map<string, Promise<WorldviewObservationList | null>>();
let requestEpoch = 0;
let activeObservationRequestKey: string | null = null;

function observationRequestKey(projectId: string, options: WorldviewObservationListParams) {
  return [
    projectId,
    options.entityType || "",
    options.status || "",
    options.reviewState || "all",
    options.page || 1,
    options.pageSize || "all",
  ].join("|");
}

function fallbackReviewCounts(items: WorldviewObservation[]): WorldviewObservationReviewCounts {
  const counts = items.reduce<WorldviewObservationReviewCounts>((acc, item) => {
    acc.all += 1;
    if (item.status === "active" && !item.confirmed_by) acc.pending += 1;
    if (item.status === "promoted" && item.auto_promoted) acc.auto += 1;
    if (item.status === "promoted" && !item.auto_promoted) acc.manual += 1;
    return acc;
  }, { ...EMPTY_OBSERVATION_COUNTS });
  counts.confirmed = counts.auto + counts.manual;
  return counts;
}

function invalidateRequests() {
  requestEpoch += 1;
  dataRequests.clear();
  promotionRequests.clear();
  observationRequests.clear();
  activeObservationRequestKey = null;
}

export const useWorldbuildingStore = create<WorldbuildingState>((set, get) => ({
  projectId: null,
  overview: null,
  rules: [],
  characters: [],
  locations: [],
  promotions: [],
  observations: [],
  observationCounts: { ...EMPTY_OBSERVATION_COUNTS },
  observationTotal: 0,
  observationPage: 1,
  observationPageSize: DEFAULT_OBSERVATION_PAGE_SIZE,
  observationTotalPages: 1,
  observationHasMore: false,
  observationReviewState: DEFAULT_OBSERVATION_QUERY.reviewState,
  loading: false,
  promotionsLoading: false,
  observationsLoading: false,
  promotionsLoaded: false,
  observationsLoaded: false,
  error: null,
  promotionsError: null,
  observationsError: null,

  fetchData: async (projectId: string, force = false) => {
    const state = get();
    if (state.projectId !== null && state.projectId !== projectId) {
      invalidateRequests();
    }
    if (!force && state.projectId === projectId && state.overview && !state.error) {
      return;
    }

    const existingRequest = dataRequests.get(projectId);
    if (existingRequest) return existingRequest;

    const epoch = requestEpoch;
    const request = (async () => {
      const current = get();
      const projectChanged = current.projectId !== projectId;
      set(projectChanged
        ? {
            projectId,
            overview: null,
            rules: [],
            characters: [],
            locations: [],
            promotions: [],
            observations: [],
            observationCounts: { ...EMPTY_OBSERVATION_COUNTS },
            observationTotal: 0,
            observationPage: 1,
            observationPageSize: DEFAULT_OBSERVATION_PAGE_SIZE,
            observationTotalPages: 1,
            observationHasMore: false,
            observationReviewState: DEFAULT_OBSERVATION_QUERY.reviewState,
            loading: true,
            promotionsLoading: false,
            observationsLoading: false,
            promotionsLoaded: false,
            observationsLoaded: false,
            error: null,
            promotionsError: null,
            observationsError: null,
          }
        : { loading: true, error: null });

      try {
        const ov = await api.getWorldbuildingOverview(projectId);
        if (get().projectId !== projectId || requestEpoch !== epoch) return;
        set({
          overview: ov,
          rules: ov.rules,
          characters: ov.characters as Character[],
          locations: ov.locations as Location[],
          loading: false,
        });
      } catch (error) {
        if (get().projectId === projectId && requestEpoch === epoch) {
          set({ error: error as Error, loading: false });
        }
      }
    })();

    dataRequests.set(projectId, request);
    try {
      await request;
    } finally {
      if (dataRequests.get(projectId) === request) {
        dataRequests.delete(projectId);
      }
    }
  },

  fetchPromotions: async (projectId: string, force = false) => {
    const state = get();
    if (!force && state.projectId === projectId && state.promotionsLoaded && !state.promotionsError) {
      return;
    }

    const existingRequest = promotionRequests.get(projectId);
    if (existingRequest) return existingRequest;

    const epoch = requestEpoch;
    const request = (async () => {
      set({ promotionsLoading: true, promotionsError: null });
      try {
        const promotions = await api.listPromotionProposals(projectId);
        if (get().projectId !== projectId || requestEpoch !== epoch) return;
        set({ promotions, promotionsLoading: false, promotionsLoaded: true });
      } catch (error) {
        if (get().projectId === projectId && requestEpoch === epoch) {
          set({ promotionsError: error as Error, promotionsLoading: false });
        }
      }
    })();

    promotionRequests.set(projectId, request);
    try {
      await request;
    } finally {
      if (promotionRequests.get(projectId) === request) {
        promotionRequests.delete(projectId);
      }
    }
  },

  fetchObservations: async (projectId, options = DEFAULT_OBSERVATION_QUERY, force = false) => {
    const normalizedOptions: WorldviewObservationListParams = {
      ...DEFAULT_OBSERVATION_QUERY,
      ...options,
      page: Math.max(1, options.page ?? DEFAULT_OBSERVATION_QUERY.page),
      pageSize: Math.max(1, options.pageSize ?? DEFAULT_OBSERVATION_QUERY.pageSize),
    };
    const requestKey = observationRequestKey(projectId, normalizedOptions);
    const state = get();
    if (
      !force
      && state.projectId === projectId
      && activeObservationRequestKey === requestKey
      && state.observationsLoaded
      && !state.observationsError
    ) {
      return {
        items: state.observations,
        total: state.observationTotal,
        by_type: {},
        by_status: {},
        by_review_state: state.observationCounts,
        page: state.observationPage,
        page_size: state.observationPageSize,
        total_pages: state.observationTotalPages,
        has_more: state.observationHasMore,
      };
    }

    const existingRequest = observationRequests.get(requestKey);
    if (existingRequest) {
      const queryChanged = activeObservationRequestKey !== requestKey;
      activeObservationRequestKey = requestKey;
      set({
        observations: queryChanged ? [] : get().observations,
        observationsLoading: true,
        observationsError: null,
        observationPage: normalizedOptions.page || 1,
        observationPageSize: normalizedOptions.pageSize || DEFAULT_OBSERVATION_PAGE_SIZE,
        observationReviewState: normalizedOptions.reviewState,
      });
      return existingRequest;
    }

    const epoch = requestEpoch;
    const request = (async () => {
      const queryChanged = activeObservationRequestKey !== requestKey;
      activeObservationRequestKey = requestKey;
      set({
        observations: queryChanged ? [] : get().observations,
        observationsLoading: true,
        observationsError: null,
        // Once the workspace has loaded its first page, a tab/page change is a
        // refresh inside that workspace rather than a return to the full-page
        // initial skeleton. This also keeps the selected review tab mounted.
        observationsLoaded: get().observationsLoaded,
        observationPage: normalizedOptions.page || 1,
        observationPageSize: normalizedOptions.pageSize || DEFAULT_OBSERVATION_PAGE_SIZE,
        observationReviewState: normalizedOptions.reviewState,
      });
      try {
        const res = await api.listObservations(projectId, normalizedOptions);
        if (
          get().projectId !== projectId
          || requestEpoch !== epoch
          || activeObservationRequestKey !== requestKey
        ) return null;
        const counts = res.by_review_state || fallbackReviewCounts(res.items);
        const pageSize = res.page_size || normalizedOptions.pageSize || DEFAULT_OBSERVATION_PAGE_SIZE;
        const totalPages = res.total_pages || Math.max(1, Math.ceil(res.total / pageSize));
        const normalized: WorldviewObservationList = {
          ...res,
          by_status: res.by_status || {},
          by_review_state: counts,
          page: res.page || normalizedOptions.page || 1,
          page_size: pageSize,
          total_pages: totalPages,
          has_more: res.has_more ?? (res.total > (res.page || 1) * pageSize),
        };
        set({
          observations: normalized.items,
          observationCounts: normalized.by_review_state,
          observationTotal: normalized.total,
          observationPage: normalized.page,
          observationPageSize: pageSize,
          observationTotalPages: normalized.total_pages,
          observationHasMore: normalized.has_more,
          observationsLoading: false,
          observationsLoaded: true,
        });
        return normalized;
      } catch (error) {
        if (
          get().projectId === projectId
          && requestEpoch === epoch
          && activeObservationRequestKey === requestKey
        ) {
          set({ observations: [], observationsLoading: false, observationsError: error as Error });
        }
        return null;
      }
    })();

    observationRequests.set(requestKey, request);
    try {
      return await request;
    } finally {
      if (observationRequests.get(requestKey) === request) {
        observationRequests.delete(requestKey);
      }
    }
  },

  invalidateProjectedData: (projectId: string) => {
    const state = get();
    if (state.projectId !== projectId) return;

    // Chapter save/generation can atomically project new rules, people,
    // locations and observations. Cancel the cache epoch so an older in-flight
    // response cannot restore the pre-save worldview after invalidation.
    invalidateRequests();
    set({
      overview: null,
      rules: [],
      characters: [],
      locations: [],
      promotions: [],
      observations: [],
      observationCounts: { ...EMPTY_OBSERVATION_COUNTS },
      observationTotal: 0,
      observationPage: 1,
      observationPageSize: DEFAULT_OBSERVATION_PAGE_SIZE,
      observationTotalPages: 1,
      observationHasMore: false,
      observationReviewState: DEFAULT_OBSERVATION_QUERY.reviewState,
      loading: false,
      promotionsLoading: false,
      observationsLoading: false,
      promotionsLoaded: false,
      observationsLoaded: false,
      error: null,
      promotionsError: null,
      observationsError: null,
    });
  },

  clear: () => {
    invalidateRequests();
    set({
      projectId: null,
      overview: null,
      rules: [],
      characters: [],
      locations: [],
      promotions: [],
      observations: [],
      observationCounts: { ...EMPTY_OBSERVATION_COUNTS },
      observationTotal: 0,
      observationPage: 1,
      observationPageSize: DEFAULT_OBSERVATION_PAGE_SIZE,
      observationTotalPages: 1,
      observationHasMore: false,
      observationReviewState: DEFAULT_OBSERVATION_QUERY.reviewState,
      loading: false,
      promotionsLoading: false,
      observationsLoading: false,
      promotionsLoaded: false,
      observationsLoaded: false,
      error: null,
      promotionsError: null,
      observationsError: null,
    });
  },
}));
