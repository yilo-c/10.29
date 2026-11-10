// 思忆集移动端布局补丁（服务端半身）
//
// 这个插件只做浏览器端的样式覆盖，服务端无需注册任何服务。
// 存在这个文件是因为 DSH 的客户端插件包必须有 main 入口，
// 且浏览器的客户端半身通过 exports["./client"] 单独加载。
export const name = 'siyi-mobile-layout'

/** 无服务端依赖 */
export const inject = []

/**
 * 服务端不做任何事：样式注入在浏览器半身完成。
 * 保留 apply 以符合插件契约。
 */
export function apply() {}
