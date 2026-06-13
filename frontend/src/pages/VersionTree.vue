<script setup lang="ts">
/**
 * VersionTree.vue — Git-log style DAG renderer for asset version lineage (spec §8.2 / M5.6).
 *
 * Renders a project's version tree as an SVG node graph and provides a two-node
 * side-by-side diff panel. No heavy graph library; layout is computed with a
 * generation/column approach in pure TypeScript.
 */
import { computed, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { useI18n } from "vue-i18n";

import { useAppStore } from "@/stores/app";
import { useWindowSize } from "@/composables/useWindowSize";
import type { VersionTreeNode, VersionDiffResponse } from "@/types/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Node enriched with SVG layout coordinates. */
interface LayoutNode {
  node: VersionTreeNode;
  /** Generation depth (0 = root). */
  generation: number;
  /** Column index within the generation (0-based, for branch separation). */
  column: number;
  /** Computed SVG centre-x. */
  cx: number;
  /** Computed SVG centre-y. */
  cy: number;
}

/** A directed edge in the layout (parent → child). */
interface LayoutEdge {
  fromX: number;
  fromY: number;
  toX: number;
  toY: number;
  /** Branch column of the child — used for colour assignment. */
  branchColumn: number;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const NODE_WIDTH = 160;
const NODE_HEIGHT = 52;
const H_GAP = 32; // horizontal gap between sibling columns
const V_GAP = 56; // vertical gap between generations
const PADDING = 24; // SVG outer padding

// Branch colours (UnoCSS token-safe stroke values matched to theme palette).
// Entries 5-6 (#a855f7, #06b6d4) are intentionally literal — this is a multi-colour
// branch palette, not a semantic state colour; no theme token maps to these shades.
const BRANCH_STROKES = [
  "var(--un-color-primary, #6366f1)",
  "var(--un-color-success, #22c55e)",
  "var(--un-color-warning, #f59e0b)",
  "var(--un-color-error, #ef4444)",
  "#a855f7",
  "#06b6d4",
];

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

const route = useRoute();
const appStore = useAppStore();
const { t } = useI18n();
const { splitGridClass } = useWindowSize();

const projectId = computed<string>(() => String(route.params.projectId ?? ""));

const isLoading = ref(false);
const loadError = ref<string | null>(null);
const isDiffLoading = ref(false);
const diffError = ref<string | null>(null);

/** The two nodes the user has selected for diff. */
const selectedIds = ref<[string | null, string | null]>([null, null]);

/** Loaded diff result (null until a compare is triggered). */
const diffResult = ref<VersionDiffResponse | null>(null);

// ---------------------------------------------------------------------------
// Data loading
// ---------------------------------------------------------------------------

watch(
  projectId,
  async (nextProjectId) => {
    if (!nextProjectId) return;
    isLoading.value = true;
    loadError.value = null;
    try {
      await appStore.loadProjectVersionTree(nextProjectId);
    } catch {
      loadError.value = t("versions.errorLoad");
    } finally {
      isLoading.value = false;
    }
  },
  { immediate: true },
);

const treeData = computed(() =>
  projectId.value ? (appStore.projectVersionTrees[projectId.value] ?? null) : null,
);

const nodes = computed<VersionTreeNode[]>(() => treeData.value?.nodes ?? []);

// ---------------------------------------------------------------------------
// DAG layout computation
// ---------------------------------------------------------------------------

/**
 * Computes layout positions for all nodes using a generation-depth assignment.
 * Each node's depth = max ancestor depth + 1.  Within each generation, siblings
 * are placed in separate columns using DFS child-ordering.
 */
function computeLayout(rawNodes: VersionTreeNode[]): { layoutNodes: LayoutNode[]; edges: LayoutEdge[]; svgWidth: number; svgHeight: number } {
  if (rawNodes.length === 0) {
    return { layoutNodes: [], edges: [], svgWidth: 0, svgHeight: 0 };
  }

  // Build parent → children adjacency (skip orphaned edges for layout).
  const childrenMap = new Map<string, string[]>();
  const idSet = new Set(rawNodes.map((n) => n.id));
  for (const node of rawNodes) {
    if (node.parent_id && idSet.has(node.parent_id) && !node.is_orphaned) {
      const siblings = childrenMap.get(node.parent_id) ?? [];
      siblings.push(node.id);
      childrenMap.set(node.parent_id, siblings);
    }
  }

  // Compute generation depth via BFS from roots.
  const nodeById = new Map<string, VersionTreeNode>(rawNodes.map((n) => [n.id, n]));
  const depth = new Map<string, number>();
  const roots = rawNodes.filter((n) => !n.parent_id || n.is_orphaned);
  const queue: string[] = roots.map((n) => n.id);
  for (const id of queue) {
    depth.set(id, 0);
  }
  while (queue.length > 0) {
    const current = queue.shift()!;
    const currentDepth = depth.get(current) ?? 0;
    for (const childId of childrenMap.get(current) ?? []) {
      const existing = depth.get(childId) ?? -1;
      if (existing < currentDepth + 1) {
        depth.set(childId, currentDepth + 1);
        queue.push(childId);
      }
    }
  }

  // Group nodes by generation (depth).
  const byGen = new Map<number, string[]>();
  for (const node of rawNodes) {
    const d = depth.get(node.id) ?? 0;
    const gen = byGen.get(d) ?? [];
    gen.push(node.id);
    byGen.set(d, gen);
  }

  // Assign column index within each generation.
  const column = new Map<string, number>();
  const maxGeneration = Math.max(...Array.from(byGen.keys()));
  for (let g = 0; g <= maxGeneration; g++) {
    const ids = byGen.get(g) ?? [];
    ids.forEach((id, idx) => column.set(id, idx));
  }

  // Compute max columns across all generations for SVG width.
  let maxColumns = 0;
  for (const ids of byGen.values()) {
    maxColumns = Math.max(maxColumns, ids.length);
  }

  const svgWidth = Math.max(NODE_WIDTH + PADDING * 2, maxColumns * (NODE_WIDTH + H_GAP) + PADDING * 2 - H_GAP);
  const svgHeight = (maxGeneration + 1) * (NODE_HEIGHT + V_GAP) + PADDING * 2 - V_GAP;

  // Build LayoutNode list.
  const layoutNodes: LayoutNode[] = [];
  for (const node of rawNodes) {
    const g = depth.get(node.id) ?? 0;
    const col = column.get(node.id) ?? 0;
    const cx = PADDING + col * (NODE_WIDTH + H_GAP) + NODE_WIDTH / 2;
    const cy = PADDING + g * (NODE_HEIGHT + V_GAP) + NODE_HEIGHT / 2;
    layoutNodes.push({ node, generation: g, column: col, cx, cy });
  }

  // Build edges from parent → child.
  const layoutNodeById = new Map<string, LayoutNode>(layoutNodes.map((ln) => [ln.node.id, ln]));
  const edges: LayoutEdge[] = [];
  for (const ln of layoutNodes) {
    const parentId = ln.node.parent_id;
    if (parentId && !ln.node.is_orphaned) {
      const parentLn = layoutNodeById.get(parentId);
      if (parentLn) {
        edges.push({
          fromX: parentLn.cx,
          fromY: parentLn.cy + NODE_HEIGHT / 2,
          toX: ln.cx,
          toY: ln.cy - NODE_HEIGHT / 2,
          branchColumn: ln.column,
        });
      }
    }
  }

  return { layoutNodes, edges, svgWidth, svgHeight };
}

const layout = computed(() => computeLayout(nodes.value));

/** Resolves a stroke colour for a branch by column index. */
function branchStroke(col: number): string {
  return BRANCH_STROKES[col % BRANCH_STROKES.length];
}

/**
 * Returns a cubic-bezier SVG path from (x1,y1) to (x2,y2) with vertical handles.
 */
function cubicPath(x1: number, y1: number, x2: number, y2: number): string {
  const midY = (y1 + y2) / 2;
  return `M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${y2}`;
}

// ---------------------------------------------------------------------------
// Node selection logic
// ---------------------------------------------------------------------------

/**
 * Handles click on a node: first click = slot 0, second click = slot 1.
 * Clicking an already-selected node deselects it and clears diff.
 */
function onNodeClick(id: string): void {
  const [a, b] = selectedIds.value;
  if (a === id) {
    selectedIds.value = [null, null];
    diffResult.value = null;
    return;
  }
  if (b === id) {
    selectedIds.value = [a, null];
    diffResult.value = null;
    return;
  }
  if (a === null) {
    selectedIds.value = [id, b];
    return;
  }
  if (b === null) {
    selectedIds.value = [a, id];
    return;
  }
  // Both slots occupied — replace slot 0.
  selectedIds.value = [id, null];
  diffResult.value = null;
}

function clearSelection(): void {
  selectedIds.value = [null, null];
  diffResult.value = null;
}

const canCompare = computed(() => selectedIds.value[0] !== null && selectedIds.value[1] !== null);

// ---------------------------------------------------------------------------
// Diff loading
// ---------------------------------------------------------------------------

async function loadDiff(): Promise<void> {
  const [fromId, toId] = selectedIds.value;
  if (!fromId || !toId || !projectId.value) return;
  isDiffLoading.value = true;
  diffError.value = null;
  diffResult.value = null;
  try {
    diffResult.value = await appStore.loadProjectVersionDiff(projectId.value, fromId, toId);
  } catch {
    diffError.value = t("versions.errorDiff");
  } finally {
    isDiffLoading.value = false;
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Returns the node object for a selected slot. */
function selectedNode(slot: 0 | 1): VersionTreeNode | null {
  const id = selectedIds.value[slot];
  if (!id) return null;
  return nodes.value.find((n) => n.id === id) ?? null;
}

/** Returns whether a node is currently selected (in either slot). */
function isSelected(id: string): boolean {
  return selectedIds.value[0] === id || selectedIds.value[1] === id;
}

/** Returns CSS classes for a node rect based on its status and selection. */
function nodeClass(ln: LayoutNode): string {
  const base = "app-panel transition-shadow";
  if (isSelected(ln.node.id)) return `${base} ring-2 ring-app-primary`;
  return base;
}

/** Formats a short date string from an ISO timestamp. */
function formatDate(isoString: string): string {
  try {
    return new Date(isoString).toLocaleDateString();
  } catch {
    return isoString;
  }
}

/**
 * Returns the localized label for a refine strategy enum value.
 * Falls back to the raw value string if the key is not found in the locale.
 */
function localizeRefineStrategy(strategy: string | null | undefined): string {
  if (!strategy) return "";
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const resolved = t(`versions.refineStrategy.${strategy}` as any);
  // vue-i18n returns the key itself when a translation is missing; show raw value then.
  return resolved !== `versions.refineStrategy.${strategy}` ? String(resolved) : strategy;
}

/** Returns a displayable diff value — shows key+value pairs or a "no change" label. */
function formatDictDiff(val: Record<string, unknown> | null | undefined): string {
  if (!val || Object.keys(val).length === 0) return t("versions.diffNoChange");
  return Object.entries(val)
    .map(([k, v]) => `${k}: ${JSON.stringify(v)}`)
    .join(", ");
}

/** Returns the selection slot number (1-based) or 0 if not selected. */
function selectionSlot(id: string): number {
  if (selectedIds.value[0] === id) return 1;
  if (selectedIds.value[1] === id) return 2;
  return 0;
}
</script>

<template>
  <section class="grid gap-5">
    <!-- Header -->
    <div class="app-panel">
      <h2 class="app-section-title">{{ $t("versions.dagTitle") }}</h2>
      <p class="app-muted">{{ $t("versions.dagIntro") }}</p>
    </div>

    <!-- Loading state -->
    <div v-if="isLoading" class="app-panel">
      <p class="app-muted">{{ $t("versions.loading") }}</p>
    </div>

    <!-- Error state -->
    <div v-else-if="loadError" class="app-panel border-l-4 border-l-app-error">
      <p class="text-app-error">{{ loadError }}</p>
    </div>

    <!-- Empty state -->
    <div v-else-if="nodes.length === 0" class="app-panel">
      <p class="app-muted">{{ $t("versions.empty") }}</p>
    </div>

    <!-- Main content -->
    <template v-else>
      <!-- Cycle warning banner -->
      <div v-if="treeData?.cycle_detected" class="app-panel border-l-4 border-l-app-warning">
        <p class="text-app-warning text-sm font-medium">{{ $t("versions.cycleWarning") }}</p>
      </div>

      <!-- Cap notice banner -->
      <div v-if="treeData?.capped" class="app-panel border-l-4 border-l-app-warning">
        <p class="text-app-warning text-sm">
          {{ $t("versions.cappedNotice", { cap: treeData.node_cap }) }}
        </p>
      </div>

      <!-- Selection / compare toolbar -->
      <div class="app-panel flex flex-wrap items-center gap-3">
        <p class="text-sm text-app-muted flex-1">{{ $t("versions.selectNodeHint") }}</p>
        <button
          v-if="canCompare"
          class="app-btn-primary text-sm px-3 py-1.5"
          :disabled="isDiffLoading"
          @click="loadDiff"
        >
          {{ isDiffLoading ? $t("versions.diffLoading") : $t("versions.compareAction") }}
        </button>
        <button
          v-if="selectedIds[0] !== null || selectedIds[1] !== null"
          class="app-btn-secondary text-sm px-3 py-1.5"
          @click="clearSelection"
        >
          {{ $t("versions.clearSelectionAction") }}
        </button>
      </div>

      <!-- DAG SVG render -->
      <div class="app-panel overflow-x-auto">
        <svg
          :width="layout.svgWidth"
          :height="layout.svgHeight"
          :viewBox="`0 0 ${layout.svgWidth} ${layout.svgHeight}`"
          class="block"
          aria-label="Version tree DAG"
          role="img"
        >
          <!-- Arrow head marker definition -->
          <defs>
            <marker
              v-for="col in Math.max(1, Math.max(...layout.layoutNodes.map((ln) => ln.column)) + 1)"
              :key="`arrow-${col - 1}`"
              :id="`arrow-${col - 1}`"
              markerWidth="8"
              markerHeight="8"
              refX="6"
              refY="3"
              orient="auto"
            >
              <path d="M0,0 L0,6 L8,3 z" :fill="branchStroke(col - 1)" />
            </marker>
          </defs>

          <!-- Edges (parent → child bezier paths) -->
          <g class="dag-edges">
            <path
              v-for="(edge, idx) in layout.edges"
              :key="`edge-${idx}`"
              :d="cubicPath(edge.fromX, edge.fromY, edge.toX, edge.toY)"
              fill="none"
              :stroke="branchStroke(edge.branchColumn)"
              stroke-width="2"
              opacity="0.7"
              :marker-end="`url(#arrow-${edge.branchColumn % BRANCH_STROKES.length})`"
            />
          </g>

          <!-- Nodes -->
          <g
            v-for="ln in layout.layoutNodes"
            :key="ln.node.id"
            :transform="`translate(${ln.cx - NODE_WIDTH / 2}, ${ln.cy - NODE_HEIGHT / 2})`"
            class="cursor-pointer"
            @click="onNodeClick(ln.node.id)"
          >
            <!-- Node background rect -->
            <rect
              :width="NODE_WIDTH"
              :height="NODE_HEIGHT"
              rx="6"
              ry="6"
              :class="[
                isSelected(ln.node.id)
                  ? 'fill-app-primary/20 stroke-app-primary stroke-2'
                  : 'fill-app-panel stroke-app-border stroke-1',
              ]"
            />

            <!-- Orphaned dashed indicator (left accent) -->
            <rect
              v-if="ln.node.is_orphaned"
              width="4"
              :height="NODE_HEIGHT"
              rx="2"
              class="fill-app-warning"
            />

            <!-- Selection slot badge -->
            <circle
              v-if="selectionSlot(ln.node.id) > 0"
              :cx="NODE_WIDTH - 10"
              :cy="10"
              r="9"
              class="fill-app-primary"
            />
            <text
              v-if="selectionSlot(ln.node.id) > 0"
              :x="NODE_WIDTH - 10"
              y="14"
              text-anchor="middle"
              class="fill-app-panel text-xs font-bold"
              style="font-size: 11px; font-weight: 700"
            >
              {{ selectionSlot(ln.node.id) }}
            </text>

            <!-- Node title -->
            <text
              x="10"
              y="20"
              class="fill-app-text text-sm font-medium"
              style="font-size: 12px; font-weight: 600"
              :textLength="ln.node.is_orphaned ? NODE_WIDTH - 24 : NODE_WIDTH - 20"
              lengthAdjust="spacingAndGlyphs"
            >
              {{ ln.node.title.length > 18 ? `${ln.node.title.slice(0, 17)}…` : ln.node.title }}
            </text>

            <!-- Modality + strategy kicker -->
            <text
              x="10"
              y="35"
              class="fill-app-muted"
              style="font-size: 10px"
            >
              {{ ln.node.modality }}
              <tspan v-if="ln.node.refine_strategy"> · {{ localizeRefineStrategy(ln.node.refine_strategy) }}</tspan>
            </text>

            <!-- Status chip -->
            <text
              x="10"
              y="48"
              class="fill-app-muted"
              style="font-size: 9px"
            >
              {{ ln.node.status }}
              <tspan v-if="ln.node.is_orphaned" class="fill-app-warning"> · {{ $t("versions.orphanedBadge") }}</tspan>
            </text>
          </g>
        </svg>
      </div>

      <!-- Two-node side-by-side diff panel -->
      <template v-if="canCompare || diffResult !== null">
        <div class="app-panel">
          <h3 class="app-section-title text-base mb-4">{{ $t("versions.diffTitle") }}</h3>

          <!-- Diff error -->
          <div v-if="diffError" class="text-app-error text-sm mb-3">{{ diffError }}</div>

          <!-- Node summary comparison header -->
          <div :class="`grid gap-4 ${splitGridClass} mb-5`">
            <div
              v-for="slot in ([0, 1] as const)"
              :key="slot"
              class="border border-app-border rounded-lg p-3"
            >
              <p class="app-kicker mb-1">{{ slot === 0 ? $t('versions.diffFrom') : $t('versions.diffTo') }}</p>
              <template v-if="selectedNode(slot)">
                <p class="font-semibold text-app-text text-sm">{{ selectedNode(slot)!.title }}</p>
                <p class="text-xs text-app-muted mt-0.5">
                  {{ selectedNode(slot)!.modality }} ·
                  {{ selectedNode(slot)!.asset_type }} ·
                  {{ formatDate(selectedNode(slot)!.created_at) }}
                </p>
                <p v-if="selectedNode(slot)!.refine_strategy" class="text-xs text-app-muted mt-0.5">
                  {{ $t("versions.nodeStrategy") }}: {{ localizeRefineStrategy(selectedNode(slot)!.refine_strategy) }}
                </p>
                <p v-if="selectedNode(slot)!.backend" class="text-xs text-app-muted mt-0.5">
                  {{ $t("versions.nodeBackend") }}: {{ selectedNode(slot)!.backend }}
                </p>
                <p class="text-xs text-app-muted mt-0.5 font-mono break-all">
                  {{ selectedNode(slot)!.id }}
                </p>
              </template>
              <p v-else class="text-xs text-app-muted">—</p>
            </div>
          </div>

          <!-- Diff result rows -->
          <template v-if="diffResult !== null">
            <!-- Prompt delta -->
            <div class="mb-3">
              <p class="text-xs font-semibold text-app-text mb-1">{{ $t("versions.diffPromptDelta") }}</p>
              <p class="text-sm text-app-muted font-mono whitespace-pre-wrap break-all bg-app-surface rounded p-2">
                {{ diffResult.prompt_delta ?? $t("versions.diffNoChange") }}
              </p>
            </div>

            <!-- Param delta -->
            <div class="mb-3">
              <p class="text-xs font-semibold text-app-text mb-1">{{ $t("versions.diffParamDelta") }}</p>
              <p class="text-sm text-app-muted font-mono whitespace-pre-wrap break-all bg-app-surface rounded p-2">
                {{ formatDictDiff(diffResult.param_delta) }}
              </p>
            </div>

            <!-- Mask diff -->
            <div v-if="diffResult.mask_diff !== null" class="mb-3">
              <p class="text-xs font-semibold text-app-text mb-1">{{ $t("versions.diffMaskDiff") }}</p>
              <div :class="`grid gap-3 ${splitGridClass}`">
                <div class="bg-app-surface rounded p-2">
                  <p class="text-xs text-app-muted mb-0.5">{{ $t("versions.diffFrom") }}</p>
                  <p class="text-sm font-mono text-app-text break-all">
                    {{ diffResult.mask_diff?.from_mask ?? $t("versions.diffNoChange") }}
                  </p>
                </div>
                <div class="bg-app-surface rounded p-2">
                  <p class="text-xs text-app-muted mb-0.5">{{ $t("versions.diffTo") }}</p>
                  <p class="text-sm font-mono text-app-text break-all">
                    {{ diffResult.mask_diff?.to_mask ?? $t("versions.diffNoChange") }}
                  </p>
                </div>
              </div>
            </div>

            <!-- Strategy diff -->
            <div v-if="diffResult.strategy_diff !== null" class="mb-3">
              <p class="text-xs font-semibold text-app-text mb-1">{{ $t("versions.diffStrategyDiff") }}</p>
              <div :class="`grid gap-3 ${splitGridClass}`">
                <div class="bg-app-surface rounded p-2">
                  <p class="text-xs text-app-muted mb-0.5">{{ $t("versions.diffFrom") }}</p>
                  <p class="text-sm font-mono text-app-text">
                    {{ diffResult.strategy_diff?.from ?? $t("versions.diffNoChange") }}
                  </p>
                </div>
                <div class="bg-app-surface rounded p-2">
                  <p class="text-xs text-app-muted mb-0.5">{{ $t("versions.diffTo") }}</p>
                  <p class="text-sm font-mono text-app-text">
                    {{ diffResult.strategy_diff?.to ?? $t("versions.diffNoChange") }}
                  </p>
                </div>
              </div>
            </div>

            <!-- Recipe diff -->
            <div v-if="diffResult.recipe_diff !== null" class="mb-3">
              <p class="text-xs font-semibold text-app-text mb-1">{{ $t("versions.diffRecipeDiff") }}</p>
              <div :class="`grid gap-3 ${splitGridClass}`">
                <div class="bg-app-surface rounded p-2">
                  <p class="text-xs text-app-muted mb-0.5">{{ $t("versions.diffFrom") }}</p>
                  <p class="text-sm font-mono text-app-text">
                    {{ diffResult.recipe_diff?.from ?? $t("versions.diffNoChange") }}
                  </p>
                </div>
                <div class="bg-app-surface rounded p-2">
                  <p class="text-xs text-app-muted mb-0.5">{{ $t("versions.diffTo") }}</p>
                  <p class="text-sm font-mono text-app-text">
                    {{ diffResult.recipe_diff?.to ?? $t("versions.diffNoChange") }}
                  </p>
                </div>
              </div>
            </div>

            <!-- Backend diff -->
            <div v-if="diffResult.backend_diff !== null" class="mb-3">
              <p class="text-xs font-semibold text-app-text mb-1">{{ $t("versions.diffBackendDiff") }}</p>
              <div :class="`grid gap-3 ${splitGridClass}`">
                <div class="bg-app-surface rounded p-2">
                  <p class="text-xs text-app-muted mb-0.5">{{ $t("versions.diffFrom") }}</p>
                  <p class="text-sm font-mono text-app-text">
                    {{ diffResult.backend_diff?.from ?? $t("versions.diffNoChange") }}
                  </p>
                </div>
                <div class="bg-app-surface rounded p-2">
                  <p class="text-xs text-app-muted mb-0.5">{{ $t("versions.diffTo") }}</p>
                  <p class="text-sm font-mono text-app-text">
                    {{ diffResult.backend_diff?.to ?? $t("versions.diffNoChange") }}
                  </p>
                </div>
              </div>
            </div>
          </template>
        </div>
      </template>
    </template>
  </section>
</template>
