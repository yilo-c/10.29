const assert = require('assert');
const {
  collectPageContextFromEnvironment,
  detectContentKind,
  extractDescriptionFromBilibili,
  extractSubtitleSnapshotFromBilibili,
  extractBilibiliSubtitleUrlsFromScripts,
  normalizeSubtitleBodyToText,
  extractSubtitleTextFromBilibili,
  extractBilibiliVideoIds,
  extractBilibiliExtraMetadata,
  estimateNoiseScore,
  buildStructuredPageText,
} = require('../content-script.js');

function createElement(innerText = '', tagName = 'DIV', options = {}) {
  return {
    innerText,
    textContent: innerText,
    tagName,
    querySelectorAll: options.querySelectorAll || (() => []),
  };
}

function createFakeDocument({ title = '', selectorMap = {}, listCountMap = {}, querySelectorAllMap = {}, bodyText = '' }) {
  const normalizedMap = { ...selectorMap };
  const body = createElement(bodyText, 'BODY');

  function querySelector(selector) {
    if (normalizedMap[selector]) {
      return normalizedMap[selector];
    }
    const parts = String(selector || '').split(',').map((item) => item.trim()).filter(Boolean);
    for (const part of parts) {
      if (normalizedMap[part]) {
        return normalizedMap[part];
      }
    }
    return null;
  }

  function querySelectorAll(selector) {
    if (querySelectorAllMap[selector]) {
      return querySelectorAllMap[selector];
    }
    const count = Number(listCountMap[selector] || 0);
    return Array.from({ length: count }, () => createElement('item'));
  }

  return {
    title,
    body,
    querySelector,
    querySelectorAll,
  };
}

function testBilibiliVideoPage() {
  const doc = createFakeDocument({
    title: '课程视频：任务拆解',
    bodyText: '这是一个视频页面的正文说明。'.repeat(20),
    selectorMap: {
      '#v_desc': createElement('视频简介和说明'.repeat(30)),
      'video': createElement('', 'VIDEO'),
      '.bpx-player-subtitle-panel': createElement('字幕容器'),
    },
    listCountMap: {
      img: 2,
      'pre, code': 0,
    },
  });
  const loc = { href: 'https://www.bilibili.com/video/BV123', hostname: 'www.bilibili.com' };
  const context = collectPageContextFromEnvironment({ doc, loc, selectedText: '这是选中的视频重点。' });

  assert.strictEqual(context.contentKind, 'video_page');
  assert.strictEqual(context.siteAdapter, 'bilibili');
  assert.strictEqual(context.rootHint, '#v_desc');
  assert.strictEqual(context.mediaHints.hasVideo, true);
  assert.strictEqual(context.descriptionText.startsWith('视频简介和说明'), true);
}

function testWenkuDocumentPage() {
  const doc = createFakeDocument({
    title: '某某文库 - 项目手册',
    bodyText: '文库预览正文'.repeat(40),
    selectorMap: {
      '.reader-word-layer': createElement('文档正文内容'.repeat(40)),
    },
    listCountMap: {
      img: 1,
      'pre, code': 0,
    },
  });
  const loc = { href: 'https://wenku.baidu.com/view/abc', hostname: 'wenku.baidu.com' };
  const context = collectPageContextFromEnvironment({ doc, loc });

  assert.strictEqual(context.contentKind, 'document_page');
  assert.strictEqual(context.siteAdapter, 'wenku');
  assert.strictEqual(context.mediaHints.hasDocumentContainer, true);
}

function testGenericArticleFallback() {
  const doc = createFakeDocument({
    title: '一篇普通文章',
    bodyText: '普通文章正文'.repeat(60),
    selectorMap: {
      article: createElement('正文内容'.repeat(50), 'ARTICLE'),
    },
    listCountMap: {
      img: 1,
      'pre, code': 0,
    },
  });
  const loc = { href: 'https://example.com/post', hostname: 'example.com' };
  const context = collectPageContextFromEnvironment({ doc, loc });

  assert.strictEqual(context.contentKind, 'article');
  assert.strictEqual(context.rootHint, 'article');
  assert.strictEqual(context.siteAdapter, 'generic');
}

