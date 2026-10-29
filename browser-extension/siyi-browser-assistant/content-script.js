const GENERIC_READABLE_SELECTORS = ['article', 'main', '[role="main"]', '.article', '.post', '.content'];
const BILIBILI_DESCRIPTION_SELECTORS = ['#v_desc', '.video-desc-container', '.desc-info-text', '.video-info-detail', '.desc-info'];
const BILIBILI_SUBTITLE_PANEL_SELECTORS = '.bpx-player-subtitle-panel, .subtitle-wrap, .bcc-subtitle-wrap';
const BILIBILI_SUBTITLE_LINE_SELECTORS = '.bpx-player-subtitle-text, .subtitle-item, .caption-line, .cue, [role="textbox"]';
const BILIBILI_SUBTITLE_URL_PATTERN = /subtitle_url["']?\s*[:=]\s*["']([^"']+)["']/i;

const SITE_ADAPTERS = [
  {
    name: 'bilibili',
    match: (loc) => String(loc?.hostname || '').includes('bilibili.com'),
    selectors: [...BILIBILI_DESCRIPTION_SELECTORS],
    mediaHints: (doc) => ({
      hasVideo: Boolean(doc.querySelector('video')),
      hasSubtitle: Boolean(doc.querySelector(BILIBILI_SUBTITLE_PANEL_SELECTORS)),
      hasDescription: Boolean(doc.querySelector(BILIBILI_DESCRIPTION_SELECTORS.join(', '))),
      site: 'bilibili'
    }),
    contentKind: () => 'video_page'
  },
  {
    name: 'zhihu',
    match: (loc) => String(loc?.hostname || '').includes('zhihu.com'),
    selectors: ['.Post-RichText', '.RichText.ztext', '.QuestionRichText', '.ContentItem-RichText'],
    mediaHints: (doc) => ({
      hasQuestionHeader: Boolean(doc.querySelector('.QuestionHeader, .Post-Header')),
      site: 'zhihu'
    })
  },
  {
    name: 'wechat',
    match: (loc) => String(loc?.hostname || '').includes('mp.weixin.qq.com'),
    selectors: ['#js_content', '.rich_media_content', '.rich_media_area_primary_inner'],
    mediaHints: (doc) => ({
      hasAuthorBlock: Boolean(doc.querySelector('#js_name, .rich_media_meta_list')),
      site: 'wechat'
    })
  },
  {
    name: 'csdn',
    match: (loc) => String(loc?.hostname || '').includes('csdn.net'),
    selectors: ['#content_views', 'main .blog-content-box', '.article_content', '.blog-content-box'],
    mediaHints: (doc) => ({
      hasCodeBlocks: doc.querySelectorAll('pre, code').length >= 3,
      site: 'csdn'
    })
  },
  {
    name: 'wenku',
    match: (loc) => String(loc?.hostname || '').includes('wenku.baidu.com') || String(loc?.hostname || '').includes('docin.com'),
    selectors: ['.reader-word-layer', '.bd.doc-reader', '.doc-reader', '.inner-page'],
    mediaHints: (doc) => ({
      hasDocumentContainer: Boolean(doc.querySelector('.reader-word-layer, .doc-reader, .inner-page')),
      site: 'wenku'
    }),
    contentKind: () => 'document_page'
  }
];

function cleanText(text) {
  return String(text || '').replace(/\n{3,}/g, '\n\n').trim();
}

// Remove bilibili UI noise from description/page text
const BILIBILI_UI_NOISE_PATTERNS = [
  /^展开更多$/gm,
  /^收起$/gm,
  /一键三连/g,
  /【长按点赞】/g,
  /^已关注$/gm,
  /^关注\s*[\d.]+万?$/gm,
  /^\d+人正在看/gm,
  /^已(装填|关闭)弹幕$/gm,
  /点赞\s*\d[\d.]*[万]?/g,
  /投币\s*\d[\d.]*[万]?/g,
  /收藏\s*\d[\d.]*[万]?/g,
  /转发\s*\d[\d.]*[万]?/g,
  /^稿件举报$/gm,
  /^笔记$/gm,
];

function cleanBilibiliText(text) {
  let value = cleanText(text);
  for (const pattern of BILIBILI_UI_NOISE_PATTERNS) {
    value = value.replace(pattern, '');
  }
  return value.replace(/\n{3,}/g, '\n\n').trim();
}

function getElementText(element) {
  return cleanText(element?.innerText || element?.textContent || '');
}

function pickFirstTextElement(doc, selectors, minLength = 20) {
  for (const selector of selectors) {
    const element = doc.querySelector(selector);
    const text = getElementText(element);
    if (text.length >= minLength) {
      return { element, selector, text };
    }
  }
  return { element: null, selector: '', text: '' };
}

function extractDescriptionFromBilibili(doc) {
  const result = pickFirstTextElement(doc, BILIBILI_DESCRIPTION_SELECTORS, 20);
  if (result.text) {
    result.text = cleanBilibiliText(result.text);
  }
  return result;
}

function extractSubtitleSnapshotFromBilibili(doc) {
  try {
    const subtitlePanel = doc.querySelector(BILIBILI_SUBTITLE_PANEL_SELECTORS);
    if (!subtitlePanel) {
      return { text: '', status: 'absent', hasContainer: false, hasText: false };
    }

    const subtitleElements = subtitlePanel.querySelectorAll(BILIBILI_SUBTITLE_LINE_SELECTORS);
    const lines = Array.from(subtitleElements)
      .map((el) => getElementText(el))
      .filter((line) => line.length > 0);

    if (lines.length > 0) {
      return {
        text: cleanText(lines.join('\n')),
        status: 'text_ready',
        hasContainer: true,
        hasText: true,
      };
    }

    const panelText = getElementText(subtitlePanel);
    if (panelText) {
      return {
        text: panelText,
        status: 'text_ready',
        hasContainer: true,
        hasText: true,
      };
    }

    return { text: '', status: 'container_only', hasContainer: true, hasText: false };
  } catch (_error) {
    return { text: '', status: 'unknown_error', hasContainer: false, hasText: false };
  }
}

function normalizeBilibiliSubtitleUrl(rawUrl) {
  const value = String(rawUrl || '').trim();
  if (!value) {
    return '';
  }
  const unescaped = value
    .replace(/\\u002F/g, '/')
    .replace(/\\\//g, '/')
    .replace(/&amp;/g, '&');
  if (unescaped.startsWith('//')) {
    return `https:${unescaped}`;
  }
  return unescaped;
}

function collectScriptTexts(doc) {
  try {
    return Array.from(doc.querySelectorAll('script'))
      .map((node) => String(node?.textContent || node?.innerText || '').trim())
      .filter(Boolean);
  } catch (_error) {
    return [];
  }
}

function extractBilibiliSubtitleUrlsFromScripts(doc) {
  const scriptTexts = collectScriptTexts(doc);
  const urls = [];
  const pattern = new RegExp(BILIBILI_SUBTITLE_URL_PATTERN.source, 'gi');
  for (const scriptText of scriptTexts) {
    let match = null;
    pattern.lastIndex = 0;
    while ((match = pattern.exec(scriptText)) !== null) {
      const normalized = normalizeBilibiliSubtitleUrl(match[1]);
      if (normalized) {
        urls.push(normalized);
      }
    }
  }
  return Array.from(new Set(urls));
}

function normalizeSubtitleBodyToText(payload) {
  const body = Array.isArray(payload?.body) ? payload.body : [];
  const lines = body
    .map((item) => cleanText(item?.content || item?.text || ''))
    .filter(Boolean);
  return cleanText(lines.join('\n'));
}

async function fetchSubtitleTextFromUrls(urls) {
  if (typeof chrome !== 'undefined' && chrome.runtime?.sendMessage) {
    try {
      const bgResult = await chrome.runtime.sendMessage({ type: 'FETCH_SUBTITLE_JSON', urls });
      if (bgResult?.ok && bgResult.text) {
        return { text: bgResult.text, sourceUrl: bgResult.sourceUrl || '' };
      }
    } catch (_bgError) {
      // fall through to direct fetch
    }
  }
  for (const url of urls) {
    try {
      const response = await fetch(url, { credentials: 'include' });
      if (!response.ok) {
        continue;
      }
      const payload = await response.json();
      const text = normalizeSubtitleBodyToText(payload);
      if (text) {
        return { text, sourceUrl: url };
      }
    } catch (_error) {
      // ignore fetch failures and try next url
    }
  }
  return { text: '', sourceUrl: '' };
}

function extractBilibiliVideoIds(loc, doc) {
  const href = String(loc?.href || '');
  const bvidMatch = href.match(/\/video\/(BV[\w]+)/i);
  const bvid = bvidMatch ? bvidMatch[1] : '';
  let cid = '';

  if (doc) {
    // Strategy 1: Scan <script> tags for cid patterns
    try {
      const scripts = doc.querySelectorAll ? doc.querySelectorAll('script') : [];
      for (const script of scripts) {
        const text = String(script?.textContent || script?.innerText || '');
        if (!text || text.length < 10) continue;
        const cidMatch = text.match(/"cid"\s*[:=]\s*(\d+)/);
        if (cidMatch) {
          cid = cidMatch[1];
          break;
        }
      }
    } catch (_e) { /* ignore */ }

    // Strategy 2: Inject into page world to read window.__INITIAL_STATE__
    // (handles SPA navigation where <script> tags are stale)
    if (!cid && doc.createElement && doc.documentElement?.appendChild) {
      try {
        const sc = doc.createElement('script');
        sc.textContent = 'try{var _s=window.__INITIAL_STATE__||{};var _v=_s.videoData||_s;document.documentElement.setAttribute("data-siyi-cid",String(_v.cid||(_v.pages&&_v.pages[0]&&_v.pages[0].cid)||(_s.epInfo&&_s.epInfo.cid)||"")}catch(e){}';
        doc.documentElement.appendChild(sc);
        sc.remove();
        const injectedCid = doc.documentElement.getAttribute('data-siyi-cid') || '';
        doc.documentElement.removeAttribute('data-siyi-cid');
        if (injectedCid && /^\d+$/.test(injectedCid)) {
          cid = injectedCid;
        }
      } catch (_e) { /* injection not supported (test env / CSP) */ }
    }
  }

  // Strategy 3: URL query parameters
  if (!cid) {
    try {
      const url = new URL(href);
      const urlCid = url.searchParams.get('cid');
      if (urlCid && /^\d+$/.test(urlCid)) {
        cid = urlCid;
      }
    } catch (_e) { /* ignore */ }
  }

  return { bvid, cid };
}

async function requestSubtitleViaBackground(bvid, cid) {
  if (typeof chrome === 'undefined' || !chrome.runtime?.sendMessage) {
    return { ok: false, error: 'no_chrome_runtime', text: '' };
  }
  try {
    return await chrome.runtime.sendMessage({
      type: 'FETCH_BILIBILI_SUBTITLE',
      bvid,
      cid,
    });
  } catch (error) {
    return { ok: false, error: String(error?.message || error), text: '' };
  }
}

async function extractSubtitleSnapshotFromBilibiliAsync(doc, loc) {
  const domSnapshot = extractSubtitleSnapshotFromBilibili(doc);
  if (domSnapshot.text) {
    return { ...domSnapshot, fetchMethod: 'dom' };
  }

  let apiFetchError = '';

  // Primary path: Player API via background relay
  // (background auto-resolves cid from View API when not provided)
  const { bvid, cid } = extractBilibiliVideoIds(loc || {}, doc);
  if (bvid) {
    const apiResult = await requestSubtitleViaBackground(bvid, cid);
    if (apiResult?.ok && apiResult.text) {
      return {
        text: cleanText(apiResult.text),
        status: 'api_text_ready',
        hasContainer: domSnapshot.hasContainer,
        hasText: true,
        sourceUrl: apiResult.subtitleUrl || '',
        fetchMethod: 'player_api',
        subtitleLan: apiResult.lan || '',
      };
    }
    if (apiResult?.ok && apiResult.status === 'no_subtitle_available') {
      return {
        text: '',
        status: 'api_confirmed_none',
        hasContainer: domSnapshot.hasContainer,
        hasText: false,
        fetchMethod: 'player_api',
        fetchError: '',
      };
    }
    if (!apiResult?.ok) {
      apiFetchError = apiResult?.error || 'unknown_api_error';
    }
  }

  // Fallback: script tag scanning (legacy)
  const subtitleUrls = extractBilibiliSubtitleUrlsFromScripts(doc);
  if (subtitleUrls.length) {
    const fetched = await fetchSubtitleTextFromUrls(subtitleUrls);
    if (fetched.text) {
      return {
        text: fetched.text,
        status: 'script_tag_text_ready',
        hasContainer: domSnapshot.hasContainer,
        hasText: true,
        sourceUrl: fetched.sourceUrl,
        fetchMethod: 'script_tag',
      };
    }
  }

  return {
    ...domSnapshot,
    status: domSnapshot.hasContainer ? domSnapshot.status : (bvid ? 'api_no_text' : 'absent'),
    fetchMethod: 'none',
    fetchError: apiFetchError,
  };
}

function extractSubtitleTextFromBilibili(doc) {
  return extractSubtitleSnapshotFromBilibili(doc).text;
}

async function extractSubtitleTextFromBilibiliAsync(doc, loc) {
  const snapshot = await extractSubtitleSnapshotFromBilibiliAsync(doc, loc);
  return snapshot.text;
}

function extractSubtitleSnapshot(doc, adapter) {
  if (!adapter) {
    return { text: '', status: 'absent', hasContainer: false, hasText: false };
  }
  if (adapter.name === 'bilibili') {
    return extractSubtitleSnapshotFromBilibili(doc);
  }

  try {
    const subtitleSelectors = [
      '.subtitle-text',
      '.caption',
      '.bpx-player-subtitle-text',
      '[role="textbox"]',
      '.subtitle-wrap [role="textbox"]'
    ];

    for (const selector of subtitleSelectors) {
      const elements = doc.querySelectorAll(selector);
      if (elements.length > 0) {
        const lines = Array.from(elements)
          .map((el) => getElementText(el))
          .filter((line) => line.length > 0);
        if (lines.length > 0) {
          return {
            text: cleanText(lines.join('\n')),
            status: 'text_ready',
            hasContainer: true,
            hasText: true,
          };
        }
      }
    }
  } catch (_error) {
    return { text: '', status: 'unknown_error', hasContainer: false, hasText: false };
  }

  return { text: '', status: 'absent', hasContainer: false, hasText: false };
}

async function extractSubtitleSnapshotAsync(doc, adapter, loc) {
  if (!adapter) {
    return { text: '', status: 'absent', hasContainer: false, hasText: false };
  }
  if (adapter.name === 'bilibili') {
    return extractSubtitleSnapshotFromBilibiliAsync(doc, loc);
  }
  return extractSubtitleSnapshot(doc, adapter);
}

function extractSubtitleText(doc, adapter) {
  return extractSubtitleSnapshot(doc, adapter).text;
}

function findSiteAdapterForLocation(loc, adapters = SITE_ADAPTERS) {
  return adapters.find((adapter) => {
    try {
      return adapter.match(loc);
    } catch (_error) {
      return false;
    }
  }) || null;
}

function collectStructuredSignals(doc, adapter) {
  const subtitle = extractSubtitleSnapshot(doc, adapter);
  if (adapter?.name === 'bilibili') {
    const description = extractDescriptionFromBilibili(doc);
    return {
      descriptionElement: description.element,
      descriptionSelector: description.selector,
      descriptionText: description.text,
      subtitleText: subtitle.text,
      subtitleStatus: subtitle.status,
      hasSubtitleContainer: subtitle.hasContainer,
      hasSubtitleText: subtitle.hasText,
    };
  }
  return {
    descriptionElement: null,
    descriptionSelector: '',
    descriptionText: '',
    subtitleText: subtitle.text,
    subtitleStatus: subtitle.status,
    hasSubtitleContainer: subtitle.hasContainer,
    hasSubtitleText: subtitle.hasText,
  };
}

async function collectStructuredSignalsAsync(doc, adapter, loc) {
  const subtitle = await extractSubtitleSnapshotAsync(doc, adapter, loc);
  if (adapter?.name === 'bilibili') {
    const description = extractDescriptionFromBilibili(doc);
    return {
      descriptionElement: description.element,
      descriptionSelector: description.selector,
      descriptionText: description.text,
      subtitleText: subtitle.text,
      subtitleStatus: subtitle.status,
      subtitleSourceUrl: subtitle.sourceUrl || '',
      subtitleFetchMethod: subtitle.fetchMethod || '',
      subtitleFetchError: subtitle.fetchError || '',
      subtitleLan: subtitle.subtitleLan || '',
      hasSubtitleContainer: subtitle.hasContainer,
      hasSubtitleText: subtitle.hasText,
    };
  }
  return {
    descriptionElement: null,
    descriptionSelector: '',
    descriptionText: '',
    subtitleText: subtitle.text,
    subtitleStatus: subtitle.status,
    subtitleSourceUrl: subtitle.sourceUrl || '',
    subtitleFetchMethod: subtitle.fetchMethod || '',
    subtitleFetchError: subtitle.fetchError || '',
    subtitleLan: subtitle.subtitleLan || '',
    hasSubtitleContainer: subtitle.hasContainer,
    hasSubtitleText: subtitle.hasText,
  };
}

function pickReadableRootFromDocument(doc, loc, adapters = SITE_ADAPTERS, structuredSignals = null) {
  const adapter = findSiteAdapterForLocation(loc, adapters);
  if (adapter?.name === 'bilibili' && structuredSignals?.descriptionElement) {
    return {
      element: structuredSignals.descriptionElement,
      selector: structuredSignals.descriptionSelector || '#v_desc',
      adapterName: adapter.name,
      usedFallback: false,
    };
  }
  const selectors = [...(adapter?.selectors || []), ...GENERIC_READABLE_SELECTORS];
  for (const selector of selectors) {
    const element = doc.querySelector(selector);
    if (element && getElementText(element).length > 120) {
      return { element, selector, adapterName: adapter?.name || 'generic', usedFallback: false };
    }
  }
  return { element: doc.body, selector: 'body', adapterName: adapter?.name || 'generic', usedFallback: true };
}

function countPatternMatches(text, patterns) {
  return patterns.reduce((count, pattern) => count + (pattern.test(text) ? 1 : 0), 0);
}

function estimateNoiseScore(text, { adapterName = '', rootSelector = '' } = {}) {
  const normalized = cleanText(text);
  if (!normalized) {
    return 10;
  }
  const lines = normalized.split(/\n+/).map((line) => line.trim()).filter(Boolean);
  const shortLineRatio = lines.length ? lines.filter((line) => line.length <= 18).length / lines.length : 0;
  const timestampCount = (normalized.match(/\b\d{1,2}:\d{2}\b/g) || []).length;
  let score = 0;
  if (shortLineRatio > 0.45) {
    score += 2;
  }
  if (timestampCount >= 3) {
    score += 2;
  }
  if (rootSelector === 'body') {
    score += 2;
  }
  if (adapterName === 'bilibili') {
    score += countPatternMatches(normalized, [
      /相关推荐/,
      /评论/,
      /弹幕/,
      /点赞/,
      /投币/,
      /收藏/,
      /分享/,
      /合集/,
      /播放/
    ]);
  }
  return Math.max(0, Math.min(score, 10));
}

function estimateContentConfidence({ selectionText = '', descriptionText = '', subtitleText = '', pageText = '', contentKind = '', noiseScore = 0 }) {
  let score = 35;
  if (String(selectionText || '').trim().length >= 60) {
    score += 25;
  }
  if (String(descriptionText || '').trim().length >= 80) {
    score += 20;
  }
  if (String(subtitleText || '').trim().length >= 120) {
    score += 30;
  }
  if (String(pageText || '').trim().length >= 200) {
    score += 10;
  }
  if (contentKind === 'video_page' && !String(subtitleText || '').trim() && String(descriptionText || '').trim().length < 80) {
    score -= 15;
  }
  score -= Number(noiseScore || 0) * 6;
  return Math.max(0, Math.min(score, 100));
}

function extractBilibiliExtraMetadata(doc) {
  const parts = [];
  try {
    // Video tags
    const tags = Array.from(doc.querySelectorAll('.tag .tag-link, .video-tag .tag-link, .bili-tag a, .tag-item .tag-txt'))
      .map((el) => getElementText(el))
      .filter((t) => t.length >= 2 && t.length <= 30);
    if (tags.length) {
      parts.push(`视频标签：${tags.join('、')}`);
    }
    // Uploader name
    const upNameEl = doc.querySelector('.up-name, .username, .up-info-container .up-name');
    const upName = getElementText(upNameEl);
    if (upName && upName.length <= 40) {
      parts.push(`UP主：${upName}`);
    }
    // Collection / series title
    const collEl = doc.querySelector('.video-sections-head_title, .head-left .title, .multi-page-header .title');
    const collText = getElementText(collEl);
    if (collText && collText.length >= 4 && collText.length <= 80) {
      parts.push(`合集：${collText}`);
    }
    // Danmaku / interaction counts from page
    const viewCountEl = doc.querySelector('.video-data .view, .view-text');
    const viewCount = getElementText(viewCountEl);
    if (viewCount) {
      parts.push(`播放量：${viewCount}`);
    }
  } catch (_e) { /* ignore */ }
  return parts.join('\n');
}

function buildStructuredPageText({ adapterName = '', rawPageText = '', descriptionText = '', subtitleText = '', extraMetadata = '' }) {
  if (adapterName !== 'bilibili') {
    return rawPageText;
  }
  const parts = [];
  const normalizedDescription = cleanText(descriptionText);
  const normalizedSubtitle = cleanText(subtitleText);
  const normalizedExtra = cleanText(extraMetadata);
  if (normalizedDescription) {
    parts.push(`视频简介：${normalizedDescription}`);
  }
  if (normalizedSubtitle) {
    parts.push(`字幕文本：${normalizedSubtitle}`);
  }
  if (normalizedExtra) {
    parts.push(normalizedExtra);
  }
  if (!parts.length && rawPageText) {
    parts.push(rawPageText);
  }
  return cleanText(parts.join('\n\n'));
}

function collectMediaHintsFromDocument(doc, adapter, structuredSignals = null) {
  const base = {
    hasVideo: Boolean(doc.querySelector('video, [data-player], .video-player, .bpx-player-container')),
    hasDocumentContainer: Boolean(doc.querySelector('embed[type="application/pdf"], iframe[src*="pdf"], .doc-reader, .reader-word-layer')),
    hasImageCluster: doc.querySelectorAll('img').length >= 5,
    rootTag: String(doc.body?.tagName || '').toLowerCase()
  };
  if (!adapter || typeof adapter.mediaHints !== 'function') {
    return base;
  }
  try {
    const merged = { ...base, ...adapter.mediaHints(doc) };
    if (structuredSignals) {
      merged.hasSubtitleContainer = Boolean(structuredSignals.hasSubtitleContainer);
      merged.hasSubtitle = Boolean(structuredSignals.hasSubtitleText);
      merged.subtitleStatus = structuredSignals.subtitleStatus || 'absent';
      if (structuredSignals.descriptionText) {
        merged.hasDescription = true;
      }
    }
    return merged;
  } catch (_error) {
    return base;
  }
}

function detectContentKind(context) {
  const url = String(context.url || '').toLowerCase();
  const title = String(context.title || '').toLowerCase();
  const text = String(context.pageText || '').toLowerCase();
  const hints = context.mediaHints || {};
  const videoHostMatched = /bilibili|douyin|kuaishou|v\.qq\.com|iqiyi|youku|youtube/.test(url);

  if (typeof context.adapterContentKind === 'string' && context.adapterContentKind) {
    return context.adapterContentKind;
  }
  if (hints.hasDocumentContainer || /\.pdf($|\?)/.test(url) || title.includes('pdf') || title.includes('文库')) {
    return 'document_page';
  }
  if (hints.hasVideo || videoHostMatched) {
    return 'video_page';
  }
  if (hints.hasImageCluster && text.length < 400) {
    return 'image_page';
  }
  if ((hints.hasVideo && hints.hasDocumentContainer) || (text.length > 400 && hints.hasImageCluster)) {
    return 'mixed_page';
  }
  return 'article';
}

function collectPageContextFromEnvironment({ doc, loc, selectedText = '', now = null, adapters = SITE_ADAPTERS }) {
  const adapter = findSiteAdapterForLocation(loc, adapters);
  const structuredSignals = collectStructuredSignals(doc, adapter);
  const rootInfo = pickReadableRootFromDocument(doc, loc, adapters, structuredSignals);
  const selectionText = cleanText(selectedText);
  const rawPageText = cleanText(rootInfo.element?.innerText || rootInfo.element?.textContent || doc.body?.innerText || '');
  const descriptionText = cleanText(structuredSignals.descriptionText || '');
  const subtitleText = cleanText(structuredSignals.subtitleText || extractSubtitleText(doc, adapter));
  const extraMetadata = adapter?.name === 'bilibili' ? extractBilibiliExtraMetadata(doc) : '';
  const normalizedPageText = buildStructuredPageText({
    adapterName: adapter?.name || rootInfo.adapterName,
    rawPageText,
    descriptionText,
    subtitleText,
    extraMetadata,
  });
  const mediaHints = collectMediaHintsFromDocument(doc, adapter, structuredSignals);
  const provisionalContentKind = typeof adapter?.contentKind === 'function' ? adapter.contentKind() : '';
  const noiseScore = estimateNoiseScore(normalizedPageText || rawPageText, {
    adapterName: adapter?.name || rootInfo.adapterName,
    rootSelector: rootInfo.selector,
  });
  const contentConfidence = estimateContentConfidence({
    selectionText,
    descriptionText,
    subtitleText,
    pageText: normalizedPageText,
    contentKind: provisionalContentKind,
    noiseScore,
  });

  const context = {
    title: doc.title || '',
    url: loc.href,
    selectionText,
    pageText: normalizedPageText.slice(0, 60000),
    descriptionText: descriptionText.slice(0, 6000),
    rootHint: rootInfo.selector,
    extractionMode: rootInfo.usedFallback ? 'fallback' : 'site_or_generic_selector',
    siteAdapter: rootInfo.adapterName,
    adapterContentKind: provisionalContentKind,
    mediaHints,
    subtitleText: subtitleText.slice(0, 30000),
    subtitleStatus: structuredSignals.subtitleStatus || mediaHints.subtitleStatus || 'absent',
    extractionDiagnostics: {
      rawPageTextLength: rawPageText.length,
      pageTextLength: normalizedPageText.length,
      descriptionLength: descriptionText.length,
      subtitleLength: subtitleText.length,
      noiseScore,
      contentConfidence,
      usedStructuredVideoText: Boolean(adapter?.name === 'bilibili'),
    },
    capturedAt: (now || new Date()).toISOString()
  };
  context.contentKind = detectContentKind(context);
  delete context.adapterContentKind;
  return context;
}

async function collectPageContextFromEnvironmentAsync({ doc, loc, selectedText = '', now = null, adapters = SITE_ADAPTERS }) {
  const adapter = findSiteAdapterForLocation(loc, adapters);
  const structuredSignals = await collectStructuredSignalsAsync(doc, adapter, loc);
  const rootInfo = pickReadableRootFromDocument(doc, loc, adapters, structuredSignals);
  const selectionText = cleanText(selectedText);
  const rawPageText = cleanText(rootInfo.element?.innerText || rootInfo.element?.textContent || doc.body?.innerText || '');
  const descriptionText = cleanText(structuredSignals.descriptionText || '');
  const subtitleText = cleanText(structuredSignals.subtitleText || '');
  const extraMetadata = adapter?.name === 'bilibili' ? extractBilibiliExtraMetadata(doc) : '';
  const normalizedPageText = buildStructuredPageText({
    adapterName: adapter?.name || rootInfo.adapterName,
    rawPageText,
    descriptionText,
    subtitleText,
    extraMetadata,
  });
  const mediaHints = collectMediaHintsFromDocument(doc, adapter, structuredSignals);
  const provisionalContentKind = typeof adapter?.contentKind === 'function' ? adapter.contentKind() : '';
  const noiseScore = estimateNoiseScore(normalizedPageText || rawPageText, {
    adapterName: adapter?.name || rootInfo.adapterName,
    rootSelector: rootInfo.selector,
  });
  const contentConfidence = estimateContentConfidence({
    selectionText,
    descriptionText,
    subtitleText,
    pageText: normalizedPageText,
    contentKind: provisionalContentKind,
    noiseScore,
  });

  const context = {
    title: doc.title || '',
    url: loc.href,
    selectionText,
    pageText: normalizedPageText.slice(0, 60000),
    descriptionText: descriptionText.slice(0, 6000),
    rootHint: rootInfo.selector,
    extractionMode: rootInfo.usedFallback ? 'fallback' : 'site_or_generic_selector',
    siteAdapter: rootInfo.adapterName,
    adapterContentKind: provisionalContentKind,
    mediaHints,
    subtitleText: subtitleText.slice(0, 30000),
    subtitleStatus: structuredSignals.subtitleStatus || mediaHints.subtitleStatus || 'absent',
    extractionDiagnostics: {
      rawPageTextLength: rawPageText.length,
      pageTextLength: normalizedPageText.length,
      descriptionLength: descriptionText.length,
      subtitleLength: subtitleText.length,
      subtitleSourceUrl: structuredSignals.subtitleSourceUrl || '',
      subtitleFetchMethod: structuredSignals.subtitleFetchMethod || '',
      subtitleFetchError: structuredSignals.subtitleFetchError || '',
      subtitleLan: structuredSignals.subtitleLan || '',
      noiseScore,
      contentConfidence,
      usedStructuredVideoText: Boolean(adapter?.name === 'bilibili'),
    },
    capturedAt: (now || new Date()).toISOString()
  };
  context.contentKind = detectContentKind(context);
  delete context.adapterContentKind;
  return context;
}

function collectPageContext() {
  return collectPageContextFromEnvironment({
    doc: document,
    loc: location,
    selectedText: window.getSelection ? window.getSelection().toString() : '',
  });
}

async function collectPageContextAsync() {
  return collectPageContextFromEnvironmentAsync({
    doc: document,
    loc: location,
    selectedText: window.getSelection ? window.getSelection().toString() : '',
  });
}

if (typeof chrome !== 'undefined' && chrome.runtime?.onMessage) {
  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (!message || typeof message !== 'object') {
      return;
    }
    if (message.type === 'COLLECT_PAGE_CONTEXT') {
      collectPageContextAsync()
        .then((context) => sendResponse({ ok: true, context }))
        .catch((error) => sendResponse({ ok: false, error: String(error?.message || error || 'collect_failed') }));
      return true;
    }
    if (message.type === 'COLLECT_KIMI_CONVERSATIONS') {
      extractKimiConversationsAsync(message)
        .then((conversations) => sendResponse({ ok: true, conversations }))
        .catch((error) => sendResponse({ ok: false, error: String(error?.message || error || 'kimi_extract_failed') }));
      return true;
    }
    if (message.type === 'COLLECT_DEEPSEEK_CONVERSATIONS') {
      extractDeepSeekConversationsAsync(message)
        .then((conversations) => sendResponse({ ok: true, conversations }))
        .catch((error) => sendResponse({ ok: false, error: String(error?.message || error || 'deepseek_extract_failed') }));
      return true;
    }
  });
}

