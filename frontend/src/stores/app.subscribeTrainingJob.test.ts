/**
 * Unit tests for useAppStore().subscribeTrainingJob — the SSE-to-store
 * mechanism ProjectWorkspace.vue relies on (spec §7.3 deferred tail).
 *
 * jsdom does not implement EventSource, so these tests install a minimal
 * mock EventSource on `globalThis` that records every constructed instance
 * (by URL) and lets a test dispatch synthetic `progress` / `done` frames —
 * the same shape core/main.py's SSE endpoint actually emits
 * (`event: progress` / `event: done`, `data: JSON.stringify({ job })`).
 *
 * These tests do NOT touch the network or a real backend; they verify only
 * that a streamed frame correctly merges into `projectTrainingJobs` (the
 * reactive state ProjectWorkspace.vue's `projectTrainingJobs` computed reads),
 * that a terminal frame closes the source and fires `onDone`, and that the
 * returned unsubscribe function closes the source without waiting for a
 * terminal frame (no leaked EventSource on unmount).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import { useAppStore } from "@/stores/app";
import type { TrainingJob } from "@/types/api";
import { Modality } from "@/types/enums";

// ---------------------------------------------------------------------------
// Mock EventSource
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

  /** Test helper: dispatch a synthetic named SSE frame to registered listeners. */
  emit(eventName: string, data: unknown): void {
    const event = { data: JSON.stringify(data) } as MessageEvent<string>;
    for (const listener of this.listeners.get(eventName) ?? []) {
      listener(event);
    }
  }
}

function lastInstance(): MockEventSource {
  const instance = MockEventSource.instances[MockEventSource.instances.length - 1];
  if (!instance) {
    throw new Error("No MockEventSource instance was constructed");
  }
  return instance;
}

function makeJob(overrides: Partial<TrainingJob> = {}): TrainingJob {
  const now = new Date().toISOString();
  return {
    id: "job-1",
    project_id: "proj-1",
    title: "Test LoRA job",
    modality: Modality.IMAGE,
    worker: "kohya-ss",
    dataset_path: "/data/ds",
    status: "running",
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

beforeEach(() => {
  setActivePinia(createPinia());
  MockEventSource.instances = [];
  vi.stubGlobal("EventSource", MockEventSource);
});

describe("useAppStore().subscribeTrainingJob", () => {
  it("merges a streamed progress frame into projectTrainingJobs", () => {
    const store = useAppStore();
    store.subscribeTrainingJob("proj-1", "job-1");

    const source = lastInstance();
    expect(source.url).toContain("proj-1");
    expect(source.url).toContain("job-1");
    expect(source.url).toContain("/stream");

    source.emit("progress", { job: makeJob({ status: "running", progress: 42, progress_label: "Step 42/100" }) });

    const jobs = store.projectTrainingJobs["proj-1"] ?? [];
    expect(jobs).toHaveLength(1);
    expect(jobs[0].progress).toBe(42);
    expect(jobs[0].progress_label).toBe("Step 42/100");
  });

  it("applies successive progress frames as the streamed value changes", () => {
    const store = useAppStore();
    store.subscribeTrainingJob("proj-1", "job-1");
    const source = lastInstance();

    source.emit("progress", { job: makeJob({ progress: 10, progress_label: "Step 10" }) });
    expect(store.projectTrainingJobs["proj-1"]?.[0].progress).toBe(10);

    source.emit("progress", { job: makeJob({ progress: 55, progress_label: "Step 55" }) });
    expect(store.projectTrainingJobs["proj-1"]?.[0].progress).toBe(55);
    expect(store.projectTrainingJobs["proj-1"]?.[0].progress_label).toBe("Step 55");
  });

  it("on a terminal done frame, closes the source and invokes onDone with the final job", () => {
    const store = useAppStore();
    const onDone = vi.fn();
    store.subscribeTrainingJob("proj-1", "job-1", onDone);
    const source = lastInstance();

    source.emit("progress", { job: makeJob({ status: "running", progress: 90, progress_label: "Almost done" }) });
    expect(source.closeCallCount).toBe(0);

    const finalJob = makeJob({ status: "completed", progress: 100, progress_label: "Completed" });
    source.emit("done", { job: finalJob });

    expect(source.closeCallCount).toBe(1);
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(onDone).toHaveBeenCalledWith(expect.objectContaining({ status: "completed", progress: 100 }));
    expect(store.projectTrainingJobs["proj-1"]?.[0].status).toBe("completed");
  });

  it("returns an unsubscribe function that closes the source immediately (no leak on unmount)", () => {
    const store = useAppStore();
    const unsubscribe = store.subscribeTrainingJob("proj-1", "job-1");
    const source = lastInstance();

    expect(source.closeCallCount).toBe(0);
    unsubscribe();
    expect(source.closeCallCount).toBe(1);
  });

  it("scopes merged jobs to the subscribed project only", () => {
    const store = useAppStore();
    store.subscribeTrainingJob("proj-A", "job-1");
    const source = lastInstance();

    source.emit("progress", { job: makeJob({ project_id: "proj-A", progress: 33 }) });

    expect(store.projectTrainingJobs["proj-A"]?.[0].progress).toBe(33);
    expect(store.projectTrainingJobs["proj-B"]).toBeUndefined();
  });

  it("replaces an existing job entry by id rather than duplicating it", () => {
    const store = useAppStore();
    // Seed an initial snapshot the way loadProjectTrainingWorkspace would.
    store.projectTrainingJobs = {
      "proj-1": [makeJob({ status: "queued", progress: 0 })],
    };

    store.subscribeTrainingJob("proj-1", "job-1");
    const source = lastInstance();
    source.emit("progress", { job: makeJob({ status: "running", progress: 20 }) });

    const jobs = store.projectTrainingJobs["proj-1"];
    expect(jobs).toHaveLength(1);
    expect(jobs[0].status).toBe("running");
    expect(jobs[0].progress).toBe(20);
  });
});
