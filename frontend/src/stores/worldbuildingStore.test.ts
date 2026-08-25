import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "@/api/client";
import type {
  WorldviewObservation,
  WorldviewObservationList,
  WorldviewObservationReviewState,
} from "@/types";
import { useWorldbuildingStore } from "./worldbuildingStore";

vi.mock("@/api/client", () => ({
  listObservations: vi.fn(),
}));

function observation(id: string, status: WorldviewObservation["status"]): WorldviewObservation {
  return {
    id,
    entity_type: "fact",
    entity_name: id,
    entity_name_normalized: id,
    operation: "new",
    confidence: 0.9,
    evidence_text: id,
    chapter_number: 1,
    scene_index: null,
    status,
    auto_promoted: status === "promoted",
    core_entity_id: null,
    confirmed_by: status === "promoted" ? "system" : null,
    confirmed_at: null,
    orphan_warning: false,
    generation_revision: 1,
    extraction_version: 1,
    extraction_source: "test",
    created_at: "2026-07-25T00:00:00Z",
  };
}

function observationPage(
  reviewState: WorldviewObservationReviewState,
  items: WorldviewObservation[],
  page = 1,
): WorldviewObservationList {
  const counts = { all: 12, pending: 7, auto: 3, manual: 2, confirmed: 5 };
  return {
    items,
    total: counts[reviewState],
    by_type: { fact: counts[reviewState] },
    by_status: {},
    by_review_state: counts,
    page,
    page_size: 5,
    total_pages: Math.max(1, Math.ceil(counts[reviewState] / 5)),
    has_more: page * 5 < counts[reviewState],
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("worldbuilding observation pagination", () => {
  beforeEach(() => {
    useWorldbuildingStore.getState().clear();
    useWorldbuildingStore.setState({ projectId: "project-1" });
  });

  it("sends the review filter and page to the API and stores server-wide counts", async () => {
    const response = observationPage("pending", [observation("pending-6", "active")], 2);
    vi.mocked(api.listObservations).mockResolvedValue(response);

    await useWorldbuildingStore.getState().fetchObservations("project-1", {
      reviewState: "pending",
      page: 2,
      pageSize: 5,
    }, true);

    expect(api.listObservations).toHaveBeenCalledWith("project-1", {
      reviewState: "pending",
      page: 2,
      pageSize: 5,
    });
    const state = useWorldbuildingStore.getState();
    expect(state.observations.map((item) => item.id)).toEqual(["pending-6"]);
    expect(state.observationCounts).toEqual(response.by_review_state);
    expect(state.observationTotal).toBe(7);
    expect(state.observationPage).toBe(2);
    expect(state.observationTotalPages).toBe(2);
  });

  it("does not let a stale tab response overwrite the tab the user returned to", async () => {
    const pending = deferred<WorldviewObservationList>();
    const auto = deferred<WorldviewObservationList>();
    vi.mocked(api.listObservations).mockImplementation((_projectId, options) => {
      const reviewState = typeof options === "string" ? "all" : options?.reviewState;
      return reviewState === "auto" ? auto.promise : pending.promise;
    });

    const firstPending = useWorldbuildingStore.getState().fetchObservations(
      "project-1",
      { reviewState: "pending", page: 1, pageSize: 5 },
      true,
    );
    const autoRequest = useWorldbuildingStore.getState().fetchObservations(
      "project-1",
      { reviewState: "auto", page: 1, pageSize: 5 },
      true,
    );
    const returnedPending = useWorldbuildingStore.getState().fetchObservations(
      "project-1",
      { reviewState: "pending", page: 1, pageSize: 5 },
      true,
    );

    auto.resolve(observationPage("auto", [observation("auto", "promoted")]));
    await autoRequest;
    pending.resolve(observationPage("pending", [observation("pending", "active")]));
    await Promise.all([firstPending, returnedPending]);

    const state = useWorldbuildingStore.getState();
    expect(state.observationReviewState).toBe("pending");
    expect(state.observations.map((item) => item.id)).toEqual(["pending"]);
  });
});
