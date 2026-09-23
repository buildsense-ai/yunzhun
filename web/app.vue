<script setup lang="ts">
import type { BoardResponse, BoardCard } from "~/composables/useApi";

const api = useApi();
const board = ref<BoardResponse | null>(null);
const selected = ref<BoardCard | null>(null);
const lastRefresh = ref<Date | null>(null);

async function refresh() {
  try {
    board.value = await api<BoardResponse>("/v1/board");
    lastRefresh.value = new Date();
  } catch (e) {
    console.error("board fetch failed", e);
  }
}

let timer: ReturnType<typeof setInterval>;
onMounted(() => {
  refresh();
  timer = setInterval(refresh, 15_000);
});
onUnmounted(() => clearInterval(timer));

function fmtBytes(n: number): string {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(u.length - 1, Math.floor(Math.log2(n) / 10));
  return `${(n / 2 ** (10 * i)).toFixed(1)} ${u[i]}`;
}
</script>

<template>
  <div class="min-h-screen bg-bio-bg text-bio-ink font-sans bio-helix">
    <!-- top bar -->
    <header class="flex items-center justify-between px-6 py-4 border-b border-bio-edge">
      <div class="flex items-center gap-3">
        <div class="i-lucide-dna text-2xl text-bio-emerald" />
        <div>
          <h1 class="text-lg font-700 tracking-wide">Yunzhun <span class="text-bio-dim font-400">上游数据看板</span></h1>
          <p class="text-[11px] text-bio-dim font-mono">mail → judge → pull · event-driven</p>
        </div>
      </div>
      <div v-if="board" class="flex items-center gap-5 font-mono text-xs text-bio-dim">
        <span>邮件 <b class="text-bio-ink">{{ board.stats.messages }}</b></span>
        <span>交付 <b class="text-bio-teal">{{ board.stats.delivery }}</b></span>
        <span>文件 <b class="text-bio-emerald">{{ board.stats.files_done }}</b></span>
        <span v-if="board.stats.files_failed" class="text-bio-rose">失败 {{ board.stats.files_failed }}</span>
        <span>体积 <b class="text-bio-ink">{{ fmtBytes(board.stats.bytes) }}</b></span>
        <span v-if="lastRefresh" class="opacity-60">{{ lastRefresh.toLocaleTimeString() }}</span>
      </div>
    </header>

    <!-- board -->
    <main v-if="board" class="flex gap-4 p-6 overflow-x-auto items-start">
      <BoardColumn
        v-for="col in board.columns"
        :key="col.key"
        :column="col"
        @select="selected = $event"
      />
    </main>
    <main v-else class="flex items-center justify-center h-80 text-bio-dim font-mono text-sm">
      <span class="i-lucide-loader-2 animate-spin mr-2" /> connecting to gateway…
    </main>

    <CardDrawer :card="selected" @close="selected = null" />
  </div>
</template>

<style>
/* faint DNA-helix backdrop — two staggered sine dot columns */
.bio-helix {
  background-image:
    radial-gradient(circle at 25% 20%, rgba(52, 211, 153, 0.05), transparent 40%),
    radial-gradient(circle at 80% 80%, rgba(45, 212, 191, 0.04), transparent 40%),
    radial-gradient(rgba(52, 211, 153, 0.06) 1px, transparent 1px);
  background-size: 100% 100%, 100% 100%, 28px 28px;
}
</style>
