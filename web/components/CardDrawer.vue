<script setup lang="ts">
import type { BoardCard, PullRecord } from "~/composables/useApi";

const props = defineProps<{ card: BoardCard | null }>();
defineEmits<{ close: [] }>();

const api = useApi();
const pulls = ref<PullRecord[]>([]);

watch(() => props.card?.message_id, async (id) => {
  pulls.value = [];
  if (id != null) {
    try {
      pulls.value = await api<PullRecord[]>(`/v1/messages/${id}/pulls`);
    } catch { /* no records */ }
  }
});

const fmtBytes = (n: number) => {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB"];
  const i = Math.min(u.length - 1, Math.floor(Math.log2(n) / 10));
  return `${(n / 2 ** (10 * i)).toFixed(1)} ${u[i]}`;
};
</script>

<template>
  <Teleport to="body">
    <Transition name="slide">
      <aside v-if="card"
             class="fixed right-0 top-0 h-full w-96 bg-bio-panel border-l border-bio-edge p-5 overflow-y-auto z-50 shadow-2xl">
        <header class="flex items-start justify-between mb-4">
          <h2 class="text-sm font-600 leading-snug pr-3">{{ card.subject }}</h2>
          <button class="i-lucide-x text-bio-dim hover:text-bio-ink shrink-0" @click="$emit('close')" />
        </header>

        <dl class="text-xs space-y-2 mb-5">
          <div class="flex justify-between"><dt class="text-bio-dim">发件人</dt><dd>{{ card.from }}</dd></div>
          <div class="flex justify-between"><dt class="text-bio-dim">类别</dt>
            <dd class="font-mono">{{ card.category ?? "—" }}</dd></div>
          <div class="flex justify-between"><dt class="text-bio-dim">交付概率</dt>
            <dd class="font-mono text-bio-teal">
              {{ card.storage_delivery != null ? (card.storage_delivery * 100).toFixed(1) + "%" : "—" }}
            </dd></div>
        </dl>

        <h3 class="text-[11px] font-600 text-bio-dim uppercase tracking-wider mb-2">存储引用</h3>
        <ul class="space-y-1.5 mb-5">
          <li v-for="(r, i) in card.refs" :key="i"
              class="font-mono text-[11px] bg-bio-bg rounded p-2 border border-bio-edge break-all">
            <span class="text-bio-teal">{{ r.provider }}</span>
            <span class="text-bio-dim">://</span>{{ r.bucket }}<span class="text-bio-dim">/</span>{{ r.key }}
          </li>
          <li v-if="!card.refs.length" class="text-bio-dim text-[11px]">无</li>
        </ul>

        <h3 class="text-[11px] font-600 text-bio-dim uppercase tracking-wider mb-2">下载记录</h3>
        <ul class="space-y-1.5">
          <li v-for="p in pulls" :key="p.id"
              class="font-mono text-[11px] bg-bio-bg rounded p-2 border border-bio-edge">
            <div class="flex items-center gap-2">
              <span :class="p.status === 'done' ? 'i-lucide-check-circle-2 text-bio-emerald' : 'i-lucide-alert-circle text-bio-rose'" />
              <span class="truncate flex-1">{{ p.remote_key }}</span>
              <span class="text-bio-dim shrink-0">{{ fmtBytes(p.size) }}</span>
            </div>
            <p v-if="p.error" class="text-bio-rose mt-1 break-all">{{ p.error }}</p>
          </li>
          <li v-if="!pulls.length" class="text-bio-dim text-[11px]">暂无下载记录</li>
        </ul>
      </aside>
    </Transition>
  </Teleport>
</template>

<style scoped>
.slide-enter-active, .slide-leave-active { transition: transform .2s ease, opacity .2s; }
.slide-enter-from, .slide-leave-to { transform: translateX(100%); opacity: 0; }
</style>
