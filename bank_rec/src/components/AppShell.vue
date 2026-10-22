<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted } from "vue";
import { RouterLink, RouterView, useRoute, useRouter } from "vue-router";
import { useBankRecStore } from "@/stores/bankRec";
import BookOpen from "~icons/lucide/book-open";
import CheckCheck from "~icons/lucide/check-check";
import CheckCircle2 from "~icons/lucide/check-circle-2";
import FileCheck2 from "~icons/lucide/file-check-2";
import History from "~icons/lucide/history";
import Upload from "~icons/lucide/upload";
import ListChecks from "~icons/lucide/list-checks";
import Scale from "~icons/lucide/scale";
import Table2 from "~icons/lucide/table-2";

const route = useRoute();
const router = useRouter();
const store = useBankRecStore();

function isActive(to: string) {
  return route.path === to || route.path.startsWith(`${to}/`);
}

const navItems = [
  {
    label: "Reconcile",
    to: "/reconcile",
    icon: CheckCircle2,
  },
  {
    label: "Import",
    to: "/import",
    icon: Upload,
  },
  {
    label: "Bank Coding",
    to: "/bank-coding",
    icon: Table2,
  },
  {
    label: "Matched",
    to: "/matched",
    icon: FileCheck2,
  },
  {
    label: "Rules",
    to: "/rules",
    icon: ListChecks,
  },
  {
    label: "History",
    to: "/history",
    icon: History,
  },
  {
    label: "Reports",
    to: "/reports",
    icon: Scale,
  },
];

// Ctrl/Cmd+Z opens the reconciliation history (ignored while typing)
function onKeydown(event: KeyboardEvent) {
  if (!(event.ctrlKey || event.metaKey) || event.shiftKey || event.key.toLowerCase() !== "z") {
    return;
  }
  const target = event.target as HTMLElement | null;
  if (target && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))) {
    return;
  }
  event.preventDefault();
  router.push({ path: "/history", query: route.query });
}

onMounted(() => window.addEventListener("keydown", onKeydown));
onBeforeUnmount(() => window.removeEventListener("keydown", onKeydown));

const activeTitle = computed(() => route.name?.toString() || "Reconcile");
const navbarLogo = computed(() => {
  const logo = store.boot?.settings.navbar_logo ?? window.settings?.navbar_logo;
  return typeof logo === "string" ? logo : "";
});
</script>

<template>
  <div class="flex min-h-screen flex-col bg-bank-surface text-bank-ink">
    <header
      class="sticky top-0 z-20 border-b border-bank-shell-line bg-bank-shell shadow-[0_1px_0_rgba(255,255,255,0.04),0_6px_18px_rgba(11,31,43,0.14)]"
    >
      <div
        class="mx-auto flex min-h-[64px] w-full max-w-[1920px] flex-col gap-3 px-4 py-3 md:flex-row md:items-center md:justify-between md:px-6 2xl:px-8"
      >
        <a
          href="/app"
          class="flex min-w-0 items-center gap-3 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-bank-shell-accent"
          title="Back to the ERP desk"
        >
          <img
            v-if="navbarLogo"
            :src="navbarLogo"
            alt="Bank Reco"
            class="h-8 w-auto max-w-[120px] shrink-0 object-contain sm:max-w-[160px]"
          />
          <div
            v-else
            class="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-white/15 bg-bank-shell-deep text-bank-shell-accent"
          >
            <CheckCheck class="h-5 w-5" />
          </div>
          <div class="min-w-0">
            <div class="text-xl font-semibold leading-7 tracking-[-0.015em] text-white">
              Bank Reco
            </div>
            <div class="truncate text-sm text-bank-shell-muted">
              {{ activeTitle }}
            </div>
          </div>
        </a>

        <nav
          class="bank-rec-nav-scrollbar flex max-w-full gap-1 overflow-x-auto rounded-lg border border-bank-shell-line bg-bank-shell-deep p-1"
          aria-label="Bank Reco sections"
        >
          <RouterLink
            v-for="item in navItems"
            :key="item.to"
            :to="item.to"
            class="flex h-9 shrink-0 items-center gap-2 rounded-md px-3 text-sm font-medium text-bank-shell-muted transition hover:bg-bank-shell-hover hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-bank-shell-accent"
            active-class="bg-bank-shell-active text-white shadow-sm"
          >
            <component
              :is="item.icon"
              class="h-4 w-4"
              :class="isActive(item.to) ? 'text-bank-shell-accent' : ''"
            />
            <span>{{ item.label }}</span>
          </RouterLink>
          <a
            href="/user-guide"
            target="_blank"
            rel="noopener"
            class="flex h-9 shrink-0 items-center gap-2 rounded-md px-3 text-sm font-medium text-bank-shell-muted transition hover:bg-bank-shell-hover hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-bank-shell-accent"
          >
            <BookOpen class="h-4 w-4" />
            <span>Guide</span>
          </a>
        </nav>
      </div>
    </header>

    <main class="mx-auto flex min-h-0 w-full max-w-[1920px] flex-1 px-3 py-4 sm:px-4 md:px-6 2xl:px-8">
      <RouterView />
    </main>
  </div>
</template>
