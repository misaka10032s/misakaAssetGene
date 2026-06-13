<script setup lang="ts">
/**
 * InpaintMaskEditor — canvas-based inpaint mask painter (spec §5.11 / M5.9).
 *
 * Loads the source image onto a display canvas, overlays a semi-transparent
 * mask layer, and lets the user paint the edit region with brush or rectangle
 * tools.  A second hidden canvas (maskCanvas) always keeps the mask at the
 * original source resolution so the exported PNG is never downscaled.
 *
 * Mask format (spec §5.11 / comfyui.py:286-289):
 *   White (RGB 255,255,255) = region to regenerate (masked).
 *   Black (RGB 0,0,0)       = region to keep.
 *   Exported as PNG via toDataURL("image/png").
 *   The backend reads the red channel via LoadImageMask, so white-on-black is
 *   the correct convention (comfyui.py:288 "channel: red").
 *
 * Output resolution:
 *   maskCanvas is always sized to sourceWidth × sourceHeight.  The display
 *   canvas is CSS-scaled with object-fit to fit the container — all pointer
 *   events are mapped back to source space via a scale factor before painting
 *   maskCanvas, ensuring the exported mask matches the source image exactly.
 */

import {
  computed,
  onMounted,
  onUnmounted,
  ref,
  watch,
} from "vue";
import { useI18n } from "vue-i18n";
import { useWindowSize } from "@/composables/useWindowSize";

// ---------------------------------------------------------------------------
// Props & emits
// ---------------------------------------------------------------------------

const props = defineProps<{
  /** Asset id of the source image being inpainted. */
  assetId: string;
  /** URL to load the source image from (resolved by parent). */
  imageUrl: string;
  /** Whether the submit action is in progress (disables the button). */
  submitting?: boolean;
}>();

const emit = defineEmits<{
  /** Emitted when the user confirms; carries the mask PNG blob and inpaint prompt. */
  (event: "submit", payload: { maskBlob: Blob; prompt: string }): void;
  /** Emitted when the user closes/cancels the editor. */
  (event: "cancel"): void;
}>();

// ---------------------------------------------------------------------------
// i18n & RWD
// ---------------------------------------------------------------------------

const { t } = useI18n();
const { isMobile } = useWindowSize();

/** Canvas wrapper max-width: narrower on mobile, wider on desktop. */
const canvasWrapperClass = computed<string>(() =>
  isMobile.value ? "w-full max-w-[100%]" : "w-full max-w-3xl",
);

// ---------------------------------------------------------------------------
// Tool enum
// ---------------------------------------------------------------------------

/** Available mask painting tools. */
const enum MaskTool {
  BRUSH = "brush",
  ERASE = "erase",
  RECT = "rect",
  CLEAR = "clear",
}

// ---------------------------------------------------------------------------
// Reactive state
// ---------------------------------------------------------------------------

/** Active tool selection. */
const activeTool = ref<MaskTool>(MaskTool.BRUSH);

/** Brush radius in source-image pixels (before display scaling). */
const brushSize = ref<number>(32);

/** The inpaint prompt text. */
const prompt = ref<string>("");

/** Error shown when the user tries to submit with an empty mask. */
const emptyMaskError = ref<boolean>(false);

/** Whether source image has finished loading. */
const imageLoaded = ref<boolean>(false);

/** Whether the source image failed to load. */
const imageError = ref<boolean>(false);

/** Original image dimensions (source resolution). */
const sourceWidth = ref<number>(0);
const sourceHeight = ref<number>(0);

// ---------------------------------------------------------------------------
// Canvas refs
// ---------------------------------------------------------------------------

/**
 * Display canvas — the image + semi-transparent overlay are drawn here.
 * Sized to fit the container; does not need to match source resolution.
 */
const displayCanvas = ref<HTMLCanvasElement | null>(null);

/**
 * Off-screen mask canvas — always at source resolution.
 * Contains pure white-on-black mask data; never visible directly.
 */
const maskCanvas = ref<HTMLCanvasElement | null>(null);

// ---------------------------------------------------------------------------
// Internal drawing state (not reactive — mutated inside event handlers)
// ---------------------------------------------------------------------------

/** Scale factor: displayPx / sourcePx (updated on resize / image load). */
let displayScale = 1;

/** True while the user is holding the mouse button down. */
let isPointerDown = false;

/** Undo stack: each entry is a Uint8ClampedArray snapshot of maskCanvas pixels. */
const undoStack: Uint8ClampedArray[] = [];

/** Rectangle tool drag start (in source space). */
let rectStart: { x: number; y: number } | null = null;

