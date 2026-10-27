/**
 * 引擎偏好（用户自选）
 *
 * 架构口径（2026-10-22 修订）：
 * - ComfyUI 是默认推荐通道，`comfyui` / `h3` 走 ComfyUI 工作流；
 * - `h3_native` / `anime` / `wan22` 是本机进程内推理，作为兜底通道保留；
 * - **两者都对外开放，由用户自己选**。产品只负责标注通道与可用性，
 *   不替用户决定，也不把用户的选择悄悄改回默认值。
 *
 * 因此"当前选哪个引擎"是一个跨页面共享的用户偏好：
 * 在任意入口选过之后，其余入口（生成页 / 分镜弹窗 / 小说批量提交 / 快捷生成）
 * 都沿用同一个选择。偏好只在"该引擎当前不可用"时被临时绕过——不覆盖存储值，
 * 等引擎恢复后用户的选择自动生效。
 */
export const DEFAULT_ENGINE_ID = 'comfyui'

const PREF_KEY = 'aivideo:engine'

export function getEnginePref(fallback = DEFAULT_ENGINE_ID) {
  try {
    const saved = (localStorage.getItem(PREF_KEY) || '').trim()
    return saved || fallback
  } catch {
    return fallback
  }
}

export function setEnginePref(engineId) {
  if (!engineId) return
  try {
    localStorage.setItem(PREF_KEY, engineId)
  } catch {
    /* 隐私模式等场景下 localStorage 不可写，忽略即可，不影响本次会话 */
  }
}

/**
 * 在一份引擎列表里落定实际可用引擎。
 *
 * 返回 `{ engine, fallback }`：
 * - 偏好可用 → engine = 偏好，fallback = false
 * - 偏好不可用但列表里有可用引擎 → engine = 第一个可用，fallback = true
 * - 列表还没加载 / 全不可用 → 原样返回偏好，fallback = false（交给用户判断）
 */
export function resolveEngine(list, preferred = getEnginePref()) {
  if (!Array.isArray(list) || !list.length) return { engine: preferred, fallback: false }
  const hit = list.find((e) => e.id === preferred)
  if (hit && hit.status === 'ready') return { engine: preferred, fallback: false }
  const usable = list.find((e) => e.status === 'ready')
  if (usable) return { engine: usable.id, fallback: true }
  return { engine: preferred, fallback: false }
}

export function engineName(list, engineId) {
  const hit = (list || []).find((e) => e.id === engineId)
  return hit?.name || engineId
}