function testZhihuArticlePage() {
  const doc = createFakeDocument({
    title: '知乎问题：如何做浏览器桥接',
    bodyText: '知乎正文'.repeat(40),
    selectorMap: {
      '.RichText.ztext': createElement('知乎回答正文'.repeat(35)),
      '.QuestionHeader': createElement('问题头部'),
    },
    listCountMap: {
      img: 2,
      'pre, code': 0,
    },
  });
  const loc = { href: 'https://www.zhihu.com/question/123', hostname: 'www.zhihu.com' };
  const context = collectPageContextFromEnvironment({ doc, loc });

  assert.strictEqual(context.siteAdapter, 'zhihu');
  assert.strictEqual(context.rootHint, '.RichText.ztext');
  assert.strictEqual(context.contentKind, 'article');
}

function testWechatArticlePage() {
  const doc = createFakeDocument({
    title: '公众号文章：项目进展',
    bodyText: '公众号正文'.repeat(40),
    selectorMap: {
      '#js_content': createElement('公众号正文内容'.repeat(35)),
      '#js_name': createElement('作者名'),
    },
    listCountMap: {
      img: 3,
      'pre, code': 0,
    },
  });
  const loc = { href: 'https://mp.weixin.qq.com/s/abc', hostname: 'mp.weixin.qq.com' };
  const context = collectPageContextFromEnvironment({ doc, loc });

  assert.strictEqual(context.siteAdapter, 'wechat');
  assert.strictEqual(context.rootHint, '#js_content');
  assert.strictEqual(context.contentKind, 'article');
}

function testCsdnArticlePage() {
  const doc = createFakeDocument({
    title: 'CSDN 教程：内容提取策略',
    bodyText: 'CSDN 正文'.repeat(40),
    selectorMap: {
      '#content_views': createElement('CSDN 文章内容'.repeat(35)),
    },
    listCountMap: {
      img: 2,
      'pre, code': 4,
    },
  });
  const loc = { href: 'https://blog.csdn.net/demo/article/details/1', hostname: 'blog.csdn.net' };
  const context = collectPageContextFromEnvironment({ doc, loc });

  assert.strictEqual(context.siteAdapter, 'csdn');
  assert.strictEqual(context.rootHint, '#content_views');
  assert.strictEqual(context.contentKind, 'article');
  assert.strictEqual(context.mediaHints.hasCodeBlocks, true);
}

function testMixedPageSignals() {
  const context = {
    url: 'https://example.com/mixed',
    title: '图文混合内容',
    pageText: '正文'.repeat(300),
    mediaHints: {
      hasVideo: false,
      hasDocumentContainer: false,
      hasImageCluster: true,
    },
  };
  assert.strictEqual(detectContentKind(context), 'mixed_page');
}

function testBilibiliSubtitleExtraction() {
  const mockSubtitleElement1 = { innerText: '这是第一行字幕' };
  const mockSubtitleElement2 = { innerText: '这是第二行字幕' };
  const mockDocument = {
    querySelector: (selector) => {
      if (selector.includes('bpx-player-subtitle-panel') || selector.includes('subtitle-wrap')) {
        return {
          innerText: '',
          textContent: '',
          querySelectorAll: (innerSelector) => {
            if (innerSelector.includes('bpx-player-subtitle-text') || innerSelector.includes('caption')) {
              return [mockSubtitleElement1, mockSubtitleElement2];
            }
            return [];
          },
        };
      }
      return null;
    },
  };

  const result = extractSubtitleTextFromBilibili(mockDocument);
  assert.strictEqual(result, '这是第一行字幕\n这是第二行字幕');
}

function testContextIncludesSubtitleText() {
  const mockSubtitlePanel = {
    innerText: '',
    textContent: '',
    querySelectorAll: () => [
      { innerText: '这是字幕第一行' },
      { innerText: '这是字幕第二行' },
    ],
  };
  
  const doc = createFakeDocument({
    title: 'Bilibili视频',
    bodyText: '视频简介'.repeat(20),
    selectorMap: {
      '#v_desc': createElement('视频简介'.repeat(30)),
      'video': createElement('', 'VIDEO'),
      '.bpx-player-subtitle-panel': mockSubtitlePanel,
    },
    listCountMap: {
      img: 2,
    },
  });
  
  const loc = { href: 'https://www.bilibili.com/video/BV123', hostname: 'www.bilibili.com' };
  const context = collectPageContextFromEnvironment({ doc, loc });
  
  // Verify subtitleText is included in context
  assert(Object.prototype.hasOwnProperty.call(context, 'subtitleText'));
  assert.strictEqual(typeof context.subtitleText, 'string');
  assert.strictEqual(context.subtitleStatus, 'text_ready');
  assert.strictEqual(context.mediaHints.hasSubtitle, true);
}

