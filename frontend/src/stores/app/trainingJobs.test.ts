/**
 * Additional unit tests for useTrainingJobsStore(), complementing the
 * existing app.subscribeTrainingJob.test.ts (kept untouched). This file
 * covers createProjectTrainingJob and two subscribeTrainingJob branches
 * that file does not exercise: a malformed SSE frame (JSON.parse failure,
 * handled defensively rather than crashing) and the onerror handler's
 * defensive close-if-already-closed behaviour.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import { apiClient } from "@/api/client";
import { useTrainingJobsStore } from "@/stores/app/trainingJobs";
import { useAppCoreStore } from "@/stores/app/core";
import { MessageKey } from "@/types/enums";

type Listener = (event: MessageEvent<string>) => void;

class MockEventSource {
  static readonly CLOSED = 2;
  static instances: MockEventSource[] = [];

  readonly url: string;
  readyState = 1; // OPEN
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

  /** Dispatches a raw (possibly malformed) data string. */
  emitRaw(eventName: string, rawData: string): void {
    const event = { data: rawData } as MessageEvent<string>;
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

beforeEach(() => {
  setActivePinia(createPinia());
  vi.restoreAllMocks();
  MockEventSource.instances = [];
  vi.stubGlobal("EventSource", MockEventSource);
});

describe("useTrainingJobsStore().createProjectTrainingJob", () => {
  it("stores the returned job list and reports success", async () => {
    const store = useTrainingJobsStore();
    const coreStore = useAppCoreStore();
    const spy = vi.spyOn(apiClient, "createProjectTrainingJob").mockResolvedValue({
      jobs: [
        {
          id: "job-1",
          project_id: "p1",
          title: "New Job",
          modality: "image" as never,
          worker: "kohya-ss",
          dataset_path: "/data",
          status: "queued",
          note: null,
          progress: 0,
          progress_label: null,
          exit_code: null,
          stderr_tail: null,
          resume_checkpoint_path: null,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        },
      ],
    });

    const result = await store.createProjectTrainingJob("p1", {
      title: "New Job",
      modality: "image",
      dataset_path: "/data",
      worker: "kohya-ss",
    });

    expect(spy).toHaveBeenCalledWith("p1", {
      title: "New Job",
      modality: "image",
      dataset_path: "/data",
      worker: "kohya-ss",
    });
    expect(store.projectTrainingJobs.p1).toHaveLength(1);
    expect(result).toEqual(store.projectTrainingJobs.p1);
    expect(coreStore.lastMessageKey).toBe(MessageKey.SUCCESS_ADD0);
    expect(coreStore.errorMessageKey).toBeNull();
  });
});

describe("useTrainingJobsStore().subscribeTrainingJob — defensive branches", () => {
  it("does not crash and leaves state unchanged on a malformed (non-JSON) frame", () => {
    const store = useTrainingJobsStore();
    store.subscribeTrainingJob("p1", "job-1");
    const source = lastInstance();

    expect(() => source.emitRaw("progress", "{not valid json")).not.toThrow();
    expect(store.projectTrainingJobs.p1).toBeUndefined();
  });

  it("onerror closes the source only once it has reached CLOSED (defensive, not a reconnect leak)", () => {
    const store = useTrainingJobsStore();
    store.subscribeTrainingJob("p1", "job-1");
    const source = lastInstance();

    // Transient error while still OPEN: browser will auto-reconnect, so this
    // must NOT close the source.
    source.onerror?.();
    expect(source.closeCallCount).toBe(0);

    // Once the connection has actually reached CLOSED, onerror closes
    // defensively (idempotent) rather than leaving a reconnecting socket.
    source.readyState = MockEventSource.CLOSED;
    source.onerror?.();
    expect(source.closeCallCount).toBe(1);
  });
});