// ── Kimi 对话提取 ───────────────────────────────────────────────────

function isKimiPage() {
  return String(location?.hostname || '').includes('kimi.com') || String(location?.href || '').includes('kimi.com');
}

function extractKimiMessageText(element) {
  if (!element) return '';
  const clone = element.cloneNode(true);
  // 移除常见的 UI 噪声
  for (const el of clone.querySelectorAll('button, [role="button"], .copy-btn, .action-bar, .feedback, .toolbar, svg, img')) {
    el.remove();
  }
  return cleanText(clone.innerText || clone.textContent || '');
}

function parseKimiConversationFromCurrentPage() {
  const messages = [];
  // Kimi 网页消息列表通常在一个 role="list" 或特定 class 的容器内
  // 备选选择器按优先级尝试
  const listSelectors = [
    '[role="list"] > [role="listitem"]',
    '[class*="chat-list"] > [class*="chat-item"]',
    '[class*="message-list"] > [class*="message"]',
    '[class*="conversation"] > [class*="message"]',
    'section[class*="chat"] > div',
    'main[class*="chat"] > div',
    'div[class*="ChatContent"] > div',
    'div[class*="chatContent"] > div',
  ];
  let items = [];
  for (const selector of listSelectors) {
    items = Array.from(document.querySelectorAll(selector));
    if (items.length >= 2) break;
  }
  // 兜底：按 data 属性或常见子节点再试
  if (items.length < 2) {
    items = Array.from(document.querySelectorAll('div[class*="message"], div[class*="chat-item"], div[class*="bubble"]'))
      .filter((el) => {
        const text = (el.innerText || '').trim();
        // 至少 8 字符才算有内容
        if (text.length < 8) return false;
        // 避免匹配到极小的按钮或图标
        if (el.children.length === 0 && text.length < 30) return false;
        return true;
      });
  }

  // 去重：按文本内容前 50 字符去重（避免同一消息被多个选择器匹配）
  const seenTexts = new Set();
  for (const item of items) {
    const text = extractKimiMessageText(item);
    if (!text || text.length < 4) continue;
    const dedupeKey = text.slice(0, 50);
    if (seenTexts.has(dedupeKey)) continue;
    seenTexts.add(dedupeKey);
    // 判断角色：用户消息通常有特定 class 或位于右侧
    const className = String(item.className || '');
    const isUser =
      className.includes('user') ||
      className.includes('right') ||
      className.includes('User') ||
      item.getAttribute('data-role') === 'user' ||
      item.querySelector('[class*="user" i], [class*="User"], [data-role="user"]') !== null;
    const role = isUser ? 'user' : 'assistant';
    messages.push({ role, content: text });
  }
  return messages;
}

