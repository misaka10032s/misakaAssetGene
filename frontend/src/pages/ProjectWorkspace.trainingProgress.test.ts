/**
 * Mounts the REAL ProjectWorkspace.vue page and proves the rendered training
 * progress figure updates as simulated SSE frames arrive, and that unmounting
 * closes the underlying EventSource (spec §7.3 deferred tail — the gap this
 * change closes: subscribeTrainingJob existed but nothing called it).
 *
 * jsdom has no EventSource; a minimal mock is installed on globalThis (see
 * MockEventSource below) so the page's own `subscribeTrainingJob` call
 * (stores/app.ts) opens one and this test can dispatch synthetic
 * `event: progress` / `event: done` frames identical in shape to what
 * core/main.py's SSE endpoint actually sends.
 *
 * apiClient is mocked at the network boundary only — every store action the
 * page calls on mount (loadProject / selectProject / loadProjects /
 * loadProjectConversation / loadProjectWorkspace / loadProjectTrainingWorkspace)
 * still runs for real; only the underlying HTTP call is replaced.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

import ProjectWorkspace from "@/pages/ProjectWorkspace.vue";
import { i18n } from "@/i18n";
import type { TrainingJob } from "@/types/api";
import { Modality } from "@/types/enums";

// vi.mock factories are hoisted above top-level const declarations, so any
// value they close over must itself be created via vi.hoisted().
const { PROJECT_ID } = vi.hoisted(() => ({ PROJECT_ID: "proj-1" }));

vi.mock("vue-router", () => ({
  useRoute: () => ({ params: { projectId: PROJECT_ID } }),
  // ProjectWorkspace.vue imports RouterLink directly from vue-router (not just
  // via the global registration) — the mock module must provide a stand-in so
  // that import resolves, even though `stubs: { RouterLink: true }` handles
  // the template usage.
  RouterLink: { name: "RouterLink", template: "<a><slot /></a>" },
}));

vi.mock("@/api/client", () => {
  const apiClient = {
    getProject: vi.fn().mockResolvedValue({
      project: { id: PROJECT_ID, name: "Test Project", type: "RPG", synopsis: "" },
    }),
    selectProject: vi.fn().mockResolvedValue(undefined),
    listProjects: vi.fn().mockResolvedValue({
      projects: [{ id: PROJECT_ID, name: "Test Project", type: "RPG", synopsis: "" }],
      current_project_id: PROJECT_ID,
    }),
    projectConversationPage: vi.fn().mockResolvedValue({ entries: [], total: 0 }),
    projectWorkspace: vi.fn().mockResolvedValue({ jobs: [], assets: [], plans: [] }),
    projectTrainingWorkspace: vi.fn(),
    trainingJobStreamUrl: (projectId: string, jobId: string) =>
      `http://127.0.0.1:8401/api/v1/projects/${projectId}/training/${jobId}/stream`,
    exportProjectDownloadUrl: () => "#",
    assetFileUrl: () => "#",
    ApiClientError: class ApiClientError extends Error {},
  };
  return { apiClient };
});

// ---------------------------------------------------------------------------
// Mock EventSource (jsdom does not implement it)
// ---------------------------------------------------------------------------

type Listener = (event: MessageEvent<string>) => void;

class MockEventSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  static instances: MockEventSource[] = [];

  readonly url: string;
  readyState = MockEventSource.OPEN;
  onerror: (() => void) | null = null;
  private readonly listeners = new Map<string, Listener[]>();
  closeCallCount = 0;

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: Listener): void {
    const existing = this.listeners.get(type) ?? [];
    existing.push(listener);
    this.listeners.set(type, existing);
  }

  close(): void {
    this.closeCallCount += 1;
    this.readyState = MockEventSource.CLOSED;
  }

  emit(eventName: string, data: unknown): void {
    const event = { data: JSON.stringify(data) } as MessageEvent<string>;
    for (const listener of this.listeners.get(eventName) ?? []) {
      listener(event);
    }
  }
}

function instanceForJob(jobId: string): MockEventSource {
  const found = MockEventSource.instances.find((instance) => instance.url.includes(jobId));
  if (!found) {
    throw new Error(`No MockEventSource opened for job ${jobId}`);
  }
  return found;
}

function makeJob(overrides: Partial<TrainingJob> = {}): TrainingJob {
  const now = new Date().toISOString();
  return {
    id: "job-1",
    project_id: PROJECT_ID,
    title: "Test LoRA job",
    modality: Modality.IMAGE,
    worker: "kohya-ss",
    dataset_path: "/data/ds",
    status: "queued",
    note: null,
    progress: 0,
    progress_label: null,
    exit_code: null,
    stderr_tail: null,
    resume_checkpoint_path: null,
    created_at: now,
    updated_at: now,
    ...overrides,
  };
}

let wrapper: VueWrapper | null = null;

beforeEach(() => {
  setActivePinia(createPinia());
  MockEventSource.instances = [];
  vi.stubGlobal("EventSource", MockEventSource);
});

afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  vi.unstubAllGlobals();
});

async function mountWorkspace(initialJobs: TrainingJob[]): Promise<VueWrapper> {
  const { apiClient } = await import("@/api/client");
  vi.mocked(apiClient.projectTrainingWorkspace).mockResolvedValue({ jobs: initialJobs });

  const instance = mount(ProjectWorkspace, {
    global: {
      plugins: [i18n],
      stubs: {
        RouterLink: true,
        TrainingEntities: true,
        ExportConfirmDialog: true,
        InpaintMaskEditor: true,
        LicenseReportView: true,
      },
    },
  });
  // The projectId watcher (immediate: true) chains several awaited store
  // actions; flush the microtask queue until they've all settled.
  await flushPromises();
  await flushPromises();
  return instance;
}

describe("ProjectWorkspace.vue — live training progress (spec §7.3)", () => {
  it("subscribes to an active (queued) training job on mount and renders its initial figure", async () => {
    wrapper = await mountWorkspace([makeJob({ status: "queued", progress: 0, progress_label: null })]);

    expect(MockEventSource.instances).toHaveLength(1);
    expect(wrapper.text()).toContain("queued");
  });

  it("updates the rendered progress figure as simulated SSE progress frames arrive", async () => {
    wrapper = await mountWorkspace([makeJob({ status: "running", progress: 5, progress_label: "Starting" })]);

    expect(wrapper.get('[data-testid="training-job-progress"]').text()).toContain("5%");

    const source = instanceForJob("job-1");
    source.emit("progress", { job: makeJob({ status: "running", progress: 47, progress_label: "Epoch 3/10" }) });
    await flushPromises();

    const progressText = wrapper.get('[data-testid="training-job-progress"]').text();
    expect(progressText).toContain("47%");
    expect(progressText).toContain("Epoch 3/10");
  });

  it("reflects the terminal done frame and does not leave the figure stuck on a stale value", async () => {
    wrapper = await mountWorkspace([makeJob({ status: "running", progress: 80, progress_label: "Almost there" })]);
    const source = instanceForJob("job-1");

    source.emit("done", { job: makeJob({ status: "completed", progress: 100, progress_label: "Completed" }) });
    await flushPromises();

    const progressText = wrapper.get('[data-testid="training-job-progress"]').text();
    expect(progressText).toContain("100%");
    expect(progressText).toContain("Completed");
    expect(wrapper.text()).toContain("completed");
    // The stream closes itself on the terminal frame (subscribeTrainingJob's
    // own handleFrame), and the page's unsubscribe bookkeeping (removing the
    // now-inactive job from trainingSubscriptions) may call close() again —
    // real EventSource.close() is idempotent, so assert CLOSED state (>=1
    // call) rather than an exact count.
    expect(source.readyState).toBe(MockEventSource.CLOSED);
    expect(source.closeCallCount).toBeGreaterThanOrEqual(1);
  });

  it("unsubscribes (closes the EventSource) when the component unmounts, so no connection leaks", async () => {
    wrapper = await mountWorkspace([makeJob({ status: "running", progress: 10, progress_label: "Starting" })]);
    const source = instanceForJob("job-1");
    expect(source.closeCallCount).toBe(0);

    wrapper.unmount();
    wrapper = null;

    expect(source.closeCallCount).toBe(1);
  });

  it("does not open a live subscription for a job that is not queued/running", async () => {
    wrapper = await mountWorkspace([makeJob({ status: "completed", progress: 100, progress_label: "Completed" })]);
    expect(MockEventSource.instances).toHaveLength(0);
  });
});