function testNoSubtitleReturnsEmptyString() {
  const doc = createFakeDocument({
    title: 'Bilibili视频 - 无字幕',
    bodyText: '视频简介'.repeat(20),
    selectorMap: {
      '#v_desc': createElement('视频简介'.repeat(30)),
      'video': createElement('', 'VIDEO'),
    },
    listCountMap: {
      img: 2,
    },
  });
  
  const loc = { href: 'https://www.bilibili.com/video/BV123', hostname: 'www.bilibili.com' };
  const context = collectPageContextFromEnvironment({ doc, loc });
  
  // When no subtitles exist, subtitleText should be empty string
  assert.strictEqual(context.subtitleText, '');
  assert.strictEqual(context.subtitleStatus, 'absent');
}

function testBilibiliDescriptionExtractionAndStructuredPageText() {
  const doc = createFakeDocument({
    title: 'Bilibili视频',
    bodyText: '播放 2.1万 弹幕 300 相关推荐 评论区'.repeat(20),
    selectorMap: {
      '#v_desc': createElement('这里是视频简介，包含真实主题、步骤和结论。'.repeat(6)),
    },
  });

  const description = extractDescriptionFromBilibili(doc);
  assert.strictEqual(description.selector, '#v_desc');
  assert.strictEqual(description.text.includes('真实主题'), true);

  const pageText = buildStructuredPageText({
    adapterName: 'bilibili',
    rawPageText: '播放 2.1万 弹幕 300 相关推荐 评论区',
    descriptionText: description.text,
    subtitleText: '第一步：拆解任务。第二步：安排执行。',
  });
  assert.strictEqual(pageText.includes('视频简介：'), true);
  assert.strictEqual(pageText.includes('字幕文本：'), true);
}

function testSubtitleSnapshotCanDifferentiateContainerOnly() {
  const doc = {
    querySelector: (selector) => {
      if (selector.includes('bpx-player-subtitle-panel')) {
        return {
          innerText: '',
          textContent: '',
          querySelectorAll: () => [],
        };
      }
      return null;
    },
  };

  const snapshot = extractSubtitleSnapshotFromBilibili(doc);
  assert.strictEqual(snapshot.status, 'container_only');
  assert.strictEqual(snapshot.hasContainer, true);
  assert.strictEqual(snapshot.hasText, false);
}

function testNoiseScoreForNoisyBilibiliText() {
  const noisyText = '播放 3.2万\n弹幕 1200\n点赞 3000\n相关推荐\n评论\n00:30 开场\n01:20 讲解';
  const score = estimateNoiseScore(noisyText, { adapterName: 'bilibili', rootSelector: 'body' });
  assert.strictEqual(score >= 4, true);
}

function testExtractBilibiliSubtitleUrlsFromScripts() {
  const doc = createFakeDocument({
    querySelectorAllMap: {
      script: [
        {
          textContent: 'window.__playinfo__={"data":{"subtitle":{"subtitles":[{"subtitle_url":"//i0.hdslb.com/bfs/subtitle/test.json"}]}}};',
        },
      ],
    },
  });
  const urls = extractBilibiliSubtitleUrlsFromScripts(doc);
  assert.deepStrictEqual(urls, ['https://i0.hdslb.com/bfs/subtitle/test.json']);
}

function testNormalizeSubtitleBodyToText() {
  const text = normalizeSubtitleBodyToText({
    body: [
      { content: '第一句' },
      { content: '第二句' },
    ],
  });
  assert.strictEqual(text, '第一句\n第二句');
}

function testExtractBilibiliVideoIds() {
  // Normal bilibili video URL
  const loc1 = { href: 'https://www.bilibili.com/video/BV1xK4y1P7bX/?spm_id=123' };
  const doc1 = createFakeDocument({
    querySelectorAllMap: {
      script: [
        { textContent: 'window.__INITIAL_STATE__={"cid":456789,"aid":12345};' },
      ],
    },
  });
  const ids1 = extractBilibiliVideoIds(loc1, doc1);
  assert.strictEqual(ids1.bvid, 'BV1xK4y1P7bX');
  assert.strictEqual(ids1.cid, '456789');

  // No cid in scripts
  const doc2 = createFakeDocument({ querySelectorAllMap: { script: [] } });
  const ids2 = extractBilibiliVideoIds(loc1, doc2);
  assert.strictEqual(ids2.bvid, 'BV1xK4y1P7bX');
  assert.strictEqual(ids2.cid, '');

  // Non-bilibili URL
  const loc3 = { href: 'https://example.com/page' };
  const ids3 = extractBilibiliVideoIds(loc3, doc2);
  assert.strictEqual(ids3.bvid, '');
  assert.strictEqual(ids3.cid, '');
}