/** Snapshot of mask at rect-drag start (so we preview without committing). */
let rectDragSnapshot: Uint8ClampedArray | null = null;

// The Image object used for loading the source.
const sourceImage = new Image();
sourceImage.crossOrigin = "anonymous";

// ---------------------------------------------------------------------------
// Scaling helpers
// ---------------------------------------------------------------------------

/**
 * Maps a CSS-space pointer position to source-image space.
 * @param clientX - Canvas-local X in CSS pixels.
 * @param clientY - Canvas-local Y in CSS pixels.
 */
function toSourceCoords(clientX: number, clientY: number): { x: number; y: number } {
  return {
    x: Math.round(clientX / displayScale),
    y: Math.round(clientY / displayScale),
  };
}

/**
 * Recalculates displayScale based on current display canvas dimensions.
 */
function updateDisplayScale(): void {
  const canvas = displayCanvas.value;
  if (!canvas || sourceWidth.value === 0) {
    return;
  }
  // The display canvas drawingbuffer matches the CSS pixel size.
  displayScale = canvas.width / sourceWidth.value;
}

// ---------------------------------------------------------------------------
// Mask canvas helpers
// ---------------------------------------------------------------------------

/**
 * Returns a snapshot of the current mask canvas pixels for undo.
 */
function snapshotMask(): Uint8ClampedArray {
  const ctx = maskCanvas.value?.getContext("2d");
  if (!ctx) {
    return new Uint8ClampedArray(0);
  }
  return ctx.getImageData(0, 0, sourceWidth.value, sourceHeight.value).data.slice();
}

/**
 * Paints a circular brush stroke on the mask canvas at source coordinates.
 * @param sx - Source X center.
 * @param sy - Source Y center.
 * @param erase - When true, paints black (erase); otherwise white (mask).
 */
function paintBrushMask(sx: number, sy: number, erase: boolean): void {
  const ctx = maskCanvas.value?.getContext("2d");
  if (!ctx) {
    return;
  }
  ctx.globalCompositeOperation = "source-over";
  ctx.fillStyle = erase ? "#000000" : "#ffffff";
  ctx.beginPath();
  ctx.arc(sx, sy, brushSize.value, 0, Math.PI * 2);
  ctx.fill();
}

/**
 * Fills a rectangular region on the mask canvas.
 * @param x1 - Top-left X (source).
 * @param y1 - Top-left Y (source).
 * @param x2 - Bottom-right X (source).
 * @param y2 - Bottom-right Y (source).
 * @param erase - When true, fills black; otherwise white.
 */
function paintRectMask(x1: number, y1: number, x2: number, y2: number, erase: boolean): void {
  const ctx = maskCanvas.value?.getContext("2d");
  if (!ctx) {
    return;
  }
  const left = Math.min(x1, x2);
  const top = Math.min(y1, y2);
  const w = Math.abs(x2 - x1);
  const h = Math.abs(y2 - y1);
  ctx.fillStyle = erase ? "#000000" : "#ffffff";
  ctx.fillRect(left, top, w, h);
}

/**
 * Clears the entire mask canvas to black (no masked region).
 */
function clearMask(): void {
  const ctx = maskCanvas.value?.getContext("2d");
  if (!ctx) {
    return;
  }
  ctx.clearRect(0, 0, sourceWidth.value, sourceHeight.value);
  ctx.fillStyle = "#000000";
  ctx.fillRect(0, 0, sourceWidth.value, sourceHeight.value);
}

/**
 * Returns true when the mask has any white pixels (i.e. region was painted).
 */
function maskHasContent(): boolean {
  const ctx = maskCanvas.value?.getContext("2d");
  if (!ctx) {
    return false;
  }
  const imageData = ctx.getImageData(0, 0, sourceWidth.value, sourceHeight.value);
  for (let i = 0; i < imageData.data.length; i += 4) {
    if (imageData.data[i] > 0) {
      return true;
    }
  }
  return false;
}

// ---------------------------------------------------------------------------
// Display (composite) rendering
// ---------------------------------------------------------------------------

/**
 * Composites the source image and the current mask onto the display canvas.
 * The mask overlay is drawn semi-transparent red over the image.
 */
