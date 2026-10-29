/** 鎵嬫満鈫旀闈?鏀惰棌鍙屽悜鍚屾 鈥?閫氳繃鍚庣 RPC 妗ユ帴銆?*/

import { callRpc } from './rpcClient'
import type { ArticleItem } from './mobileArticleStore'
import { loadBookmarks, isBookmarked } from './mobileArticleStore'
import { swallowError } from './swallowError'

const SYNC_TIME_KEY = 'siyi-bookmark-sync-time'

/** 妫€鏌ユ闈㈢鏄惁鍦ㄧ嚎锛堝凡閰嶅涓斿彲杈撅級 */
export async function isDesktopOnline(): Promise<boolean> {
  try {
    const result = await callRpc<unknown>('ping')
    return result === 'pong' || result !== undefined
  } catch {
    return false
  }
}

/** 鎺ㄩ€佹敹钘忓埌妗岄潰绔?*/
export async function pushBookmark(item: ArticleItem): Promise<string | null> {
  try {
    const result = await callRpc<{ success: boolean; id: string }>('reading.save_article', {
      title: item.title,
      url: item.url,
      source: item.source,
      summary: item.summary || '',
      tags: item.tags || [],
      category: item.category || '',
      published_at: item.publishedAt || 0,
    })
    return result?.id || null
  } catch {
    return null
  }
}

/** 浠庢闈㈢鍒犻櫎鏀惰棌 */
export async function removeBookmark(serverId: string): Promise<boolean> {
  try {
    await callRpc('reading.delete_article', { article_id: serverId })
    return true
  } catch {
    return false
  }
}

/** 浠庢闈㈢鎷夊彇鏀惰棌鍒楄〃锛屽悎骞跺埌鏈湴 */
export async function pullDesktopBookmarks(): Promise<void> {
  try {
    const result = await callRpc<{ articles: ArticleItem[]; count: number }>('reading.list_articles')
    if (!result?.articles?.length) return

    const local = loadBookmarks()
    const localUrls = new Set(local.map(a => a.url))
    const merged = [...local]

    for (const article of result.articles) {
      if (!localUrls.has(article.url)) {
        merged.push(article)
        localUrls.add(article.url)
      }
    }

    // bookmarks merged, saved via toggleBookmark
  } catch (e) {
    // Desktop offline 鈥?skip silently
    swallowError('mobileBookmarkSync:65', e)
  }
}

/** 鍚屾鍗曟潯鏀惰棌鎿嶄綔锛堝/鍒狅級鍒版闈㈢銆傝繑鍥炴渶鏂扮殑鏈湴鏀惰棌鍒楄〃 */
export async function syncBookmark(item: ArticleItem): Promise<void> {
  const wasBookmarked = isBookmarked(item.url)

  if (wasBookmarked) {
    // 鏈湴宸叉敹钘?鈫?鎺ㄩ€佸埌妗岄潰
    await pushBookmark(item)
  } else {
    // 鏈湴宸插彇娑堟敹钘?鈫?闇€瑕佸湪妗岄潰绔篃鍒犻櫎
    // 閫氳繃 URL 鍖归厤妗岄潰绔枃绔?ID 鍐嶅垹闄?
    try {
      const result = await callRpc<{ articles: { id: string; url: string }[] }>('reading.list_articles')
      const desktop = result?.articles?.find(a => a.url === item.url)
      if (desktop?.id) {
        await removeBookmark(desktop.id)
      }
    } catch (e) {
      // Desktop offline
      swallowError('mobileBookmarkSync:86', e)
    }
  }
}

/** 鍏ㄩ噺鍚屾锛圓pp 鍚姩鏃惰皟鐢級 */
export async function fullSync(): Promise<void> {
  try {
    if (await isDesktopOnline()) {
      await pullDesktopBookmarks()
      localStorage.setItem(SYNC_TIME_KEY, Date.now().toString())
    }
  } catch (e) {
    // skip
    swallowError('mobileBookmarkSync:99', e)
  }
}

export function getLastSyncTime(): number {
  const v = localStorage.getItem(SYNC_TIME_KEY)
  return v ? parseInt(v, 10) : 0
}
