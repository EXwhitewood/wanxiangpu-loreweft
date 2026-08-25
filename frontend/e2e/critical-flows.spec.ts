import { expect, test, type Page, type Route } from "@playwright/test";

const projectId = "e2e-project";

type MockServerState = {
  chapters: Array<{
    chapter_number: number;
    title: string;
    status: string;
    word_count: number;
  }>;
  chapterLoads: Record<number, number>;
  autoObservationAttempts: number;
};

const project = {
  id: projectId,
  name: "端到端测试作品",
  description: "只存在于 Playwright 网络桩中",
  genre: "fantasy",
  word_count_target: 100_000,
  created_at: "2026-07-25T00:00:00Z",
  updated_at: "2026-07-25T00:00:00Z",
  current_chapter: 2,
  total_words: 8,
  outline_data: { chapters: [] },
  core_data: {},
};

const storyState = {
  timeline_id: "main",
  narrative_time: null,
  active_chapter: 1,
  active_scene: 1,
  pov_character: null,
  objective_state: {},
  subjective_views: {},
};

const overview = {
  total_rules: 0,
  total_characters: 0,
  total_locations: 0,
  total_foreshadowing: 0,
  total_promotions: 0,
  total_styles: 0,
  active_style: null,
  promotion_status: {},
  foreshadowing_status: {},
  rule_categories: {},
  seed_stats: {},
  rules: [],
  characters: [],
  locations: [],
  foreshadowing: [],
  observations: {
    total: 2,
    by_type: { character: 2 },
    by_status: { active: 1, promoted: 1 },
    pending_review: 1,
  },
};

function observation(id: string, name: string, autoPromoted: boolean) {
  return {
    id,
    entity_type: "character",
    entity_name: name,
    entity_name_normalized: name,
    operation: "new",
    payload: {},
    confidence: 0.95,
    evidence_text: `${name}的正文证据`,
    chapter_number: 1,
    scene_index: null,
    status: autoPromoted ? "promoted" : "active",
    auto_promoted: autoPromoted,
    core_entity_id: autoPromoted ? "character-1" : null,
    confirmed_by: null,
    confirmed_at: autoPromoted ? "2026-07-25T00:00:00Z" : null,
    orphan_warning: false,
    generation_revision: 1,
    extraction_version: 1,
    extraction_source: "e2e",
    created_at: "2026-07-25T00:00:00Z",
  };
}

async function fulfillJson(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json; charset=utf-8",
    body: JSON.stringify(body),
  });
}