function renderDisplay(): void {
  const canvas = displayCanvas.value;
  if (!canvas || !imageLoaded.value) {
    return;
  }
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    return;
  }
  const dw = canvas.width;
  const dh = canvas.height;

  // Draw source image.
  ctx.clearRect(0, 0, dw, dh);
  ctx.drawImage(sourceImage, 0, 0, dw, dh);

  // Draw mask overlay in semi-transparent red.
  if (maskCanvas.value) {
    // Temporarily tint maskCanvas pixels red using a temporary canvas.
    const tmp = document.createElement("canvas");
    tmp.width = dw;
    tmp.height = dh;
    const tmpCtx = tmp.getContext("2d");
    if (tmpCtx) {
      // Scale the mask canvas to display size.
      tmpCtx.drawImage(maskCanvas.value, 0, 0, dw, dh);
      // Tint: multiply by red.
      tmpCtx.globalCompositeOperation = "source-in";
      tmpCtx.fillStyle = "rgba(255, 60, 60, 0.55)";
      tmpCtx.fillRect(0, 0, dw, dh);
    }
    ctx.drawImage(tmp, 0, 0);
  }

  // If currently dragging a rect preview, draw it on display too.
  if (activeTool.value === MaskTool.RECT && rectStart !== null && currentDisplayDrag !== null) {
    const { x: dx1, y: dy1 } = toDisplayCoords(rectStart.x, rectStart.y);
    ctx.strokeStyle = "rgba(255, 60, 60, 0.9)";
    ctx.lineWidth = 2;
    ctx.setLineDash([6, 3]);
    const dx2 = currentDisplayDrag.x;
    const dy2 = currentDisplayDrag.y;
    ctx.strokeRect(
      Math.min(dx1, dx2),
      Math.min(dy1, dy2),
      Math.abs(dx2 - dx1),
      Math.abs(dy2 - dy1),
    );
    ctx.setLineDash([]);
  }
}

/** Converts source-space coords back to display-canvas space (for rect preview). */
function toDisplayCoords(sx: number, sy: number): { x: number; y: number } {
  return { x: Math.round(sx * displayScale), y: Math.round(sy * displayScale) };
}

/** Current display-space pointer position during a rect drag (for preview outline). */
let currentDisplayDrag: { x: number; y: number } | null = null;

// ---------------------------------------------------------------------------
// Resize observer
// ---------------------------------------------------------------------------

let resizeObserver: ResizeObserver | null = null;

/**
 * Resizes the display canvas to match its CSS layout size and re-renders.
 * Called when the container size changes.
 */
function onContainerResized(): void {
  const canvas = displayCanvas.value;
  if (!canvas || sourceWidth.value === 0) {
    return;
  }
  // Fit the canvas into the container while preserving source aspect ratio.
  const containerWidth = canvas.parentElement?.clientWidth ?? canvas.clientWidth;
  const aspect = sourceHeight.value / sourceWidth.value;
  canvas.width = containerWidth;
  canvas.height = Math.round(containerWidth * aspect);
  updateDisplayScale();
  renderDisplay();
}

// ---------------------------------------------------------------------------
// Image loading
// ---------------------------------------------------------------------------

/**
 * Loads the source image and initialises both canvases.
 */
function loadImage(): void {
  imageLoaded.value = false;
  imageError.value = false;
  sourceImage.onload = () => {
    sourceWidth.value = sourceImage.naturalWidth;
    sourceHeight.value = sourceImage.naturalHeight;

    // Initialize off-screen mask canvas at source resolution.
    const mc = maskCanvas.value;
    if (mc) {
      mc.width = sourceWidth.value;
      mc.height = sourceHeight.value;
      clearMask();
    }

    // Initialise display canvas, then fit.
    imageLoaded.value = true;
    onContainerResized();
  };
  sourceImage.onerror = () => {
    imageError.value = true;
  };
  sourceImage.src = props.imageUrl;
}

// ---------------------------------------------------------------------------
// Pointer event handlers
// ---------------------------------------------------------------------------

/**
 * Extracts the canvas-local (CSS pixel) coordinates from a pointer/mouse event.
 */
function eventToCanvasLocal(event: MouseEvent | PointerEvent): { x: number; y: number } {
  const canvas = displayCanvas.value;
  if (!canvas) {
    return { x: 0, y: 0 };
  }
  const rect = canvas.getBoundingClientRect();
  return {
    x: event.clientX - rect.left,
    y: event.clientY - rect.top,
  };
}

