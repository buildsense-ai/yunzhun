<script setup lang="ts">
import type { BoardColumn, BoardCard } from "~/composables/useApi";

const props = defineProps<{ column: BoardColumn }>();
defineEmits<{ select: [card: BoardCard] }>();

const accent: Record<string, string> = {
  pending: "text-bio-amber",
  identified: "text-bio-teal",
  downloaded: "text-bio-emerald",
  failed: "text-bio-rose",
  other: "text-bio-slate",
  analysis: "text-bio-lime",
  archived: "text-bio-lime",
};
</script>

<template>
  <section
    class="w-72 shrink-0 bio-card p-3"
    :class="column.disabled && 'opacity-40 pointer-events-none'"
  >
    <header class="flex items-center justify-between mb-3 px-1">
      <div class="flex items-center gap-2">
        <span class="w-2 h-2 rounded-full" :class="`bg-current ${accent[column.key]}`" />
        <h2 class="text-sm font-600 tracking-wide">{{ column.title }}</h2>
      </div>
      <span class="font-mono text-xs text-bio-dim">{{ column.count }}</span>
    </header>
    <p v-if="column.hint" class="text-[10px] text-bio-dim px-1 mb-2">{{ column.hint }}</p>

    <div class="space-y-2">
      <KanbanCard
        v-for="card in column.cards"
        :key="card.message_id"
        :card="card"
        :stage="column.key"
        @click="$emit('select', card)"
      />
      <p v-if="!column.cards.length && !column.disabled"
         class="text-center text-[11px] text-bio-dim py-6 font-mono">— empty —</p>
      <p v-if="column.disabled"
         class="text-center text-[11px] text-bio-dim py-6 font-mono">待下游接入</p>
    </div>
  </section>
</template>
