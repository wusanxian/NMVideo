<!-- 统一的线性图标集：侧栏与卡片按钮共用。
     自带 emoji 的问题不是好看不好看，是跨平台字形不一致、颜色不受控、
     跟文字基线对不齐，一屏里出现七八个就会显得零散。 -->
<template>
  <svg class="ico" :width="size" :height="size" viewBox="0 0 24 24" fill="none"
       stroke="currentColor" :stroke-width="strokeWidth" stroke-linecap="round"
       stroke-linejoin="round" aria-hidden="true">
    <path v-for="(d, i) in paths" :key="i" :d="d" />
  </svg>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  name: { type: String, required: true },
  size: { type: [Number, String], default: 16 },
  strokeWidth: { type: [Number, String], default: 1.7 },
})

const SET = {
  // 导航
  launch: ['M12 3v4', 'M12 13v8', 'M5 13h14', 'M7 21h10'],
  spark: ['M12 3l2.2 5.2L20 10l-4.4 3.6L16.4 20 12 17.2 7.6 20l.8-6.4L4 10l5.8-1.8z'],
  book: ['M4 5.5A2.5 2.5 0 016.5 3H19v15H6.5A2.5 2.5 0 004 20.5z', 'M19 18v3H6.5'],
  folder: ['M3 7.5A1.5 1.5 0 014.5 6H9l2 2.2h8.5A1.5 1.5 0 0121 9.7v8.8a1.5 1.5 0 01-1.5 1.5h-15A1.5 1.5 0 013 18.5z'],
  film: ['M3 5.5h18v13H3z', 'M3 9.5h18', 'M3 14.5h18', 'M8 5.5v13', 'M16 5.5v13'],
  users: ['M9 11a3.4 3.4 0 100-6.8A3.4 3.4 0 009 11z', 'M2.8 20a6.2 6.2 0 0112.4 0', 'M16.4 5.1a3.2 3.2 0 010 6.2', 'M18.2 20a5.6 5.6 0 00-2-4.3'],
  scissors: ['M6.5 6.5l11 11', 'M17.5 6.5l-11 11', 'M5.2 19.8a2.6 2.6 0 100-5.2 2.6 2.6 0 000 5.2z', 'M18.8 19.8a2.6 2.6 0 100-5.2 2.6 2.6 0 000 5.2z'],
  mic: ['M12 3.4a3 3 0 00-3 3v5.2a3 3 0 006 0V6.4a3 3 0 00-3-3z', 'M5.8 11.2a6.2 6.2 0 0012.4 0', 'M12 17.4V21'],
  sliders: ['M4 7h9', 'M17 7h3', 'M4 17h4', 'M12 17h8', 'M15 4.5v5', 'M10 14.5v5'],
  queue: ['M4 6.5h11', 'M4 12h11', 'M4 17.5h7', 'M18 15.6l1.8 1.8 3-3.6'],
  image: ['M3.5 5.5h17v13h-17z', 'M3.5 15l4.6-4.2 4 3.4 3.2-2.6 5.2 4.2', 'M8.6 9.6a1.3 1.3 0 100-2.6 1.3 1.3 0 000 2.6z'],
  // 操作
  plus: ['M12 5v14', 'M5 12h14'],
  trash: ['M4 7h16', 'M9.5 7V4.8h5V7', 'M6.5 7l.9 12.4a1.6 1.6 0 001.6 1.4h6a1.6 1.6 0 001.6-1.4L17.5 7'],
  close: ['M6 6l12 12', 'M18 6L6 18'],
  pencil: ['M4 20h4.2L20 8.2 15.8 4 4 15.8z'],
  play: ['M8.5 5.6l10 6.4-10 6.4z'],
  up: ['M12 19V5', 'M6 11l6-6 6 6'],
  down: ['M12 5v14', 'M18 13l-6 6-6-6'],
  chevron: ['M9 6l6 6-6 6'],
  check: ['M5 12.5l4.5 4.5L19 7'],
  clock: ['M12 4a8 8 0 100 16 8 8 0 000-16z', 'M12 8v4.4l3 1.8'],
  layers: ['M12 3l8 4.4-8 4.4L4 7.4z', 'M4 12.2l8 4.4 8-4.4', 'M4 16.8l8 4.4 8-4.4'],
  grid: ['M4 4h7v7H4z', 'M13 4h7v7h-7z', 'M4 13h7v7H4z', 'M13 13h7v7h-7z'],
  bolt: ['M13 3L5 13.6h5.4L11 21l8-10.6h-5.4z'],
  refresh: ['M4.6 12a7.4 7.4 0 0112.6-5.2', 'M17.6 3.4v4h-4', 'M19.4 12a7.4 7.4 0 01-12.6 5.2', 'M6.4 20.6v-4h4'],
  box: ['M12 3l8 4.2v9.6L12 21l-8-4.2V7.2z', 'M4 7.2l8 4.3 8-4.3', 'M12 11.5V21'],
  music: ['M9 18.4a2.4 2.4 0 100-4.8 2.4 2.4 0 000 4.8z', 'M11.4 16V5.6l8-1.8v9.8', 'M19.4 16.6a2.4 2.4 0 100-4.8 2.4 2.4 0 000 4.8z'],
  text: ['M4 6h16', 'M4 11h10', 'M4 16h13'],
}

const paths = computed(() => SET[props.name] || SET.spark)
</script>

<style scoped>
.ico { flex: none; display: block; }
</style>