async function installApiMock(page: Page): Promise<MockServerState> {
  const state: MockServerState = {
    chapters: [
      { chapter_number: 1, title: "第一章", status: "draft", word_count: 8 },
    ],
    chapterLoads: {},
    autoObservationAttempts: 0,
  };

  // Typography is not under test. Keep the suite fully local and avoid a CDN
  // timeout delaying otherwise deterministic functional assertions.
  await page.route(
    /^https:\/\/(cdn\.jsdelivr\.net|fonts\.googleapis\.com)\//,
    (route) => route.fulfill({ status: 200, contentType: "text/css", body: "" }),
  );

  // Match only the backend root. A broad `**/api/**` glob also catches the
  // Vite source module `/src/api/client.ts` and would prevent React booting.
  await page.route(/^http:\/\/127\.0\.0\.1:\d+\/api\//, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/api/, "");
    const method = request.method();

    if (method === "GET" && path === `/projects/${projectId}`) {
      return fulfillJson(route, project);
    }
    if (method === "GET" && path === `/projects/${projectId}/chapters`) {
      return fulfillJson(route, state.chapters);
    }
    if (method === "GET" && path === `/projects/${projectId}/state`) {
      return fulfillJson(route, storyState);
    }
    if (method === "GET" && path === `/outline/${projectId}/status`) {
      return fulfillJson(route, {
        frozen: false,
        frozen_at: null,
        version: 1,
        has_outline: false,
        has_draft: false,
        chapter_continuity: {
          valid: true,
          source: "none",
          chapter_count: 0,
          max_chapter: 0,
          numbers: [],
          missing: [],
          duplicates: [],
          invalid_entries: [],
        },
      });
    }
    if (method === "GET" && path === `/projects/${projectId}/chapters/history`) {
      return fulfillJson(route, []);
    }
    if (method === "GET" && path === `/projects/${projectId}/writing-assistance/settings`) {
      return fulfillJson(route, {
        consistency_reminders: { enabled: false, check_on_save: false, dimensions: [] },
        ai_edit_permission: { mode: "proposal_only" },
      });
    }

    const chapterMatch = path.match(new RegExp(`^/projects/${projectId}/chapters/(\\d+)$`));
    if (chapterMatch) {
      const chapterNumber = Number(chapterMatch[1]);
      if (method === "GET") {
        state.chapterLoads[chapterNumber] = (state.chapterLoads[chapterNumber] || 0) + 1;
        return fulfillJson(route, {
          chapter_number: chapterNumber,
          title: `第${chapterNumber}章`,
          content: chapterNumber === 1 ? "第一章正文" : "",
          status: "draft",
        });
      }
      if (method === "PUT") {
        const payload = request.postDataJSON() as { title?: string; status?: string };
        if (!state.chapters.some((item) => item.chapter_number === chapterNumber)) {
          state.chapters.push({
            chapter_number: chapterNumber,
            title: payload.title || `第${chapterNumber}章`,
            status: payload.status || "draft",
            word_count: 0,
          });
        }
        return fulfillJson(route, {
          chapter_number: chapterNumber,
          title: payload.title || `第${chapterNumber}章`,
          content: "",
          status: payload.status || "draft",
          settlement: null,
        });
      }
    }

    const diagnosticsMatch = path.match(
      new RegExp(`^/projects/${projectId}/chapters/(\\d+)/diagnostics$`),
    );
    if (method === "GET" && diagnosticsMatch) {
      return fulfillJson(route, { chapter_number: Number(diagnosticsMatch[1]), items: [] });
    }
    if (
      method === "GET"
      && path.match(new RegExp(`^/projects/${projectId}/chapters/\\d+/settlement$`))
    ) {
      return fulfillJson(route, null);
    }
    if (
      method === "GET"
      && path.match(new RegExp(`^/outline/${projectId}/story-plan/chapter-blueprint/\\d+$`))
    ) {
      const chapterNumber = Number(path.split("/").at(-1));
      return fulfillJson(route, {
        chapter_number: chapterNumber,
        chapter: { chapter_number: chapterNumber, title: `第${chapterNumber}章` },
        scene_brief: {},
        scenes: [],
        revision: 1,
        plan_version: 1,
        scene_brief_version: 1,
        persisted: true,
        frozen: false,
        frozen_layers: [],
        diagnostics: [],
      });
    }

    if (method === "GET" && path === `/worldbuilding/${projectId}/overview`) {
      return fulfillJson(route, overview);
    }
    if (method === "GET" && path === `/worldbuilding/${projectId}/promotions`) {
      return fulfillJson(route, []);
    }
    if (method === "GET" && path === `/worldbuilding/${projectId}/observations`) {
      const reviewState = url.searchParams.get("review_state") || "all";
      const counts = { all: 2, pending: 1, auto: 1, manual: 0, confirmed: 1 };
      if (reviewState === "auto") {
        state.autoObservationAttempts += 1;
        if (state.autoObservationAttempts === 1) {
          return fulfillJson(route, { detail: "模拟网络抖动" }, 503);
        }
        return fulfillJson(route, {
          items: [observation("auto-1", "已自动确认的陈长生", true)],
          total: 1,
          by_type: { character: 1 },
          by_status: { promoted: 1 },
          by_review_state: counts,
          page: 1,
          page_size: 50,
          total_pages: 1,
          has_more: false,
        });
      }
      return fulfillJson(route, {
        items: [observation("pending-1", "待确认的陈长生", false)],
        total: 1,
        by_type: { character: 1 },
        by_status: { active: 1 },
        by_review_state: counts,
        page: 1,
        page_size: 50,
        total_pages: 1,
        has_more: false,
      });
    }

    return fulfillJson(route, { detail: `E2E 未配置接口: ${method} ${path}` }, 404);
  });

  return state;
}

test("新增章节后当前路由立即加载新章", async ({ page }) => {
  const state = await installApiMock(page);

  await page.goto(`/project/${projectId}/editor?chapter=1`);
  await expect(page.getByRole("button", { name: "添加新章节" })).toBeVisible();
  await expect(page.getByRole("button", { name: "保存文本" })).toBeEnabled();

  await page.getByRole("button", { name: "添加新章节" }).click();

  await expect(page).toHaveURL(new RegExp(`/project/${projectId}/editor\\?chapter=2$`));
  await expect.poll(() => state.chapterLoads[2] || 0).toBeGreaterThan(0);
  await expect(page.getByRole("heading", { name: "第2章", exact: true })).toBeVisible();
  await expect(page.getByText("尚未加载章节")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "保存文本" })).toBeEnabled();
});

test("世界观深链进入后可隔离筛选并从失败中重试", async ({ page }) => {
  const state = await installApiMock(page);

  await page.goto(`/project/${projectId}/worldbuilding?tab=promotions`);
  await expect(page).toHaveURL(new RegExp(`/worldbuilding\\?tab=promotions$`));
  await expect(page.getByRole("heading", { name: "设定晋升工作台" })).toBeVisible();
  await expect(page.getByText("待确认的陈长生", { exact: true })).toBeVisible();

  const autoFilter = page.getByRole("button", { name: /自动确认/ });
  await autoFilter.click();
  await expect(page.getByRole("alert")).toContainText("筛选结果加载失败，请重试");
  await expect(page.getByText("待确认的陈长生", { exact: true })).toHaveCount(0);

  await autoFilter.click();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.getByText("已自动确认的陈长生", { exact: true })).toBeVisible();
  await expect(autoFilter).toHaveAttribute("aria-pressed", "true");
  expect(state.autoObservationAttempts).toBe(2);
});