function onPointerDown(event: PointerEvent): void {
  if (!imageLoaded.value) {
    return;
  }
  const { x: cx, y: cy } = eventToCanvasLocal(event);
  const { x: sx, y: sy } = toSourceCoords(cx, cy);

  if (activeTool.value === MaskTool.CLEAR) {
    undoStack.push(snapshotMask());
    clearMask();
    renderDisplay();
    return;
  }

  isPointerDown = true;
  undoStack.push(snapshotMask());
  emptyMaskError.value = false;

  if (activeTool.value === MaskTool.BRUSH || activeTool.value === MaskTool.ERASE) {
    paintBrushMask(sx, sy, activeTool.value === MaskTool.ERASE);
    renderDisplay();
  } else if (activeTool.value === MaskTool.RECT) {
    rectStart = { x: sx, y: sy };
    rectDragSnapshot = snapshotMask();
    currentDisplayDrag = { x: cx, y: cy };
  }

  (event.target as HTMLElement).setPointerCapture?.(event.pointerId);
}

function onPointerMove(event: PointerEvent): void {
  if (!isPointerDown || !imageLoaded.value) {
    return;
  }
  const { x: cx, y: cy } = eventToCanvasLocal(event);
  const { x: sx, y: sy } = toSourceCoords(cx, cy);

  if (activeTool.value === MaskTool.BRUSH || activeTool.value === MaskTool.ERASE) {
    paintBrushMask(sx, sy, activeTool.value === MaskTool.ERASE);
    renderDisplay();
  } else if (activeTool.value === MaskTool.RECT && rectStart !== null && rectDragSnapshot !== null) {
    // Restore snapshot then preview the current rect.
    const ctx = maskCanvas.value?.getContext("2d");
    if (ctx) {
      const imgData = ctx.createImageData(sourceWidth.value, sourceHeight.value);
      imgData.data.set(rectDragSnapshot);
      ctx.putImageData(imgData, 0, 0);
    }
    paintRectMask(rectStart.x, rectStart.y, sx, sy, false);
    currentDisplayDrag = { x: cx, y: cy };
    renderDisplay();
  }
}

function onPointerUp(event: PointerEvent): void {
  if (!isPointerDown || !imageLoaded.value) {
    return;
  }
  isPointerDown = false;
  rectStart = null;
  rectDragSnapshot = null;
  currentDisplayDrag = null;
  renderDisplay();
}

// ---------------------------------------------------------------------------
// Undo
// ---------------------------------------------------------------------------

/**
 * Reverts the mask canvas to the state before the last stroke.
 */
function undoLastStroke(): void {
  const snapshot = undoStack.pop();
  if (!snapshot) {
    return;
  }
  const ctx = maskCanvas.value?.getContext("2d");
  if (!ctx) {
    return;
  }
  const imgData = ctx.createImageData(sourceWidth.value, sourceHeight.value);
  imgData.data.set(snapshot);
  ctx.putImageData(imgData, 0, 0);
  renderDisplay();
}

// ---------------------------------------------------------------------------
// Submit
// ---------------------------------------------------------------------------

/**
 * Exports the mask canvas as a PNG blob and emits the submit event.
 * Validates that the mask is non-empty (at least one white pixel).
 */
async function submitInpaint(): Promise<void> {
  if (!maskCanvas.value) {
    return;
  }
  if (!maskHasContent()) {
    emptyMaskError.value = true;
    return;
  }
  emptyMaskError.value = false;

  // Export mask as PNG blob.
  const blob = await new Promise<Blob | null>((resolve) => {
    maskCanvas.value!.toBlob(resolve, "image/png");
  });
  if (!blob) {
    return;
  }
  emit("submit", { maskBlob: blob, prompt: prompt.value });
}

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

onMounted(() => {
  loadImage();

  // Observe container size changes and re-fit the display canvas.
  if (displayCanvas.value?.parentElement) {
    resizeObserver = new ResizeObserver(onContainerResized);
    resizeObserver.observe(displayCanvas.value.parentElement);
  }
});

onUnmounted(() => {
  resizeObserver?.disconnect();
  sourceImage.onload = null;
  sourceImage.onerror = null;
});

watch(() => props.imageUrl, () => {
  undoStack.length = 0;
  loadImage();
});
</script>

