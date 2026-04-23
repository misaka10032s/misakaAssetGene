import { computed, onMounted, onUnmounted, readonly, ref } from "vue";

/**
 * Tracks the viewport size and exposes responsive utility classes for layouts.
 */
export function useWindowSize() {
  const width = ref(0);
  const height = ref(0);

  /**
   * Reads the most reliable viewport width during responsive design testing.
   */
  function updateWindowSize(): void {
    width.value = window.visualViewport?.width ?? window.innerWidth;
    height.value = window.visualViewport?.height ?? window.innerHeight;
  }

  const isMobile = computed<boolean>(() => width.value < 768);
  const isTablet = computed<boolean>(() => width.value >= 768 && width.value < 1024);
  const isDesktop = computed<boolean>(() => width.value >= 1024);
  const splitGridClass = computed<string>(() => (isDesktop.value ? "grid-cols-2" : "grid-cols-1"));
  const tripleGridClass = computed<string>(() => {
    if (width.value >= 1280) {
      return "grid-cols-3";
    }
    if (width.value >= 768) {
      return "grid-cols-2";
    }
    return "grid-cols-1";
  });

  onMounted(() => {
    updateWindowSize();
    window.addEventListener("resize", updateWindowSize);
    window.visualViewport?.addEventListener("resize", updateWindowSize);
  });

  onUnmounted(() => {
    window.removeEventListener("resize", updateWindowSize);
    window.visualViewport?.removeEventListener("resize", updateWindowSize);
  });

  return {
    width: readonly(width),
    height: readonly(height),
    isMobile: readonly(isMobile),
    isTablet: readonly(isTablet),
    isDesktop: readonly(isDesktop),
    splitGridClass: readonly(splitGridClass),
    tripleGridClass: readonly(tripleGridClass),
  };
}