async function extractKimiConversationsAsync(message) {
  if (!isKimiPage()) {
    throw new Error('当前页面不是 Kimi');
  }
  const title = String(document.title || 'Kimi 对话').trim();
  const messages = parseKimiConversationFromCurrentPage();
  if (!messages.length) {
    throw new Error('未在当前页面找到 Kimi 对话消息');
  }
  const conversation = {
    title,
    timestamp: new Date().toISOString(),
    source: 'kimi',
    messages,
  };
  // 如果 message.openEach === true，表示要批量导出多个对话
  // 当前仅导出当前打开的对话
  return [conversation];
}

// ── DeepSeek 对话提取 ─────────────────────────────────────────────────

function isDeepSeekPage() {
  const host = String(location?.hostname || '');
  return host.includes('deepseek.com') && host.startsWith('chat');
}

function extractDeepSeekMessageText(element) {
  if (!element) return '';
  const clone = element.cloneNode(true);
  // 移除常见的 UI 噪声
  for (const el of clone.querySelectorAll('button, [role="button"], .copy-btn, .action-bar, .feedback, .toolbar, .md-operator, svg, img')) {
    el.remove();
  }
  return cleanText(clone.innerText || clone.textContent || '');
}

function parseDeepSeekConversationFromCurrentPage() {
  const messages = [];
  // DeepSeek 网页消息列表（结构随版本变化，用多套选择器回退）
  const listSelectors = [
    '[data-testid="chat_message"]',
    '[class*="message-list"] > [class*="message"]',
    '[class*="chat-list"] > [class*="chat-item"]',
    '[class*="conversation"] > [class*="turn"]',
    '[class*="thread"] > [class*="message"]',
  ];
  let items = [];
  for (const selector of listSelectors) {
    items = Array.from(document.querySelectorAll(selector));
    if (items.length >= 2) break;
  }
  // 兜底：常见消息节点
  if (items.length < 2) {
    items = Array.from(
      document.querySelectorAll('div[class*="message"], div[class*="chat-item"], div[class*="bubble"], div[class*="turn"], src-msg')
    )
      .filter((el) => {
        const text = (el.innerText || '').trim();
        return text.length >= 8;
      });
  }

  // 去重：按文本前 50 字符
  const seenTexts = new Set();
  for (const item of items) {
    const text = extractDeepSeekMessageText(item);
    if (!text || text.length < 4) continue;
    const dedupeKey = text.slice(0, 50);
    if (seenTexts.has(dedupeKey)) continue;
    seenTexts.add(dedupeKey);
    // 判断角色：用户消息通常带 self/user/right 标识
    const className = String(item.className || '');
    const isUser =
      className.includes('user') ||
      className.includes('self') ||
      className.includes('right') ||
      className.includes('User') ||
      item.getAttribute('data-role') === 'user' ||
      item.hasAttribute('data-user') ||
      item.querySelector('[class*="user" i], [class*="self" i], [class*="right" i], [data-role="user"]') !== null;
    const role = isUser ? 'user' : 'assistant';
    messages.push({ role, content: text });
  }
  return messages;
}