function testExtractBilibiliVideoIdsFallbackToUrlCid() {
  // When no script tags have cid, should try URL query params
  const loc = { href: 'https://www.bilibili.com/video/BV1xK4y1P7bX/?cid=778899' };
  const doc = createFakeDocument({ querySelectorAllMap: { script: [] } });
  const ids = extractBilibiliVideoIds(loc, doc);
  assert.strictEqual(ids.bvid, 'BV1xK4y1P7bX');
  assert.strictEqual(ids.cid, '778899');
}

function testExtractBilibiliVideoIdsBvidOnlyStillWorks() {
  // Even without cid, bvid should be extracted from URL
  const loc = { href: 'https://www.bilibili.com/video/BV1UQPZzqEN6/?spm_id_from=333' };
  const doc = createFakeDocument({ querySelectorAllMap: { script: [] } });
  const ids = extractBilibiliVideoIds(loc, doc);
  assert.strictEqual(ids.bvid, 'BV1UQPZzqEN6');
  assert.strictEqual(ids.cid, '');
}

function testExtractBilibiliExtraMetadata() {
  const doc = createFakeDocument({
    title: 'Bilibili视频',
    selectorMap: {
      '.up-name': createElement('AI-Jerry'),
    },
    querySelectorAllMap: {
      '.tag .tag-link, .video-tag .tag-link, .bili-tag a, .tag-item .tag-txt': [
        { innerText: 'AI', textContent: 'AI' },
        { innerText: 'Gradio', textContent: 'Gradio' },
      ],
    },
  });
  const metadata = extractBilibiliExtraMetadata(doc);
  assert.strictEqual(metadata.includes('AI'), true);
  assert.strictEqual(metadata.includes('Gradio'), true);
  assert.strictEqual(metadata.includes('AI-Jerry'), true);
}

function testBuildStructuredPageTextWithExtraMetadata() {
  const pageText = buildStructuredPageText({
    adapterName: 'bilibili',
    rawPageText: '原始文本',
    descriptionText: '简介内容',
    subtitleText: '',
    extraMetadata: '视频标签：AI\nUP主：Jerry',
  });
  assert.strictEqual(pageText.includes('视频简介：简介内容'), true);
  assert.strictEqual(pageText.includes('视频标签：AI'), true);
  assert.strictEqual(pageText.includes('UP主：Jerry'), true);
}

function testRegexGFlagSafety() {
  // extractBilibiliSubtitleUrlsFromScripts should be safe to call multiple times
  // (no global state leak from regex)
  const doc = createFakeDocument({
    querySelectorAllMap: {
      script: [
        {
          textContent: 'subtitle_url:"//i0.hdslb.com/bfs/subtitle/a.json"',
        },
      ],
    },
  });
  const urls1 = extractBilibiliSubtitleUrlsFromScripts(doc);
  const urls2 = extractBilibiliSubtitleUrlsFromScripts(doc);
  assert.deepStrictEqual(urls1, ['https://i0.hdslb.com/bfs/subtitle/a.json']);
  assert.deepStrictEqual(urls2, ['https://i0.hdslb.com/bfs/subtitle/a.json']);
}

function run() {
  testBilibiliVideoPage();
  testWenkuDocumentPage();
  testGenericArticleFallback();
  testZhihuArticlePage();
  testWechatArticlePage();
  testCsdnArticlePage();
  testMixedPageSignals();
  testBilibiliSubtitleExtraction();
  testContextIncludesSubtitleText();
  testNoSubtitleReturnsEmptyString();
  testBilibiliDescriptionExtractionAndStructuredPageText();
  testSubtitleSnapshotCanDifferentiateContainerOnly();
  testNoiseScoreForNoisyBilibiliText();
  testExtractBilibiliSubtitleUrlsFromScripts();
  testNormalizeSubtitleBodyToText();
  testExtractBilibiliVideoIds();
  testExtractBilibiliVideoIdsFallbackToUrlCid();
  testExtractBilibiliVideoIdsBvidOnlyStillWorks();
  testExtractBilibiliExtraMetadata();
  testBuildStructuredPageTextWithExtraMetadata();
  testRegexGFlagSafety();
  console.log('content-script sample tests passed');
}

run();
