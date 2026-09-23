<script setup lang="ts">
import type { BoardCard } from "~/composables/useApi";

const props = defineProps<{ card: BoardCard; stage: string }>();

const edge: Record<string, string> = {
  pending: "border-l-bio-amber",
  identified: "border-l-bio-teal",
  downloaded: "border-l-bio-emerald",
  failed: "border-l-bio-rose",
  other: "border-l-bio-slate",
};

const providerColor: Record<string, string> = {
  "aliyun-oss": "text-orange-300 border-orange-300/30",
  "tencent-cos": "text-sky-300 border-sky-300/30",
  "huawei-obs": "text-red-300 border-red-300/30",
  "aws-s3": "text-amber-300 border-amber-300/30",
  "s3-compatible": "text-amber-300 border-amber-300/30",
  "cloud-drive": "text-violet-300 border-violet-300/30",
};

const fmtDate = (d: string | null) =>
  d ? new Date(d).toLocaleDateString("zh-CN", { month: "short", day: "numeric" }) : "";
</script>

<template>
  <article
    class="bio-card border-l-2 p-3 cursor-pointer hover:border-bio-emerald/50 transition-colors"
    :class="edge[stage] || 'border-l-bio-edge'"
  >
    <h3 class="text-[13px] font-500 leading-snug line-clamp-2 mb-1">{{ card.subject || "(无主题)" }}</h3>
    <p class="text-[11px] text-bio-dim truncate mb-2">{{ card.from }} · {{ fmtDate(card.date) }}</p>

    <div class="flex flex-wrap gap-1 mb-2">
      <span v-for="(r, i) in card.refs.slice(0, 3)" :key="i"
            class="bio-chip" :class="providerColor[r.provider] || 'text-bio-dim border-bio-edge'">
        {{ r.provider }}
      </span>
      <span v-if="card.refs.length > 3" class="bio-chip text-bio-dim border-bio-edge">
        +{{ card.refs.length - 3 }}
      </span>
    </div>

    <div class="flex items-center justify-between text-[10px] font-mono text-bio-dim">
      <span v-if="card.storage_delivery != null">
        交付概率 {{ (card.storage_delivery * 100).toFixed(0) }}%
      </span>
      <span v-if="card.pull.done || card.pull.failed">
        <span class="text-bio-emerald">{{ card.pull.done }}✓</span>
        <span v-if="card.pull.failed" class="text-bio-rose ml-1">{{ card.pull.failed }}✗</span>
      </span>
    </div>
  </article>
</template>
