<template>
  <section class="app-panel">
    <h2 class="app-section-title">{{ $t("assets.title") }}</h2>
    <p class="app-muted">{{ $t("assets.intro") }}</p>
    <p class="mt-3 text-sm text-app-text">{{ $t("assets.explain") }}</p>
    <ul class="mt-4 grid gap-2 text-sm text-app-text">
      <li>{{ $t("assets.items.accepted") }}</li>
      <li>{{ $t("assets.items.dependency") }}</li>
      <li>{{ $t("assets.items.bundle") }}</li>
    </ul>
  </section>
</template>