async function extractDeepSeekConversationsAsync(message) {
  if (!isDeepSeekPage()) {
    throw new Error('当前页面不是 DeepSeek 对话页');
  }
  const title = String(document.title || 'DeepSeek 对话').trim();
  const messages = parseDeepSeekConversationFromCurrentPage();
  if (!messages.length) {
    throw new Error('未在当前页面找到 DeepSeek 对话消息');
  }
  const conversation = {
    title,
    timestamp: new Date().toISOString(),
    source: 'deepseek',
    messages,
  };
  return [conversation];
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    SITE_ADAPTERS,
    getElementText,
    pickFirstTextElement,
    extractDescriptionFromBilibili,
    extractSubtitleSnapshotFromBilibili,
    extractSubtitleSnapshotFromBilibiliAsync,
    cleanText,
    extractSubtitleTextFromBilibili,
    extractSubtitleTextFromBilibiliAsync,
    extractSubtitleSnapshot,
    extractSubtitleSnapshotAsync,
    extractSubtitleText,
    normalizeBilibiliSubtitleUrl,
    extractBilibiliSubtitleUrlsFromScripts,
    normalizeSubtitleBodyToText,
    extractBilibiliVideoIds,
    extractBilibiliExtraMetadata,
    requestSubtitleViaBackground,
    collectStructuredSignals,
    collectStructuredSignalsAsync,
    findSiteAdapterForLocation,
    pickReadableRootFromDocument,
    estimateNoiseScore,
    estimateContentConfidence,
    buildStructuredPageText,
    collectMediaHintsFromDocument,
    detectContentKind,
    collectPageContextFromEnvironment,
    collectPageContextFromEnvironmentAsync,
  };
}