<template>
  <!-- Modal overlay -->
  <div class="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/70 backdrop-blur-sm px-4 py-8">
    <div class="w-full max-w-3xl rounded-[28px] border border-app-border bg-app-surface p-6 shadow-2xl shadow-black/40">

      <!-- Header -->
      <div class="mb-4 flex items-center justify-between gap-4">
        <div>
          <h2 class="app-section-title">{{ t("inpaint.editorTitle") }}</h2>
          <p class="mt-1 text-sm text-app-muted">{{ t("inpaint.editorHint") }}</p>
        </div>
        <button
          class="app-button-secondary shrink-0"
          type="button"
          @click="$emit('cancel')"
        >
          {{ t("app.close") }}
        </button>
      </div>

      <!-- Tool bar -->
      <div class="mb-3 flex flex-wrap items-center gap-2">
        <!-- Brush -->
        <button
          type="button"
          class="app-button-secondary text-sm"
          :class="activeTool === 'brush' ? 'ring-2 ring-app-primary' : ''"
          @click="activeTool = 'brush' as MaskTool"
        >
          {{ t("inpaint.toolBrush") }}
        </button>
        <!-- Erase -->
        <button
          type="button"
          class="app-button-secondary text-sm"
          :class="activeTool === 'erase' ? 'ring-2 ring-app-primary' : ''"
          @click="activeTool = 'erase' as MaskTool"
        >
          {{ t("inpaint.toolErase") }}
        </button>
        <!-- Rect -->
        <button
          type="button"
          class="app-button-secondary text-sm"
          :class="activeTool === 'rect' ? 'ring-2 ring-app-primary' : ''"
          @click="activeTool = 'rect' as MaskTool"
        >
          {{ t("inpaint.toolRect") }}
        </button>
        <!-- Clear all -->
        <button
          type="button"
          class="app-button-secondary text-sm"
          @click="activeTool = 'clear' as MaskTool; onPointerDown($event as unknown as PointerEvent)"
        >
          {{ t("inpaint.toolClear") }}
        </button>
        <!-- Undo -->
        <button
          type="button"
          class="app-button-secondary text-sm"
          :disabled="undoStack.length === 0"
          @click="undoLastStroke"
        >
          {{ t("inpaint.undoAction") }}
        </button>

        <!-- Brush size slider (only for brush / erase) -->
        <label
          v-if="activeTool === 'brush' || activeTool === 'erase'"
          class="ml-auto flex items-center gap-2 text-sm text-app-text"
        >
          <span class="app-muted">{{ t("inpaint.brushSize") }}</span>
          <input
            v-model.number="brushSize"
            type="range"
            min="4"
            max="128"
            step="4"
            class="w-28 accent-app-primary"
          />
          <span class="w-8 text-right tabular-nums text-app-muted">{{ brushSize }}</span>
        </label>
      </div>

      <!-- Canvas area -->
      <div :class="canvasWrapperClass" class="relative mx-auto overflow-hidden rounded-2xl border border-app-border bg-black">
        <!-- Loading spinner -->
        <div v-if="!imageLoaded && !imageError" class="flex h-48 items-center justify-center text-sm text-app-muted">
          <span class="inline-block h-5 w-5 animate-spin rounded-full border-2 border-app-primary border-t-transparent mr-2"></span>
          {{ t("inpaint.imageLoading") }}
        </div>

        <!-- Load error -->
        <div v-else-if="imageError" class="flex h-48 items-center justify-center text-sm text-app-warning">
          {{ t("inpaint.imageLoadError") }}
        </div>

        <!-- Display canvas (composite: source image + mask overlay) -->
        <canvas
          v-show="imageLoaded"
          ref="displayCanvas"
          class="block w-full cursor-crosshair touch-none"
          style="image-rendering: auto;"
          @pointerdown="onPointerDown"
          @pointermove="onPointerMove"
          @pointerup="onPointerUp"
          @pointerleave="onPointerUp"
        />
      </div>

      <!-- Off-screen mask canvas (source resolution, hidden from user) -->
      <canvas ref="maskCanvas" class="hidden" aria-hidden="true" />

      <!-- Empty mask warning -->
      <p
        v-if="emptyMaskError"
        class="mt-2 text-sm text-app-warning"
      >
        {{ t("inpaint.emptyMaskWarning") }}
      </p>

      <!-- Inpaint prompt input -->
      <div class="mt-4 grid gap-2">
        <label class="grid gap-1 text-sm text-app-text">
          <span class="app-muted">{{ t("inpaint.promptLabel") }}</span>
          <input
            v-model="prompt"
            type="text"
            class="app-input"
            :placeholder="t('inpaint.promptPlaceholder')"
          />
        </label>
      </div>

      <!-- Action buttons -->
      <div class="mt-5 flex flex-wrap justify-end gap-3">
        <button
          type="button"
          class="app-button-secondary"
          @click="$emit('cancel')"
        >
          {{ t("inpaint.cancelAction") }}
        </button>
        <button
          type="button"
          class="app-button"
          :disabled="!imageLoaded || submitting"
          @click="submitInpaint"
        >
          {{ submitting ? t("inpaint.submittingAction") : t("inpaint.submitAction") }}
        </button>
      </div>

    </div>
  </div>
</template>
